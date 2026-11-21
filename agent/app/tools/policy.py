"""工具层 L2：策略层。

职责：**决定本次运行模型能看到哪些工具**。它自己不做任何路由。

输出两份清单：
  1. 直接暴露清单（direct）
     → 以完整 JSON Schema 进入 `tools` 数组，模型可以点名调用。
     → 写操作永远在这里（沙箱不能写）。
  2. 沙箱引用清单（sandbox）
     → 只把**签名文档**写进 `execute_code` 的 description，
       不写完整 schema（省 token，而且代码通道本来就不需要 schema）。
     → 全部是只读工具（注册表的第二条不变量已经保证了这点）。

为什么要分层而不是"把所有工具都给模型"：
  - 工具一多，模型选错工具的概率显著上升
  - 让只读工具不出现在 tools 数组里，能**减少误调**：
    该算的让模型写代码算，别一个个查
  - 治理上，两条通道的审计维度不同，混在一起就统计不清
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .registry import REGISTRY
from .spec import ToolSpec

# 代码通道的伪工具名。它不是业务工具，而是一条执行通道。
EXECUTE_CODE_TOOL = "execute_code"

# 允许使用代码通道的子 agent。
#
# 只有 compute 需要"多步组合计算"。query 是纯取数，execute 是纯写操作，
# notify 只是拼文案 —— 给它们代码通道只会诱导模型跑偏。
CODE_CHANNEL_AGENTS = frozenset({"compute"})

# 额外借用给某些 agent 的只读工具。
#
# 为什么需要：execute 手里只有写工具，但它得先知道"要选哪个 offering_id"。
# 光靠上一个节点的一段总结文字不一定够（模型总结时可能把 id 丢了）。
# 让它自己查一次，比猜 id 靠谱得多。
# 这里只允许**只读**工具，写工具永远不会被借用。
EXTRA_READ_TOOLS: dict[str, tuple[str, ...]] = {
    "execute": (
        "get_offering",
        "search_offerings",
        "search_offerings_by_codes",
        "my_enrollments",
        "get_current_round",
    ),
    "notify": ("my_enrollments", "my_schedule"),
}


def _code_channel_enabled() -> bool:
    """代码通道是否启用（默认关，见 Settings.enable_code_channel）。"""
    try:
        from ..config import get_settings

        return bool(get_settings().enable_code_channel)
    except Exception:  # noqa: BLE001
        return False


@dataclass
class PolicyDecision:
    """策略层的输出。"""

    direct_tools: list[ToolSpec] = field(default_factory=list)
    sandbox_tools: list[ToolSpec] = field(default_factory=list)
    tools_schema: list[dict[str, Any]] = field(default_factory=list)
    sandbox_notes: str = ""

    def summary(self) -> dict[str, Any]:
        return {
            "direct": [t.name for t in self.direct_tools],
            "sandbox": [t.name for t in self.sandbox_tools],
        }


class ToolPolicy:
    """按 agent 归属 + 权限码筛工具，再按 exposure 切成两条通道。"""

    def decide(self, *, agent: str, scopes: tuple[str, ...] | list[str]) -> PolicyDecision:
        candidates = list(REGISTRY.by_owner_and_scopes(agent, scopes))

        # 借用只读工具（见 EXTRA_READ_TOOLS 的说明）。写工具一律不借。
        for extra_name in EXTRA_READ_TOOLS.get(agent, ()):
            try:
                spec = REGISTRY.require(extra_name)
            except KeyError:
                continue
            if spec.side_effect or spec in candidates:
                continue
            if not set(spec.scopes).issubset(set(scopes)):
                continue
            candidates.append(spec)

        direct = [t for t in candidates if t.visible_directly()]
        sandbox = [t for t in candidates if t.visible_in_sandbox()]

        # 兜底自检：注册表已经拦住了，这里再确认一次。
        # 双通道的安全模型太关键，值得多一道断言。
        assert all(not t.side_effect for t in sandbox), "沙箱清单里混入了写操作"

        decision = PolicyDecision(direct_tools=direct, sandbox_tools=sandbox)
        decision.tools_schema = [t.to_openai_schema() for t in direct]

        # **代码通道只给 compute。**
        #
        # 这是踩出来的结论：查询节点的活是"调几个读接口把数据拿回来"，
        # 它完全不需要写代码。但工具列表里有 execute_code 时，模型会被诱导去写，
        # 写出来又被 AST 拒绝或超预算，于是同一个节点里连撞十几次 ——
        # 预算烧光、步骤耗尽，后面的执行节点就再也没机会跑。
        #
        # 少给一个工具，比事后熔断有效得多。
        # 代码通道只给 compute，而且要在总开关打开时才提供。
        #
        # 关掉它的理由（实测踩出来的）：execute_code 要起子进程，那个调用
        # 在某些 Windows 环境里会把整个事件循环卡死 —— 日志上就是
        # "LLM 调用完成"之后彻底没声音，连看门狗都不响。
        # compute 的 5 个专用工具已经能覆盖同样的分析，不写代码也能完成任务。
        if sandbox and agent in CODE_CHANNEL_AGENTS and _code_channel_enabled():
            decision.tools_schema.append(self._execute_code_schema(sandbox))
            decision.sandbox_notes = "\n".join(t.signature_doc() for t in sandbox)

        return decision

    # ------------------------------------------------------------------
    def _execute_code_schema(self, sandbox_tools: list[ToolSpec]) -> dict[str, Any]:
        """生成 execute_code 的工具声明。

        签名文档写进 description 而不是 system prompt：
        模型的工具选择主要看 description，放这里命中率更高；
        system prompt 里只保留"什么时候该用代码通道"的判断准则。
        """
        notes = "\n".join(t.signature_doc() for t in sandbox_tools)
        description = (
            "在一个受限的 Python 沙箱里执行代码，用于多步处理、统计、筛选、排序、"
            "跨查询编排等计算密集任务。\n"
            "沙箱内不能 import、不能读写文件、不能联网；只能做纯计算。\n"
            "需要业务数据时在代码里调用 call_tool(name, args)，会把结果以 dict/list 返回。\n"
            "只有 print() 的内容会作为执行结果被看到，所以务必 print 最终结论。\n"
            "可用工具：\n" + notes
        )

        return {
            "type": "function",
            "function": {
                "name": EXECUTE_CODE_TOOL,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "要执行的 Python 代码。必须用 print 输出结果。",
                        },
                        "purpose": {
                            "type": "string",
                            "description": "一句话说明这段代码要算什么（便于审计与回放）",
                        },
                    },
                    "required": ["code"],
                },
            },
        }


POLICY = ToolPolicy()
