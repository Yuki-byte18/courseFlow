"""工具层 L3：直接通道。

模型点名调用工具时的完整链路：

    生成工具调用的结构化意图
      → 参数校验（JSON Schema 子集）
      → 权限检查（scopes）
      → 治理检查（预算、超时）
      → 写操作进审批闸门
      → 宿主执行（线程池 + 超时）
      → 结果回灌（截断后给模型看）

每一步失败都返回结构化错误，而不是抛异常 ——
模型需要看到"为什么失败"才能自我修正。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from ..events import EventKind
from .context import ToolContext
from .registry import REGISTRY
from .spec import ToolSpec

# JSON Schema 类型 → Python 类型（只覆盖业务工具实际用到的）
_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
}


@dataclass
class ToolOutcome:
    """一次工具调用的结果。"""

    ok: bool
    name: str
    data: Any = None
    error: str = ""
    preview: str = ""
    card: str | None = None
    duration_ms: int = 0
    channel: str = "direct"
    calls: list[dict[str, Any]] = field(default_factory=list)
    # 是否被安全策略拦下（而不是"执行了但失败"）。
    # 上层据此应换手段，而不是原样重试。
    blocked: bool = False


def validate_arguments(spec: ToolSpec, args: dict[str, Any]) -> str:
    """极简 JSON Schema 校验。返回错误文案，空串表示通过。

    为什么不用 jsonschema 库：我们的工具参数都是扁平的基础类型，
    多一个依赖不划算；而且自己写能精确控制错误文案（要回给模型看）。
    """
    if not isinstance(args, dict):
        return "参数必须是一个 JSON 对象"

    schema = spec.parameters
    props: dict[str, dict[str, Any]] = schema.get("properties") or {}
    required: list[str] = schema.get("required") or []

    for key in required:
        if key not in args or args[key] is None:
            return f"缺少必填参数 {key}"

    for key, value in args.items():
        prop = props.get(key)
        if prop is None:
            # 多余参数不报错：模型多传一个无害字段时，拒绝它反而更贵
            continue
        expected = prop.get("type")
        if not expected:
            continue
        py_types = _TYPE_MAP.get(expected)
        if py_types is None:
            continue
        # bool 是 int 的子类，单独处理避免 true 被当成 1 通过 integer 校验
        if expected == "integer" and isinstance(value, bool):
            return f"参数 {key} 应为整数"
        if not isinstance(value, py_types):
            return f"参数 {key} 类型错误，应为 {expected}"
        enum = prop.get("enum")
        if enum and value not in enum:
            return f"参数 {key} 只能是 {enum} 之一"

    return ""


class DirectChannel:
    """宿主直接执行工具。"""

    async def invoke(self, ctx: ToolContext, name: str, args: dict[str, Any]) -> ToolOutcome:
        started = time.monotonic()

        spec = REGISTRY.get(name)
        if spec is None:
            return self._fail(name, started, f"未知工具 {name}")

        # ---- 权限 ----
        if not spec.allows(ctx.scopes):
            need = "、".join(spec.scopes)
            return self._fail(name, started, f"没有权限调用 {name}（需要 {need}）")

        # ---- 参数 ----
        param_error = validate_arguments(spec, args)
        if param_error:
            return self._fail(name, started, param_error)

        # ---- 治理：预算 ----
        if not ctx.spend(name):
            return self._fail(name, started, "本次运行的工具调用预算已用尽", )

        ctx.emit(
            EventKind.TOOL_CALL,
            agent=ctx.agent,
            tool=name,
            channel="direct",
            args=_safe_json(args),
            side_effect=spec.side_effect,
        )

        # ---- 审批（只对写操作）----
        if spec.side_effect:
            approved = False
            if ctx.approve is not None:
                approved = await ctx.approve(name, args, spec.description)
            if not approved:
                duration = _elapsed_ms(started)
                ctx.emit(
                    EventKind.APPROVAL_RESULT,
                    agent=ctx.agent,
                    tool=name,
                    approved=False,
                )
                ctx.audit(
                    {
                        "run_id": ctx.run_id,
                        "agent": ctx.agent,
                        "tool": name,
                        "channel": "direct",
                        "side_effect": True,
                        "approved": False,
                        "ok": False,
                        "duration_ms": duration,
                    }
                )
                return ToolOutcome(
                    ok=False,
                    name=name,
                    error="用户未批准这次操作，已取消",
                    duration_ms=duration,
                    channel="direct",
                )

        # ---- 执行（线程 + 超时）----
        timeout = spec.timeout_s or 15.0
        try:
            data = await asyncio.wait_for(
                asyncio.to_thread(spec.handler, ctx, args),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            duration = _elapsed_ms(started)
            ctx.audit(
                {
                    "run_id": ctx.run_id,
                    "agent": ctx.agent,
                    "tool": name,
                    "channel": "direct",
                    "side_effect": spec.side_effect,
                    "ok": False,
                    "error": "timeout",
                    "duration_ms": duration,
                }
            )
            return ToolOutcome(
                ok=False,
                name=name,
                error=f"工具执行超时（>{timeout:.0f}s）",
                duration_ms=duration,
                channel="direct",
            )
        except Exception as exc:  # noqa: BLE001 —— 工具内部错误要回给模型，别把服务打挂
            duration = _elapsed_ms(started)
            ctx.audit(
                {
                    "run_id": ctx.run_id,
                    "agent": ctx.agent,
                    "tool": name,
                    "channel": "direct",
                    "side_effect": spec.side_effect,
                    "ok": False,
                    "error": str(exc)[:500],
                    "duration_ms": duration,
                }
            )
            return ToolOutcome(
                ok=False,
                name=name,
                error=f"工具执行失败：{exc}",
                duration_ms=duration,
                channel="direct",
            )

        duration = _elapsed_ms(started)
        card, payload = _split_card(data)

        # 业务失败 vs 工具失败。
        #
        # 写工具（选课/退课）的"失败"不是异常 —— 后端返回 409 学分超限、
        # 时间冲突、名额已满，这些都是**正常业务结果**。但它们必须让工具
        # 报 ok=False，否则模型会看到"工具调用成功"，然后告诉用户"已经帮你选上了"。
        # 这个 bug 真的发生过：payload 里 ok=false，工具层却报 ok=True。
        business_ok = True
        business_error = ""
        if isinstance(payload, dict) and payload.get("ok") is False:
            business_ok = False
            business_error = str(payload.get("message") or "操作未成功")

        preview = _preview(payload, spec.preview_chars)

        ctx.audit(
            {
                "run_id": ctx.run_id,
                "agent": ctx.agent,
                "tool": name,
                "channel": "direct",
                "side_effect": spec.side_effect,
                "ok": business_ok,
                "error": business_error[:300],
                "duration_ms": duration,
            }
        )
        ctx.emit(
            EventKind.TOOL_RESULT,
            agent=ctx.agent,
            tool=name,
            ok=business_ok,
            duration_ms=duration,
            preview=preview[:600],
        )

        # 卡片照发：用户需要看到"为什么没选上"，这个信息不能丢
        if card:
            ctx.emit(EventKind.CARD, agent=ctx.agent, card=card, payload=_safe_json(payload))

        return ToolOutcome(
            ok=business_ok,
            name=name,
            data=payload,
            error=business_error,
            preview=preview,
            card=card,
            duration_ms=duration,
            channel="direct",
        )

    # ------------------------------------------------------------------
    @staticmethod
    def _fail(name: str, started: float, error: str) -> ToolOutcome:
        return ToolOutcome(
            ok=False,
            name=name,
            error=error,
            duration_ms=_elapsed_ms(started),
            channel="direct",
        )


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _split_card(data: Any) -> tuple[str | None, Any]:
    """工具可以返回 {"card": "...", "payload": ...} 让前端渲染卡片。

    约定放在同一个 dict 里而不是两个返回值：
    工具签名保持"一个参数、一个返回值"，实现和测试都更简单。
    """
    if isinstance(data, dict) and "card" in data and "payload" in data:
        return str(data["card"]), data["payload"]
    return None, data


def _preview(data: Any, limit: int) -> str:
    """给模型看的工具结果预览。

    **必须是合法 JSON。** 之前是直接 `text[:limit]`，把 JSON 从中间切断，
    模型拿到半截 JSON 解析不了，只好再查一次 —— 一个"帮我选两门课"的任务里，
    查询助手就这样连调了 12 次工具。

    **先量再砍，不能反过来。** 曾经写成"先从 8 条开始收缩、再看长度"，
    结果 10 门课的清单永远只给前 8 条，模型就老实报告"还有 2 门查不出来"。
    正确顺序：原样能装下就一个字节都不动。
    """
    try:
        text = json.dumps(data, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(data)[:limit]

    if len(text) <= limit:
        return text

    # 真的超了才收缩，而且从宽到窄试：尽量多给几条
    for max_items in (40, 30, 20, 12, 8, 5, 3, 1):
        try:
            candidate = json.dumps(
                _shrink(data, max_items), ensure_ascii=False, default=str
            )
        except (TypeError, ValueError):
            continue
        if len(candidate) <= limit:
            return candidate

    # 兜底：某个字段本身就巨大，退化成"合法 JSON 里放一段摘要"
    try:
        return json.dumps(
            {"truncated": True, "note": "结果过长，已省略细节", "preview": text[: limit // 2]},
            ensure_ascii=False,
        )
    except (TypeError, ValueError):
        return "{}"


def _shrink(data: Any, max_items: int, depth: int = 0) -> Any:
    """按结构收缩，而不是切字符串。深度有限，避免大对象递归太久。"""
    if depth > 4:
        return "…"
    if isinstance(data, dict):
        return {str(key): _shrink(value, max_items, depth + 1) for key, value in data.items()}
    if isinstance(data, list):
        head = [_shrink(item, max_items, depth + 1) for item in data[:max_items]]
        if len(data) > max_items:
            head.append(f"…另有 {len(data) - max_items} 条，如需全部请缩小查询条件")
        return head
    if isinstance(data, str) and len(data) > 300:
        return data[:300] + "…"
    return data


def _safe_json(data: Any) -> Any:
    """把任意结构转成可 JSON 序列化的形式，供事件下发。"""
    try:
        return json.loads(json.dumps(data, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(data)


DIRECT = DirectChannel()
