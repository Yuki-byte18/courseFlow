"""查询 agent 的工具集。

定位：**把业务状态读出来**，不做计算、不做决定。
全部只读，且 exposure=both —— 既可以直接调，也可以写进代码里批量调。
"""

from __future__ import annotations

from typing import Any

from ... import db
from ..context import ToolContext
from ..registry import tool
from . import common

OWNER = "query"


@tool(
    name="get_current_term",
    description="获取当前的学期（学期码、名称、起止日期）。用户说到「这学期」时用它确定学期。",
    parameters={"type": "object", "properties": {}, "required": []},
    side_effect=False,
    exposure="both",
    owner=OWNER,
    card="term",
)
def get_current_term(ctx: ToolContext, args: dict[str, Any]) -> Any:
    term = common.current_term()
    if term is None:
        return {"card": "term", "payload": {"found": False}}
    payload = {
        "found": True,
        "term_id": term["id"],
        "code": term["code"],
        "name": term["name"],
        "starts_on": str(term["starts_on"]),
        "ends_on": str(term["ends_on"]),
    }
    return {"card": "term", "payload": payload}


@tool(
    name="list_terms",
    description="列出所有学期，按时间倒序。用户需要跨学期查询时用。",
    parameters={"type": "object", "properties": {}, "required": []},
    side_effect=False,
    exposure="both",
    owner=OWNER,
)
def list_terms(ctx: ToolContext, args: dict[str, Any]) -> Any:
    rows = db.fetch_all(
        "SELECT id, code, name, starts_on, ends_on, is_current "
        "FROM terms ORDER BY starts_on DESC LIMIT 20"
    )
    return [
        {
            "term_id": r["id"],
            "code": r["code"],
            "name": r["name"],
            "is_current": bool(r["is_current"]),
            "starts_on": str(r["starts_on"]),
            "ends_on": str(r["ends_on"]),
        }
        for r in rows
    ]


@tool(
    name="search_offerings",
    description=(
        "按关键词、院系、是否有余量筛选班次。返回课程名、教师、时间、地点、容量与余量。"
        "关键词会匹配课程名和课程号。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "keyword": {"type": "string", "description": "课程名或课程号关键词，可留空"},
            "department": {"type": "string", "description": "院系名称，可留空"},
            "only_available": {"type": "boolean", "description": "只要还有余量的班次"},
            "term_id": {"type": "integer", "description": "学期 ID，留空用当前学期"},
            "limit": {"type": "integer", "description": "最多返回多少条，默认 20，上限 50"},
        },
        "required": [],
    },
    side_effect=False,
    exposure="both",
    owner=OWNER,
    card="offering_list",
    # 8000：20 个班次约 7.8KB。卡 3000 会让模型只看到 9 个，
    # 然后说"还有 11 个查不出来" —— 用户提个简单查询却被"截断"敷衍。
    preview_chars=8000,
)
def search_offerings(ctx: ToolContext, args: dict[str, Any]) -> Any:
    term = common.resolve_term(args.get("term_id"))
    if term is None:
        return {"card": "offering_list", "payload": {"offerings": [], "reason": "没有可用学期"}}

    limit = min(int(args.get("limit") or 20), 50)
    where = ["o.term_id = %s", "o.status = 1", "c.status = 1"]
    params: list[Any] = [term["id"]]

    keyword = (args.get("keyword") or "").strip()
    if keyword:
        where.append("(c.name LIKE %s OR c.code LIKE %s)")
        like = common.like_pattern(keyword)
        params.extend([like, like])

    department = (args.get("department") or "").strip()
    if department:
        where.append("c.department LIKE %s")
        params.append(common.like_pattern(department))

    if args.get("only_available"):
        where.append("o.taken < o.capacity")

    sql = (
        common.OFFERING_SELECT
        + " WHERE "
        + " AND ".join(where)
        + " ORDER BY c.code, o.section_no LIMIT %s"
    )
    params.append(limit)

    rows = db.fetch_all(sql, tuple(params))
    slot_map = common.slots_map([r["offering_id"] for r in rows])
    offerings = [common.shape_offering(r, slot_map.get(r["offering_id"], [])) for r in rows]

    return {
        "card": "offering_list",
        "payload": {
            "term_id": term["id"],
            "term_name": term["name"],
            "count": len(offerings),
            "offerings": offerings,
        },
    }


