"""工具层 L3：代码通道（CodeAct）。

链路：

    模型写代码
      → AST 白名单校验（astguard）
      → 子进程沙箱执行（sandbox）
      → 沙箱内 call_tool 桥接回宿主
      → 只有 print() 的输出回灌给模型

**桥接的绝对约束：只能桥接到 side_effect=False 的工具。**
这一条在三个地方各挡一次（注册表不变量、策略层断言、这里的运行时检查），
因为它就是整个双通道安全模型的边界：模型可以写任意代码，但代码永远改不了数据。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..events import EventKind
from . import astguard, sandbox
from .context import ToolContext
from .direct import ToolOutcome, _preview, _safe_json, _split_card
from .registry import REGISTRY


class CodeActChannel:
    """在沙箱里跑模型写的代码。"""

    async def execute(
        self,
        ctx: ToolContext,
        code: str,
        purpose: str = "",
        *,
        timeout_s: float,
        max_output_chars: int,
        max_code_runs: int,
        code_runs_used: int,
    ) -> ToolOutcome:
        started = time.monotonic()

        # ---- 预算 ----
        if code_runs_used >= max_code_runs:
            return self._fail(
                started, f"本次运行的代码执行次数已达上限（{max_code_runs}）"
            )

        # ---- 静态闸门 ----
        report = astguard.validate(code)
        if not report.ok:
            ctx.emit(
                EventKind.CODE_RUN,
                agent=ctx.agent,
                purpose=purpose,
                code=code,
                blocked=True,
                reason=report.reason,
            )
            ctx.audit(
                {
                    "run_id": ctx.run_id,
                    "agent": ctx.agent,
                    "tool": "execute_code",
                    "channel": "code",
                    "ok": False,
                    "error": f"AST 拒绝：{report.reason}",
                    "duration_ms": int((time.monotonic() - started) * 1000),
                }
            )
            outcome = self._fail(started, f"代码被安全策略拒绝：{report.reason}")
            # 标记为"被拦下"而不是"执行失败"：
            # 上层据此应当换手段（改用直连工具），而不是原样重试。
            outcome.blocked = True
            return outcome

        ctx.emit(
            EventKind.CODE_RUN,
            agent=ctx.agent,
            purpose=purpose,
            code=code,
            blocked=False,
            declared_tools=list(report.call_tool_names),
        )

        # ---- 桥接 ----
        bridge_log: list[dict[str, Any]] = []

        def on_tool(name: str, args: dict[str, Any]) -> tuple[bool, Any, str]:
            """沙箱要数据时回调到这里。运行在 sandbox 的工作线程里。"""
            call_started = time.monotonic()

            # 三重校验（第三重）
            if name not in ctx.sandbox_tool_names:
                return False, None, f"沙箱内不能调用 {name}：不在本次可用的只读工具清单里"

            spec = REGISTRY.get(name)
            if spec is None:
                return False, None, f"未知工具 {name}"
            if spec.side_effect:
                # 理论上走不到这里（注册表 + 策略层已经拦住），
                # 但这是安全边界，值得冗余一次
                return False, None, f"{name} 是写操作，沙箱内禁止执行"
            if not spec.visible_in_sandbox():
                return False, None, f"{name} 未开放给代码通道"
            if not spec.allows(ctx.scopes):
                return False, None, f"没有权限调用 {name}"
            if not ctx.spend(name):
                return False, None, "工具调用预算已用尽"

            try:
                raw = spec.handler(ctx, args)
            except Exception as exc:  # noqa: BLE001
                duration = int((time.monotonic() - call_started) * 1000)
                bridge_log.append(
                    {"tool": name, "ok": False, "error": str(exc)[:300], "duration_ms": duration}
                )
                return False, None, f"工具执行失败：{exc}"

            duration = int((time.monotonic() - call_started) * 1000)
            card, payload = _split_card(raw)
            bridge_log.append({"tool": name, "ok": True, "duration_ms": duration})

            ctx.emit(
                EventKind.TOOL_CALL,
                agent=ctx.agent,
                tool=name,
                channel="code",
                args=_safe_json(args),
                side_effect=False,
            )
            ctx.emit(
                EventKind.TOOL_RESULT,
                agent=ctx.agent,
                tool=name,
                ok=True,
                duration_ms=duration,
                preview=_preview(payload, 400),
            )
            ctx.audit(
                {
                    "run_id": ctx.run_id,
                    "agent": ctx.agent,
                    "tool": name,
                    "channel": "code",
                    "side_effect": False,
                    "ok": True,
                    "duration_ms": duration,
                }
            )

            if card:
                ctx.emit(EventKind.CARD, agent=ctx.agent, card=card, payload=_safe_json(payload))

            # 返回给模型代码的结构：**两种取法都能用**。
            #
            # 踩过的坑：`call_tool` 原来只返回拆掉 card 之后的 payload，
            # 模型猜"应该是 result['payload']['offerings']"就 KeyError，
            # 然后它换个键再猜一次 —— 一个节点里连撞十几次，
            # 预算和步骤全烧光，后面的执行节点就没机会跑了。
            #
            # 解法不是教模型猜，而是让两种访问方式都成立：
            #   rows['offerings']          ← 直接用业务字段
            #   rows['payload']['offerings'] ← 也成立
            # 模型无论按哪种直觉写都能跑通，这类无效重试直接消失。
            shaped: Any = payload
            if isinstance(payload, dict):
                shaped = dict(payload)
                shaped.setdefault("card", card)
                shaped.setdefault("payload", payload)

            return True, shaped, ""

        # ---- 执行 ----
        result = await asyncio.to_thread(
            sandbox.run_code,
            code=code,
            on_tool=on_tool,
            timeout_s=timeout_s,
            max_output_chars=max_output_chars,
            allowed_builtins=astguard.ALLOWED_BUILTINS,
        )

        duration = int((time.monotonic() - started) * 1000)

        ctx.emit(
            EventKind.CODE_OUTPUT,
            agent=ctx.agent,
            ok=result.ok,
            output=result.output,
            error=result.error,
            duration_ms=duration,
            tool_calls=bridge_log,
        )
        ctx.audit(
            {
                "run_id": ctx.run_id,
                "agent": ctx.agent,
                "tool": "execute_code",
                "channel": "code",
                "ok": result.ok,
                "error": result.error[:300],
                "duration_ms": duration,
                "bridge_calls": len(bridge_log),
            }
        )

        if not result.ok:
            return ToolOutcome(
                ok=False,
                name="execute_code",
                error=result.error or "代码执行失败",
                preview=result.output,
                duration_ms=duration,
                channel="code",
                calls=bridge_log,
            )

        return ToolOutcome(
            ok=True,
            name="execute_code",
            data=result.output,
            preview=result.output,
            duration_ms=duration,
            channel="code",
            calls=bridge_log,
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _fail(started: float, error: str) -> ToolOutcome:
        return ToolOutcome(
            ok=False,
            name="execute_code",
            error=error,
            duration_ms=int((time.monotonic() - started) * 1000),
            channel="code",
        )


CODEACT = CodeActChannel()
