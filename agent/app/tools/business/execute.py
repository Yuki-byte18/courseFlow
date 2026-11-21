"""执行 agent 的工具集：真正改变数据。

**这是整个系统里唯一能写数据的两个工具**，因此受三重约束：

1. 注册表层面：side_effect=True 且 exposure=direct
   → 永远进 tools 数组，永远不会出现在沙箱里
2. 工具层：进了审批闸门，用户必须看到"要做什么"并点确认
3. 实现层面：**不直接写 MySQL**，而是带着用户自己的 token 回调 Go 的
   `POST /my/enrollments` / `DELETE /my/enrollments/{id}`

第 3 点是最重要的设计决定：
容量扣减、学分上限、时间冲突、防超卖这些规则只在 Go 的事务里实现了一份。
Agent 再实现一遍一定会漂移（改了 Go 忘了改 Python），
而"两个地方各判一次"的结果往往是超卖。

带用户 token 回调还有个额外好处：**身份不放大**。
Agent 用的是这个学生自己的凭据，所以它不可能替别人选课。
"""

from __future__ import annotations

from typing import Any

import httpx

from ...config import get_settings
from ..context import ToolContext
from ..registry import tool

OWNER = "execute"


def _call_go(
    ctx: ToolContext, method: str, path: str, body: dict[str, Any] | None = None
) -> tuple[bool, Any]:
    """带用户 token 调用 Go 的业务接口。

    返回 (ok, result)。失败时不抛异常 —— 要把后端的错误文案原样交给模型和用户，
    因为那些文案是业务语义的（"名额已满"、"不在选课轮次内"），比我们重新措辞准。
    """
    settings = get_settings()
    url = f"{settings.go_base_url}{path}"
    headers = {
        "Authorization": f"Bearer {ctx.user_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=settings.tool_timeout_s) as client:
            response = client.request(method, url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        return False, {"code": "network_error", "message": f"无法连接后端服务：{exc.__class__.__name__}"}

    if response.status_code == 204:
        return True, {}

    try:
        payload = response.json()
    except ValueError:
        return False, {"code": "bad_response", "message": f"后端返回了无法解析的内容（HTTP {response.status_code}）"}

    if response.status_code < 400:
        return True, payload

    error = (payload or {}).get("error") or {}
    return False, {
        "code": error.get("code") or f"http_{response.status_code}",
        "message": error.get("message") or f"后端返回 HTTP {response.status_code}",
    }


@tool(
    name="enroll_offering",
    description=(
        "为当前用户选一门课（写操作，会弹审批）。"
        "需要先确认班次 ID。后端会校验轮次窗口、容量、学分上限、时间冲突。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "offering_id": {"type": "integer", "description": "要选的班次 ID"},
        },
        "required": ["offering_id"],
    },
    side_effect=True,
    exposure="direct",
    owner=OWNER,
    scopes=("enrollment:create",),
    timeout_s=20.0,
    card="enrollment_result",
)
def enroll_offering(ctx: ToolContext, args: dict[str, Any]) -> Any:
    offering_id = int(args["offering_id"])

    # 先读一下班次信息，用于审批卡片上的人类可读描述（失败不影响主流程）
    detail = _safe_detail(offering_id)

    ok, result = _call_go(
        ctx, "POST", "/api/v1/my/enrollments", {"offering_id": offering_id}
    )

    payload = {
        "action": "enroll",
        "ok": ok,
        "offering_id": offering_id,
        "offering": detail,
        "message": "选课成功" if ok else result.get("message", "选课失败"),
        "remaining": (result or {}).get("remaining"),
    }
    return {"card": "enrollment_result", "payload": payload}


@tool(
    name="enroll_offerings",
    description=(
        "一次为当前用户选多门课（写操作，只弹一次审批）。"
        "传入全部 offering_id。多门课不要拆成多次 enroll_offering。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "offering_ids": {
                "type": "array",
                "description": "要选的班次 ID 列表",
            },
        },
        "required": ["offering_ids"],
    },
    side_effect=True,
    exposure="direct",
    owner=OWNER,
    scopes=("enrollment:create",),
    timeout_s=40.0,
    card="enrollment_batch",
)
def enroll_offerings(ctx: ToolContext, args: dict[str, Any]) -> Any:
    ids = _as_id_list(args.get("offering_ids"))
    if not ids:
        return {
            "card": "enrollment_batch",
            "payload": {"ok": False, "message": "没有有效的班次 ID", "results": []},
        }

    results: list[dict[str, Any]] = []
    for offering_id in ids[:8]:
        detail = _safe_detail(offering_id)
        ok, result = _call_go(
            ctx, "POST", "/api/v1/my/enrollments", {"offering_id": offering_id}
        )
        results.append(
            {
                "ok": ok,
                "offering_id": offering_id,
                "offering": detail,
                "message": "选课成功" if ok else result.get("message", "选课失败"),
                "remaining": (result or {}).get("remaining") if isinstance(result, dict) else None,
            }
        )

    succeeded = sum(1 for item in results if item["ok"])
    payload = {
        "action": "enroll_batch",
        "ok": succeeded == len(results) and succeeded > 0,
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "message": f"选课完成：成功 {succeeded} 门，失败 {len(results) - succeeded} 门",
        "results": results,
    }
    return {"card": "enrollment_batch", "payload": payload}


def _as_id_list(raw: Any) -> list[int]:
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, (int, float)):
        values = [raw]
    elif isinstance(raw, str):
        values = [p.strip() for p in raw.replace("，", ",").split(",") if p.strip()]
    else:
        return []
    ids: list[int] = []
    for item in values:
        try:
            ids.append(int(item))
        except (TypeError, ValueError):
            continue
    return ids


@tool(
    name="drop_offering",
    description=(
        "为当前用户退掉一门课（写操作，会弹审批）。"
        "需要班次 ID。退课后名额立即释放。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "offering_id": {"type": "integer", "description": "要退的班次 ID"},
        },
        "required": ["offering_id"],
    },
    side_effect=True,
    exposure="direct",
    owner=OWNER,
    scopes=("enrollment:drop",),
    timeout_s=20.0,
    card="enrollment_result",
)
def drop_offering(ctx: ToolContext, args: dict[str, Any]) -> Any:
    offering_id = int(args["offering_id"])
    detail = _safe_detail(offering_id)

    ok, result = _call_go(ctx, "DELETE", f"/api/v1/my/enrollments/{offering_id}")

    payload = {
        "action": "drop",
        "ok": ok,
        "offering_id": offering_id,
        "offering": detail,
        "message": "退课成功" if ok else result.get("message", "退课失败"),
    }
    return {"card": "enrollment_result", "payload": payload}


def _safe_detail(offering_id: int) -> dict[str, Any] | None:
    """查班次的展示信息。查不到就算了，不能因为它失败而影响选课。"""
    try:
        from ... import db
        from . import common

        row = db.fetch_one(common.OFFERING_SELECT + " WHERE o.id = %s", (offering_id,))
        if row is None:
            return None
        slot_map = common.slots_map([offering_id])
        shaped = common.shape_offering(row, slot_map.get(offering_id, []))
        return {
            "course_code": shaped["course_code"],
            "course_name": shaped["course_name"],
            "section_no": shaped["section_no"],
            "teacher_name": shaped["teacher_name"],
            "slots_text": shaped["slots_text"],
            "location": shaped["location"],
            "credits": shaped["credits"],
        }
    except Exception:  # noqa: BLE001
        return None