@tool(
    name="search_offerings_by_codes",
    description=(
        "按课程号列表一次查出多个班次（含 offering_id、余量、时间）。"
        "用户点名 CS109、CS110 这种课程号时必须用它，不要一门一门 search_offerings。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "codes": {
                "type": "array",
                "description": "课程号列表，例如 [\"CS109\", \"CS110\"]",
            },
            "term_id": {"type": "integer", "description": "学期 ID，留空用当前学期"},
        },
        "required": ["codes"],
    },
    side_effect=False,
    exposure="both",
    owner=OWNER,
    card="offering_list",
    preview_chars=4000,
)
def search_offerings_by_codes(ctx: ToolContext, args: dict[str, Any]) -> Any:
    term = common.resolve_term(args.get("term_id"))
    if term is None:
        return {"card": "offering_list", "payload": {"offerings": [], "reason": "没有可用学期"}}

    codes = _as_code_list(args.get("codes"))
    if not codes:
        return {"card": "offering_list", "payload": {"offerings": [], "reason": "没有课程号"}}

    placeholders = ",".join(["%s"] * len(codes))
    sql = (
        common.OFFERING_SELECT
        + f" WHERE o.term_id = %s AND o.status = 1 AND c.status = 1 AND c.code IN ({placeholders})"
        + " ORDER BY c.code, o.section_no LIMIT 50"
    )
    rows = db.fetch_all(sql, (term["id"], *codes))
    slot_map = common.slots_map([r["offering_id"] for r in rows])
    offerings = [common.shape_offering(r, slot_map.get(r["offering_id"], [])) for r in rows]
    found = {str(item["course_code"]).upper() for item in offerings}
    missing = [code for code in codes if code not in found]

    return {
        "card": "offering_list",
        "payload": {
            "term_id": term["id"],
            "term_name": term["name"],
            "count": len(offerings),
            "codes": codes,
            "missing": missing,
            "offerings": offerings,
        },
    }


def _as_code_list(raw: Any) -> list[str]:
    if isinstance(raw, str):
        parts = [p.strip().upper() for p in raw.replace("，", ",").split(",") if p.strip()]
        return parts
    if isinstance(raw, list):
        return [str(item).strip().upper() for item in raw if str(item).strip()]
    return []


@tool(
    name="get_offering",
    description="查询单个班次的完整信息（含时间段、余量、教师）。需要确认某个班次细节时用。",
    parameters={
        "type": "object",
        "properties": {
            "offering_id": {"type": "integer", "description": "班次 ID"},
        },
        "required": ["offering_id"],
    },
    side_effect=False,
    exposure="both",
    owner=OWNER,
    card="offering_detail",
)
def get_offering(ctx: ToolContext, args: dict[str, Any]) -> Any:
    offering_id = int(args["offering_id"])
    row = db.fetch_one(common.OFFERING_SELECT + " WHERE o.id = %s", (offering_id,))
    if row is None:
        return {"card": "offering_detail", "payload": {"found": False}}

    slot_map = common.slots_map([offering_id])
    shaped = common.shape_offering(row, slot_map.get(offering_id, []))
    shaped["found"] = True
    return {"card": "offering_detail", "payload": shaped}


