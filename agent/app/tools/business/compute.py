"""计算 agent 的工具集。

定位：**在已有数据上做计算**，不改变任何状态。
全部 exposure=both，因为它天然适合 CodeAct ——
模型往往要拿几个工具的结果在代码里交叉比较、排序、求交集。

刻意把"数据"和"算法"分开：
这里返回的是**结构化数据**（列表、字典），让模型在代码里算，
而不是把结论硬编码在工具里。这样同样的工具能回答
"余量最少的五门"、"周三下午有哪些可选"、"和我的课冲突的有哪些" 等一堆问题。
"""

from __future__ import annotations

from typing import Any

from ... import db
from ..context import ToolContext
from ..registry import tool
from . import common

OWNER = "compute"


def _term_or_none(term_id: Any) -> dict[str, Any] | None:
    return common.resolve_term(term_id if isinstance(term_id, int) else None)


@tool(
    name="compute_credit_summary",
    description=(
        "核算某个学期的学分情况：已选总学分、学分上限、剩余可修、各门课学分明细。"
        "用户问「我还差多少学分」「这学期多少分」时用它。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "term_id": {"type": "integer", "description": "学期 ID，留空用当前学期"},
        },
        "required": [],
    },
    side_effect=False,
    exposure="both",
    owner=OWNER,
    card="credit_summary",
)
def compute_credit_summary(ctx: ToolContext, args: dict[str, Any]) -> Any:
    term = _term_or_none(args.get("term_id"))
    if term is None:
        return {"card": "credit_summary", "payload": {"reason": "没有可用学期"}}

    rows = db.fetch_all(
        """
        SELECT c.code, c.name, e.credits
          FROM enrollments e
          JOIN offerings o ON o.id = e.offering_id
          JOIN courses c   ON c.id = o.course_id
         WHERE e.student_id = %s AND e.status = 1 AND o.term_id = %s
         ORDER BY c.code
        """,
        (ctx.user_id, term["id"]),
    )
    used = sum(float(r["credits"]) for r in rows)

    round_row = db.fetch_one(
        "SELECT max_credits FROM rounds WHERE term_id = %s AND status = 1 "
        "ORDER BY opens_at DESC LIMIT 1",
        (term["id"],),
    )
    max_credits = float(round_row["max_credits"]) if round_row else 0.0

    payload = {
        "term_id": term["id"],
        "term_name": term["name"],
        "course_count": len(rows),
        "used_credits": round(used, 1),
        "max_credits": round(max_credits, 1),
        "remaining_credits": round(max(0.0, max_credits - used), 1) if max_credits else None,
        "courses": [
            {"code": r["code"], "name": r["name"], "credits": float(r["credits"])} for r in rows
        ],
    }
    return {"card": "credit_summary", "payload": payload}


@tool(
    name="compute_conflicts",
    description=(
        "检测给定班次之间是否有上课时间冲突，也可以和我已选的课比。"
        "返回冲突对（哪两门、哪天、哪几节重叠）。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "offering_ids": {
                "type": "array",
                "description": "要检查的班次 ID 列表",
            },
            "against_enrolled": {
                "type": "boolean",
                "description": "是否同时与我已选的课比较，默认 true",
            },
        },
        "required": ["offering_ids"],
    },
    side_effect=False,
    exposure="both",
    owner=OWNER,
    card="conflict_report",
)
def compute_conflicts(ctx: ToolContext, args: dict[str, Any]) -> Any:
    raw_ids = args.get("offering_ids") or []
    if not isinstance(raw_ids, list) or not raw_ids:
        return {"card": "conflict_report", "payload": {"conflicts": [], "reason": "没有给班次 ID"}}

    candidate_ids = [int(x) for x in raw_ids]
    term = common.current_term()
    enrolled_ids = common.my_enrolled_offering_ids(ctx.user_id, term["id"]) if term else []

    if args.get("against_enrolled", True):
        check_ids = list(dict.fromkeys(candidate_ids + enrolled_ids))
    else:
        check_ids = list(dict.fromkeys(candidate_ids))

    slot_map = common.slots_map(check_ids)

    names = {
        int(r["offering_id"]): f"{r['course_name']} {r['section_no']}班"
        for r in db.fetch_all(
            common.OFFERING_SELECT
            + " WHERE o.id IN ("
            + ",".join(["%s"] * len(check_ids))
            + ")",
            tuple(check_ids),
        )
    }

    conflicts: list[dict[str, Any]] = []
    for i, left in enumerate(check_ids):
        for right in check_ids[i + 1 :]:
            for a in slot_map.get(left, []):
                for b in slot_map.get(right, []):
                    if a["weekday"] != b["weekday"]:
                        continue
                    if a["start_period"] <= b["end_period"] and b["start_period"] <= a["end_period"]:
                        conflicts.append(
                            {
                                "weekday": a["weekday"],
                                "weekday_name": common.WEEKDAY_NAMES.get(a["weekday"], ""),
                                "overlap": [
                                    max(a["start_period"], b["start_period"]),
                                    min(a["end_period"], b["end_period"]),
                                ],
                                "left_id": left,
                                "left_name": names.get(left, str(left)),
                                "right_id": right,
                                "right_name": names.get(right, str(right)),
                                "left_enrolled": left in enrolled_ids,
                                "right_enrolled": right in enrolled_ids,
                            }
                        )

    return {
        "card": "conflict_report",
        "payload": {
            "conflicts": conflicts,
            "has_conflict": bool(conflicts),
            "checked": check_ids,
            "enrolled": enrolled_ids,
        },
    }


