"""四个子 agent。

每个子 agent 都是同一种结构：**一个带工具的循环**，区别只在
  - 归属（决定它能拿到哪些工具）
  - 提示词里的职责描述

刻意不搞四种不同的执行范式：多 agent 的价值在"职责隔离 + 工具集隔离"，
不在"每个 agent 一套奇怪的循环"。统一结构让调试和观测都简单得多。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from ..events import EventKind
from ..llm.client import Completion, ToolCall
from ..llm.prompts import AGENT_SYSTEM_TEMPLATE, SANDBOX_TOOLS_HEADER
from ..tools.codeact import CODEACT
from ..tools.context import ToolContext
from ..tools.direct import DIRECT, ToolOutcome
from ..tools.policy import EXECUTE_CODE_TOOL, POLICY

logger = logging.getLogger("courseflow.agent")


@dataclass
class AgentResult:
    summary: str
    ok: bool = True
    error: str = ""
    tool_calls: int = 0


class SubAgent:
    """子 agent 基类。子类只需要覆写三个类属性。"""

    key: str = "query"
    label: str = "助手"
    mission: str = "完成任务"

    def __init__(self, settings: Any) -> None:
        self._settings = settings

    # ------------------------------------------------------------------
    async def run(
        self,
        runtime: Any,
        *,
        goal: str,
        observations: str,
        history: list[dict[str, Any]] | None = None,
        request: str = "",
    ) -> AgentResult:
        ctx = runtime.tool_context(self.key)
        decision = POLICY.decide(agent=self.key, scopes=runtime.scopes)

        # 本 agent **被允许调用**的工具白名单，来源就是发给模型的 tools 数组。
        #
        # 为什么要单独记一份：模型可能"幻觉"出一个没给它的工具名
        # （比如它从系统提示里见过 execute_code，就在 execute 节点里点它）。
        # 没有这份白名单，_dispatch 会照单执行 —— 那就是绕过策略层。
        offered = {
            str((item.get("function") or {}).get("name")) for item in decision.tools_schema
        }
        ctx.allowed_tools = frozenset(offered)

        if not decision.direct_tools and not decision.sandbox_tools:
            return AgentResult(
                summary="我没有可用的工具来完成这个任务。",
                ok=False,
                error="该 agent 没有可用工具",
            )

        system = AGENT_SYSTEM_TEMPLATE.format(
            agent_label=self.label,
            mission=self.mission,
            today=date.today().isoformat(),
            user_name=runtime.user.real_name or runtime.user.username,
            username=runtime.user.username,
            scopes="、".join(runtime.scopes) or "无",
        )
        if decision.sandbox_tools:
            system += "\n" + SANDBOX_TOOLS_HEADER + decision.sandbox_notes

        user_parts = []
        # 用户请求原文（含澄清补充）。子 agent 必须看得到 ——
        # 否则"澄清过了，执行时又忘了"：goal 是规划器写的，
        # 而澄清得到的具体课程号/动作只在请求原文里。
        if request:
            user_parts.append(f"用户的请求原文：\n{request}")
        user_parts.append(f"本次要完成的目标：{goal}")
        if observations:
            user_parts.append(f"前面节点的观察结果：\n{observations}")
        if history:
            recent = [m for m in history[-4:] if m.get("role") in ("user", "assistant")]
            if recent:
                transcript = "\n".join(
                    f"{'用户' if m['role'] == 'user' else '助手'}："
                    f"{str(m.get('content') or '')[:300]}"
                    for m in recent
                )
                user_parts.append(f"最近的对话：\n{transcript}")

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ]

        tool_call_count = 0
        tools_schema = decision.tools_schema or None

        # 代码通道的连续失败次数（被拦下或执行失败都算）。
        # 模型撞同一堵墙时不会自己停 —— 它会换个写法再试，每次都是一轮 LLM 调用。
        # 连续 2 次就直接把 execute_code 摘掉，逼它走直连工具。
        code_failures = 0

        # 已经调过的 (工具, 参数) 组合。**同样的调用不重复执行第二遍。**
        #
        # 从日志里量出来的问题：查询助手为了"查 CS109/CS110"连调了 12 次工具，
        # 其中大量是完全相同的 search_offerings —— 因为结果 JSON 被从中间截断，
        # 模型解析不了只好再查一遍。预算和步数就这么烧光，
        # 后面的执行节点连工具都不够用，用户看到的就是"卡住然后失败"。
        seen_calls: dict[str, str] = {}
        repeats = 0

        for _step in range(self._settings.max_agent_steps):
            if not runtime.governance.check_wall():
                return AgentResult(
                    summary="运行时间超出预算，任务中止。",
                    ok=False,
                    error="wall_time_exceeded",
                )

            runtime.set_stage(f"{self.label}·第 {_step + 1} 轮思考")
            completion = await runtime.chat(
                messages=messages, tools=tools_schema, temperature=0.2
            )

            if not completion.tool_calls:
                return AgentResult(
                    summary=(completion.content or "").strip() or "已完成。",
                    tool_calls=tool_call_count,
                )

            messages.append(_assistant_tool_message(completion))
            for call in completion.tool_calls:
                tool_call_count += 1

                # 重复调用：不执行，把上次的结果再给它一份，并明确叫停
                key = _call_key(call)
                if key in seen_calls:
                    repeats += 1
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.id or "",
                            "content": (
                                "（这个调用已经执行过，结果与上一次完全相同："
                                f"{seen_calls[key]}）"
                                "不要重复查询同样的条件，请基于已有结果继续。"
                            ),
                        }
                    )
                    if repeats >= 2:
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "你已经开始重复调用同样的工具了。"
                                    "请停止查询，直接用已经拿到的事实给出结论。"
                                ),
                            }
                        )
                    continue

                outcome = await self._dispatch(runtime, ctx, call)
                seen_calls[key] = (outcome.preview or outcome.error or "")[:400]

                if call.name == EXECUTE_CODE_TOOL and (outcome.blocked or not outcome.ok):
                    code_failures += 1
                    if code_failures >= 2:
                        tools_schema = _without_execute_code(tools_schema)
                        messages.append(
                            {
                                "role": "system",
                                "content": (
                                    "代码通道连续失败，已停用，本轮不要再写代码。"
                                    "请改用直连工具完成任务；"
                                    "如果已有专门的统计/计算类工具能直接给出结果，优先用它。"
                                ),
                            }
                        )

                messages.append(_tool_message(call, outcome))

        # 步数用尽：不带工具再问一次，强制收敛成结论
        try:
            final = await runtime.chat(
                messages=messages
                + [
                    {
                        "role": "user",
                        "content": (
                            "工具调用次数已到上限。请基于已有信息直接给出结论，"
                            "不要再调用工具。"
                        ),
                    }
                ],
                tools=None,
                temperature=0.2,
            )
            return AgentResult(
                summary=(final.content or "").strip() or "信息不足，未能得出结论。",
                tool_calls=tool_call_count,
            )
        except Exception as exc:  # noqa: BLE001
            return AgentResult(
                summary="子任务没有在上限内完成。",
                ok=False,
                error=str(exc),
                tool_calls=tool_call_count,
            )

    # ------------------------------------------------------------------
    async def _dispatch(self, runtime: Any, ctx: ToolContext, call: ToolCall) -> ToolOutcome:
        """分派到两条通道之一。"""
        # 白名单先挡一道：模型可能点名一个根本没发给它的工具。
        # 这不是多余检查 —— 它确实发生过：execute 节点里点了 execute_code，
        # 而按策略 execute 不该有代码通道。
        if ctx.allowed_tools and call.name not in ctx.allowed_tools:
            args = call.arguments if isinstance(call.arguments, dict) else {}
            ctx.emit(
                EventKind.TOOL_CALL,
                agent=ctx.agent,
                tool=call.name,
                channel="direct",
                args=args,
                side_effect=False,
            )
            ctx.emit(
                EventKind.TOOL_RESULT,
                agent=ctx.agent,
                tool=call.name,
                ok=False,
                duration_ms=0,
                preview="该工具不在本步骤的可用清单里",
            )
            return ToolOutcome(
                ok=False,
                name=call.name,
                error=f"工具 {call.name} 不在你可以使用的清单里，请只从工具列表中选。",
                blocked=True,
            )

        if call.name == EXECUTE_CODE_TOOL:
            code = str(call.arguments.get("code") or "")
            purpose = str(call.arguments.get("purpose") or "")

            # 进出各打一行日志：代码通道是唯一会"起子进程"的路径。
            # 一旦它卡住，日志上看就是"LLM 调用完成"之后彻底安静 ——
            # 有这两行就能立刻判断是卡在沙箱里，还是卡在别处。
            logger.info(
                "代码通道开始 | agent=%s | code=%d 字符 | timeout=%.0fs",
                ctx.agent,
                len(code),
                self._settings.sandbox_timeout_s,
            )

            # 外层再加一道硬超时。沙箱内部有自己的 deadline，但那是
            # "读得到检查点"才生效的；万一子进程卡在系统调用上，
            # 这里必须能兜住，否则整个运行就停在这一步。
            hard_timeout = self._settings.sandbox_timeout_s + 10.0
            try:
                outcome = await asyncio.wait_for(
                    CODEACT.execute(
                        ctx,
                        code,
                        purpose,
                        timeout_s=self._settings.sandbox_timeout_s,
                        max_output_chars=self._settings.sandbox_max_output_chars,
                        max_code_runs=self._settings.max_code_runs,
                        code_runs_used=runtime.governance.budget.code_runs,
                    ),
                    timeout=hard_timeout,
                )
                logger.info(
                    "代码通道结束 | agent=%s | ok=%s | %sms | %s",
                    ctx.agent,
                    outcome.ok,
                    outcome.duration_ms,
                    (outcome.error or "")[:120],
                )
                return outcome
            except asyncio.TimeoutError:
                logger.warning(
                    "代码通道整体超时 | agent=%s | %.0fs", ctx.agent, hard_timeout
                )
                ctx.emit(
                    EventKind.CODE_RUN,
                    agent=ctx.agent,
                    purpose=purpose,
                    code=code,
                    blocked=False,
                    reason="代码通道整体超时",
                )
                return ToolOutcome(
                    ok=False,
                    name=EXECUTE_CODE_TOOL,
                    error=f"代码执行整体超时（>{hard_timeout:.0f}s），已放弃这一步",
                    channel="code",
                )

        return await DIRECT.invoke(ctx, call.name, dict(call.arguments or {}))


# ---------------------------------------------------------------------------
# 四个子 agent
# ---------------------------------------------------------------------------
class QueryAgent(SubAgent):
    key = "query"
    label = "查询助手"
    mission = (
        "把用户需要的业务事实准确查出来：课程、班次、余量、时间地点、我的课表、"
        "我已经选了什么、当前轮次和学分上限。只查不改，不做复杂计算。"
        "用户给了多个课程号时，用 search_offerings_by_codes 一次查完，不要一门一门搜。"
        "拿到 offering_id、余量、时间后立刻给出结论，不要反复用同一条件再查。"
    )


class ComputeAgent(SubAgent):
    key = "compute"
    label = "计算助手"
    mission = (
        "在查到的数据基础上做分析：统计学分、按余量/时间/学分排序筛选、"
        "检测时间冲突、找出空余时段、给出可选的课程组合。"
        "需要多步处理时优先写代码（execute_code）而不是反复查。"
    )


class ExecuteAgent(SubAgent):
    key = "execute"
    label = "执行助手"
    mission = (
        "根据用户明确的指令执行写操作：选课、退课。"
        "只执行用户已经说清楚的动作，不要自己替用户决定选哪门课。"
        "观察结果或目标里已经有 offering_id 时，直接调用 enroll_offerings / drop_offering，"
        "不要再查一遍。"
        "一次要选多门课时，必须用 enroll_offerings 传入全部 offering_id，"
        "禁止一门一门调 enroll_offering（每调一次用户就要确认一次）。"
        "还不知道班次 ID 时，才用 search_offerings_by_codes 或 get_offering 确认。"
    )


class NotifyAgent(SubAgent):
    key = "notify"
    label = "通知助手"
    mission = (
        "生成给用户看的提醒与通知卡片；用户明确要求记录提醒时，保存提醒。"
        "文案要短、具体、可执行（写清时间与事项）。"
    )


AGENTS: dict[str, SubAgent] = {}


def build_agents(settings: Any) -> dict[str, SubAgent]:
    global AGENTS
    AGENTS = {
        "query": QueryAgent(settings),
        "compute": ComputeAgent(settings),
        "execute": ExecuteAgent(settings),
        "notify": NotifyAgent(settings),
    }
    return AGENTS


def get_agent(key: str) -> SubAgent | None:
    return AGENTS.get(key)


# ---------------------------------------------------------------------------
# 消息拼装
# ---------------------------------------------------------------------------
def _assistant_tool_message(completion: Completion) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": completion.content or None,
        "tool_calls": [
            {
                "id": call.id or f"call_{index}",
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments or {}, ensure_ascii=False),
                },
            }
            for index, call in enumerate(completion.tool_calls)
        ],
    }


def _tool_message(call: ToolCall, outcome: ToolOutcome) -> dict[str, Any]:
    if outcome.ok:
        content = outcome.preview or json.dumps(outcome.data, ensure_ascii=False, default=str)
    elif outcome.blocked:
        # 被安全策略拦下：告诉模型"换手段"，而不是让它以为"再试一次就好"
        content = f"该做法被安全策略拒绝，请改用其他工具：{outcome.error}"
    else:
        content = f"调用失败：{outcome.error}"
    # 工具返回也可能很长，截断保护上下文。
    # 用 8000 而不是 6000：preview 已经按 preview_chars（默认 6000）收敛过一次，
    # 这里再卡到同一个数，会把刚放行的内容又切掉一截。
    return {
        "role": "tool",
        "tool_call_id": call.id or "",
        "content": content[:8000],
    }


def _call_key(call: ToolCall) -> str:
    """把一次工具调用压成一个可比较的键，用来识别重复调用。"""
    try:
        args = json.dumps(call.arguments or {}, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        args = str(call.arguments)
    return f"{call.name}|{args}"


def _without_execute_code(
    tools_schema: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """从 tools 数组里去掉 execute_code。返回 None 表示没有工具可用了。"""
    if not tools_schema:
        return None
    remaining = [
        item
        for item in tools_schema
        if (item.get("function") or {}).get("name") != EXECUTE_CODE_TOOL
    ]
    return remaining or None