@tool(
    name="my_enrollments",
    description="查询我某个学期已选的课程列表（含学分、时间、选课时间）。默认只返回在选状态。",
    parameters={
        "type": "object",
        "properties": {
            "term_id": {"type": "integer", "description": "学期 ID，留空用当前学期"},
            "include_dropped": {"type": "boolean", "description": "是否包含已退课的记录"},
        },
        "required": [],
    },
    side_effect=False,
    exposure="both",
    owner=OWNER,
    card="offering_list",
)
def my_enrollments(ctx: ToolContext, args: dict[str, Any]) -> Any:
    term = common.resolve_term(args.get("term_id"))
    if term is None:
        return {"card": "offering_list", "payload": {"offerings": [], "reason": "没有可用学期"}}

    status_filter = "" if args.get("include_dropped") else " AND e.status = 1"
    rows = db.fetch_all(
        """
        SELECT e.offering_id, e.status AS enroll_status,
               e.created_at AS enrolled_at,
               o.section_no, o.capacity, o.taken, o.location, o.status,
               c.code AS course_code, c.name AS course_name, c.credits, c.department,
               u.real_name AS teacher_name
          FROM enrollments e
          JOIN offerings o ON o.id = e.offering_id
          JOIN courses c   ON c.id = o.course_id
          LEFT JOIN users u ON u.id = o.teacher_id
         WHERE e.student_id = %s AND o.term_id = %s
        """
        + status_filter
        + " ORDER BY c.code",
        (ctx.user_id, term["id"]),
    )

    slot_map = common.slots_map([r["offering_id"] for r in rows])
    offerings = []
    for row in rows:
        shaped = common.shape_offering(row, slot_map.get(row["offering_id"], []))
        shaped["enroll_status"] = int(row["enroll_status"])
        shaped["enrolled_at"] = str(row["enrolled_at"])
        offerings.append(shaped)

    return {
        "card": "offering_list",
        "payload": {
            "term_id": term["id"],
            "term_name": term["name"],
            "count": len(offerings),
            "offerings": offerings,
        },
    }


@tool(
    name="my_schedule",
    description="查询我某个学期的周课表，按星期和节次排好。用户问「我的课表」时用它。",
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
    card="schedule",
)
def my_schedule(ctx: ToolContext, args: dict[str, Any]) -> Any:
    term = common.resolve_term(args.get("term_id"))
    if term is None:
        return {"card": "schedule", "payload": {"items": [], "reason": "没有可用学期"}}

    rows = db.fetch_all(
        """
        SELECT e.offering_id, o.section_no, o.location,
               c.code AS course_code, c.name AS course_name, c.credits,
               u.real_name AS teacher_name
          FROM enrollments e
          JOIN offerings o ON o.id = e.offering_id
          JOIN courses c   ON c.id = o.course_id
          LEFT JOIN users u ON u.id = o.teacher_id
         WHERE e.student_id = %s AND e.status = 1 AND o.term_id = %s
        """,
        (ctx.user_id, term["id"]),
    )

    slot_map = common.slots_map([r["offering_id"] for r in rows])
    items: list[dict[str, Any]] = []
    for row in rows:
        base = {
            "offering_id": row["offering_id"],
            "course_code": row["course_code"],
            "course_name": row["course_name"],
            "credits": float(row["credits"]),
            "teacher_name": row["teacher_name"] or "",
            "location": row["location"] or "",
            "section_no": row["section_no"],
        }
        for slot in slot_map.get(row["offering_id"], []):
            items.append({**base, **slot})

    items.sort(key=lambda x: (x["weekday"], x["start_period"]))
    return {
        "card": "schedule",
        "payload": {
            "term_id": term["id"],
            "term_name": term["name"],
            "items": items,
            "total_credits": sum(float(r["credits"]) for r in rows),
        },
    }


@tool(
    name="get_current_round",
    description="查询当前选课轮次：是否开放、什么时候关闭、学分上限、我的已用学分。",
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
    card="round",
)
def get_current_round(ctx: ToolContext, args: dict[str, Any]) -> Any:
    term = common.resolve_term(args.get("term_id"))
    if term is None:
        return {"card": "round", "payload": {"open": False, "reason": "没有可用学期"}}

    row = db.fetch_one(
        """
        SELECT id, name, opens_at, closes_at, max_credits, allow_grade, status,
               (NOW(3) BETWEEN opens_at AND closes_at) AS is_open
          FROM rounds
         WHERE term_id = %s AND status = 1
         ORDER BY opens_at DESC
         LIMIT 1
        """,
        (term["id"],),
    )
    if row is None:
        return {
            "card": "round",
            "payload": {"open": False, "term_id": term["id"], "reason": "该学期没有开放的选课轮次"},
        }

    used = common.used_credits(ctx.user_id, term["id"])
    return {
        "card": "round",
        "payload": {
            "open": bool(row["is_open"]),
            "round_id": row["id"],
            "name": row["name"],
            "opens_at": str(row["opens_at"]),
            "closes_at": str(row["closes_at"]),
            "max_credits": float(row["max_credits"]),
            "used_credits": used,
            "remaining_credits": max(0.0, float(row["max_credits"]) - used),
            "term_id": term["id"],
            "term_name": term["name"],
        },
    }