@tool(
    name="compute_remaining_ranking",
    description=(
        "按剩余名额对某学期的班次排序，返回最紧张和最宽松的班次。"
        "用户问「哪些课快满了」「还有名额的课」时用它。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "term_id": {"type": "integer", "description": "学期 ID，留空用当前学期"},
            "order": {"type": "string", "description": "tightest 或 loosest，默认 tightest"},
            "limit": {"type": "integer", "description": "返回条数，默认 10，上限 30"},
        },
        "required": [],
    },
    side_effect=False,
    exposure="both",
    owner=OWNER,
    card="offering_list",
)
def compute_remaining_ranking(ctx: ToolContext, args: dict[str, Any]) -> Any:
    term = _term_or_none(args.get("term_id"))
    if term is None:
        return {"card": "offering_list", "payload": {"offerings": [], "reason": "没有可用学期"}}

    limit = min(int(args.get("limit") or 10), 30)
    order = "ASC" if (args.get("order") or "tightest") != "loosest" else "DESC"

    rows = db.fetch_all(
        common.OFFERING_SELECT
        + " WHERE o.term_id = %s AND o.status = 1 AND c.status = 1"
        + f" ORDER BY (o.capacity - o.taken) {order}, c.code LIMIT %s",
        (term["id"], limit),
    )
    slot_map = common.slots_map([r["offering_id"] for r in rows])
    offerings = [common.shape_offering(r, slot_map.get(r["offering_id"], [])) for r in rows]

    return {
        "card": "offering_list",
        "payload": {
            "term_id": term["id"],
            "term_name": term["name"],
            "order": order.lower(),
            "count": len(offerings),
            "offerings": offerings,
        },
    }


@tool(
    name="compute_free_slots",
    description=(
        "算出我某学期每周哪些时间段是空的（1-7 天 × 1-12 节）。"
        "想找「周三下午有空」时，用它算出空闲格再和候选班次比对。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "term_id": {"type": "integer", "description": "学期 ID，留空用当前学期"},
        },
        "required": [],
    },
    side_effect=False,
    exposure="both",
    owner=OWNER,
)
def compute_free_slots(ctx: ToolContext, args: dict[str, Any]) -> Any:
    term = _term_or_none(args.get("term_id"))
    if term is None:
        return {"free": [], "reason": "没有可用学期"}

    enrolled = common.my_enrolled_offering_ids(ctx.user_id, term["id"])
    slot_map = common.slots_map(enrolled)

    busy: set[tuple[int, int]] = set()
    for slots in slot_map.values():
        for slot in slots:
            for period in range(slot["start_period"], slot["end_period"] + 1):
                busy.add((slot["weekday"], period))

    free = [
        {"weekday": day, "weekday_name": common.WEEKDAY_NAMES[day], "period": period}
        for day in range(1, 8)
        for period in range(1, 13)
        if (day, period) not in busy
    ]
    return {
        "term_id": term["id"],
        "busy_periods": sorted([{"weekday": d, "period": p} for d, p in busy], key=lambda x: (x["weekday"], x["period"])),
        "free": free,
        "free_count": len(free),
    }


@tool(
    name="compute_candidates",
    description=(
        "筛出符合条件的候选班次：指定星期、节次区间、最少剩余名额、学分范围。"
        "用于「周三下午有空且还有名额的课」这类组合筛选。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "term_id": {"type": "integer", "description": "学期 ID，留空用当前学期"},
            "weekdays": {"type": "array", "description": "限定星期列表，例如 [3,4]"},
            "start_period": {"type": "integer", "description": "最早节次，例如 5"},
            "end_period": {"type": "integer", "description": "最晚节次，例如 8"},
            "min_remaining": {"type": "integer", "description": "最少剩余名额，默认 1"},
            "exclude_enrolled": {"type": "boolean", "description": "排除我已选的，默认 true"},
            "limit": {"type": "integer", "description": "最多返回，默认 20，上限 50"},
        },
        "required": [],
    },
    side_effect=False,
    exposure="both",
    owner=OWNER,
    card="offering_list",
)
def compute_candidates(ctx: ToolContext, args: dict[str, Any]) -> Any:
    term = _term_or_none(args.get("term_id"))
    if term is None:
        return {"card": "offering_list", "payload": {"offerings": [], "reason": "没有可用学期"}}

    limit = min(int(args.get("limit") or 20), 50)
    min_remaining = max(0, int(args.get("min_remaining") or 1))

    rows = db.fetch_all(
        common.OFFERING_SELECT
        + " WHERE o.term_id = %s AND o.status = 1 AND c.status = 1"
        + " AND (o.capacity - o.taken) >= %s ORDER BY c.code",
        (term["id"], min_remaining),
    )
    slot_map = common.slots_map([r["offering_id"] for r in rows])

    weekdays = args.get("weekdays")
    weekday_set = {int(w) for w in weekdays} if isinstance(weekdays, list) and weekdays else None
    start = args.get("start_period")
    end = args.get("end_period")
    enrolled = set(common.my_enrolled_offering_ids(ctx.user_id, term["id"]))

    result: list[dict[str, Any]] = []
    for row in rows:
        offering_id = row["offering_id"]
        if args.get("exclude_enrolled", True) and offering_id in enrolled:
            continue
        slots = slot_map.get(offering_id, [])
        if not slots:
            continue
        if weekday_set is not None and not any(s["weekday"] in weekday_set for s in slots):
            continue
        if isinstance(start, int) and isinstance(end, int):
            if not any(s["start_period"] <= end and start <= s["end_period"] for s in slots):
                continue
        result.append(common.shape_offering(row, slots))
        if len(result) >= limit:
            break

    return {
        "card": "offering_list",
        "payload": {
            "term_id": term["id"],
            "term_name": term["name"],
            "count": len(result),
            "offerings": result,
        },
    }
