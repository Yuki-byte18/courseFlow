"""业务工具共用的查询辅助。

刻意集中在一处：四个 agent 都要拼"班次 + 课程 + 教师 + 时间段"，
每个文件各写一遍 SQL 是最容易出现字段不一致的地方。
"""

from __future__ import annotations

from typing import Any

from ... import db

WEEKDAY_NAMES = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


def current_term() -> dict[str, Any] | None:
    return db.fetch_one(
        "SELECT id, code, name, starts_on, ends_on, is_current "
        "FROM terms WHERE is_current = 1 LIMIT 1"
    )


def resolve_term(term_id: int | None) -> dict[str, Any] | None:
    """给了 term_id 就用它，否则退回当前学期。"""
    if term_id:
        return db.fetch_one(
            "SELECT id, code, name, starts_on, ends_on, is_current FROM terms WHERE id = %s",
            (term_id,),
        )
    return current_term()


def slots_map(offering_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """批量取时间段，避免 N+1。"""
    if not offering_ids:
        return {}
    placeholders = ",".join(["%s"] * len(offering_ids))
    rows = db.fetch_all(
        f"SELECT offering_id, weekday, start_period, end_period "
        f"FROM offering_slots WHERE offering_id IN ({placeholders}) "
        f"ORDER BY offering_id, weekday, start_period",
        tuple(offering_ids),
    )
    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row["offering_id"], []).append(
            {
                "weekday": row["weekday"],
                "start_period": row["start_period"],
                "end_period": row["end_period"],
            }
        )
    return result


def like_pattern(text: str) -> str:
    """把用户输入包成 LIKE 模式，并转义 LIKE 通配符。

    为什么必须转义：`%` 和 `_` 在 LIKE 里是通配符。
    用户（或模型）搜一个 `%` 会把整张表都匹配出来 —— 不是注入，
    但会让"搜索关键词"这个动作产生意料之外的巨大结果集。

    这不是 SQL 注入防护（注入靠占位符），而是**语义正确性**：
    用户输入的 `%` 应该被当成一个普通百分号。
    """
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def offering_label(offering_id: int) -> str:
    """审批文案用的人类可读班次名。查不到就退回「班次 N」。"""
    try:
        row = db.fetch_one(OFFERING_SELECT + " WHERE o.id = %s", (offering_id,))
    except Exception:  # noqa: BLE001
        return f"班次 {offering_id}"
    if row is None:
        return f"班次 {offering_id}"
    code = row.get("course_code") or ""
    name = row.get("course_name") or ""
    section = row.get("section_no") or ""
    title = f"{code} {name}".strip()
    if section:
        title = f"{title}（{section}班）"
    return title or f"班次 {offering_id}"


def format_slots(slots: list[dict[str, Any]]) -> str:
    if not slots:
        return "未排时间"
    parts = []
    for slot in slots:
        name = WEEKDAY_NAMES.get(slot["weekday"], f"周{slot['weekday']}")
        parts.append(f"{name}第{slot['start_period']}-{slot['end_period']}节")
    return "、".join(parts)


def shape_offering(row: dict[str, Any], slots: list[dict[str, Any]]) -> dict[str, Any]:
    """把一行 SQL 结果整理成给模型/前端看的结构。

    字段名用业务语义（course_name 而不是 name），
    模型对业务名词的理解比裸列名可靠得多。
    """
    capacity = int(row.get("capacity") or 0)
    taken = int(row.get("taken") or 0)
    return {
        "offering_id": row["offering_id"],
        "course_code": row.get("course_code"),
        "course_name": row.get("course_name"),
        "credits": float(row.get("credits") or 0),
        "department": row.get("department") or "",
        "teacher_name": row.get("teacher_name") or "",
        "section_no": row.get("section_no") or "",
        "location": row.get("location") or "",
        "capacity": capacity,
        "taken": taken,
        "remaining": max(0, capacity - taken),
        "status": int(row.get("status") or 0),
        "slots": slots,
        "slots_text": format_slots(slots),
    }


OFFERING_SELECT = """
    SELECT o.id            AS offering_id,
           o.term_id       AS term_id,
           o.section_no    AS section_no,
           o.capacity      AS capacity,
           o.taken         AS taken,
           o.location      AS location,
           o.status        AS status,
           c.code          AS course_code,
           c.name          AS course_name,
           c.credits       AS credits,
           c.department    AS department,
           u.real_name     AS teacher_name
      FROM offerings o
      JOIN courses c ON c.id = o.course_id
      LEFT JOIN users u ON u.id = o.teacher_id
"""


def my_enrolled_offering_ids(student_id: int, term_id: int) -> list[int]:
    rows = db.fetch_all(
        """
        SELECT e.offering_id
          FROM enrollments e
          JOIN offerings o ON o.id = e.offering_id
         WHERE e.student_id = %s AND e.status = 1 AND o.term_id = %s
        """,
        (student_id, term_id),
    )
    return [int(r["offering_id"]) for r in rows]


def used_credits(student_id: int, term_id: int) -> float:
    row = db.fetch_one(
        """
        SELECT COALESCE(SUM(e.credits), 0) AS used
          FROM enrollments e
          JOIN offerings o ON o.id = e.offering_id
         WHERE e.student_id = %s AND e.status = 1 AND o.term_id = %s
        """,
        (student_id, term_id),
    )
    return float(row["used"]) if row else 0.0
