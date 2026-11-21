"""工具层 L1：统一工具注册表。

注册表**不关心模型怎么调用工具**，只负责描述两件事：
  1. 能力：名字、描述、参数 schema
  2. 治理属性：`side_effect` 与 `exposure`

设计约束（写在 ToolSpec.__post_init__ 里，注册时就报错，而不是运行时才发现）：

  不变量 A：side_effect=True 的工具，exposure 必须是 direct 或 both。
     原因：沙箱内的 call_tool 只桥接只读工具（见 tools/codeact.py）。
     一个写工具如果标成 sandboxed，等于声明了一个永远调不到的能力。

  不变量 B：exposure 是 sandboxed 或 both 的工具，side_effect 必须是 False。
     同一条规则的另一面。

这两条不变量是"双通道安全模型"的地基：
  - 写操作永远走宿主直连 + 审批
  - 沙箱里永远只能读
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

# 暴露通道
#   direct     只进 tools 数组（模型直接点名调用）
#   sandboxed  只把签名写进提示词，模型写代码时通过 call_tool 调
#   both       两者都有
Exposure = Literal["direct", "sandboxed", "both"]

VALID_EXPOSURES: frozenset[str] = frozenset({"direct", "sandboxed", "both"})

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


class ToolSpecError(ValueError):
    """工具定义不合法。启动时就抛，避免带着错误定义上线。"""


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的描述。handler 是同步函数，由宿主在治理层内执行。"""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[..., Any]

    # ---- 治理属性 ----
    side_effect: bool
    exposure: Exposure

    # 需要的业务权限码。空表示登录即可。
    scopes: tuple[str, ...] = ()

    # 归属的 sub-agent。策略层据此决定"这次能给模型看哪些工具"。
    owner: str = "*"

    # 单次执行超时（秒）。None 用全局默认。
    timeout_s: float | None = None

    # 工具返回里如果带 card，这里是卡片类型，前端据此选组件渲染
    card: str | None = None

    # 结果给模型看时截断到多少字符（防止把大 JSON 塞进上下文）
    #
    # 默认 6000 而不是 2000：一个"我选了哪些课"的清单，9 门课就要 3.5KB。
    # 卡在 2000 会导致每次只看得到 6 门，模型就会老实报告
    # "还有 N 门查不出来" —— 用户提个简单问题却被"截断"敷衍，体验很差。
    # 真正过大的列表（比如全校 31 个班次）仍然会被收缩，并在结果里
    # 标注"另有 N 条，请缩小查询条件"，那是合理的。
    preview_chars: int = 6000

    tags: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not _NAME_RE.match(self.name):
            raise ToolSpecError(
                f"工具名 {self.name!r} 不合法：要求小写字母开头、只含小写字母数字下划线、长度 3-64"
            )
        if self.exposure not in VALID_EXPOSURES:
            raise ToolSpecError(f"工具 {self.name} 的 exposure={self.exposure!r} 不合法")

        # 不变量 A
        if self.side_effect and self.exposure == "sandboxed":
            raise ToolSpecError(
                f"工具 {self.name} 是写操作（side_effect=True），"
                f"不能只暴露在沙箱里：沙箱的 call_tool 只桥接只读工具"
            )
        # 不变量 B
        if self.exposure in ("sandboxed", "both") and self.side_effect:
            raise ToolSpecError(
                f"工具 {self.name} 暴露给沙箱（exposure={self.exposure}），"
                f"但它是写操作：沙箱内绝不能执行写操作"
            )

        if not isinstance(self.parameters, dict) or self.parameters.get("type") != "object":
            raise ToolSpecError(f"工具 {self.name} 的 parameters 必须是 object 类型的 JSON Schema")

    # ------------------------------------------------------------------
    # 三种投影
    # ------------------------------------------------------------------
    def to_openai_schema(self) -> dict[str, Any]:
        """完整 schema —— 进 tools 数组，供 function calling 使用。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def signature_doc(self) -> str:
        """精简签名 —— 只写进提示词，供模型在代码里 call_tool 时参考。

        不写完整 JSON Schema：提示词里塞 schema 很占 token，
        而模型写代码时只需要知道"叫什么、要哪些参数、返回什么"。
        """
        props = self.parameters.get("properties") or {}
        required = set(self.parameters.get("required") or [])

        parts: list[str] = []
        for key, spec in props.items():
            type_name = spec.get("type", "any")
            piece = f"{key}: {type_name}"
            if key in required:
                piece += "（必填）"
            parts.append(piece)

        args_doc = ", ".join(parts) if parts else "无参数"
        return f"- {self.name}({args_doc}) → {self.description}"

    def visible_directly(self) -> bool:
        return self.exposure in ("direct", "both")

    def visible_in_sandbox(self) -> bool:
        return self.exposure in ("sandboxed", "both")

    def allows(self, scopes: list[str] | set[str]) -> bool:
        """权限判断。工具不需要权限时永远通过。"""
        if not self.scopes:
            return True
        have = set(scopes)
        return all(scope in have for scope in self.scopes)
