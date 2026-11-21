"""通知 agent 的工具集。

业务定位：选课这件事有明确的时间窗，学生最需要的是**提醒**。
所以通知 agent 做的不是"发消息"（我们还没有消息通道），而是：
  1. 生成给用户看的通知卡（选课提醒、课表摘要、待办）
  2. 把提醒存下来（写操作，需要审批）

为什么要单独一个 agent 而不是塞进 query：
"生成一段给人的话"和"查数据"是两种任务，混在一起会让查询 agent 的提示词变得很杂。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ... import db
from ..context import ToolContext
from ..registry import tool

OWNER = "notify"


@tool(
    name="compose_notice",
    description=(
        "把一段信息整理成给用户看的通知卡片（标题 + 正文 + 优先级）。"
        "纯生成，不落库。用户要「提醒我」「帮我记一下」时配合 save_reminder 使用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "通知标题，不超过 40 字"},
            "body": {"type": "string", "description": "通知正文，可以是多行"},
            "level": {
                "type": "string",
                "description": "优先级：info / warning / urgent",
                "enum": ["info", "warning", "urgent"],
            },
        },
        "required": ["title", "body"],
    },
    side_effect=False,
    exposure="both",
    owner=OWNER,
    card="notice",
)
def compose_notice(ctx: ToolContext, args: dict[str, Any]) -> Any:
    level = args.get("level") or "info"
    return {
        "card": "notice",
        "payload": {
            "title": str(args["title"])[:40],
            "body": str(args["body"]),
            "level": level,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        },
    }


@tool(
    name="save_reminder",
    description=(
        "保存一条提醒（写操作，会弹审批）。remind_at 用 YYYY-MM-DD HH:MM 格式。"
        "只在用户明确要求记录时才调用。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "提醒标题，不超过 60 字"},
            "remind_at": {"type": "string", "description": "提醒时间，格式 YYYY-MM-DD HH:MM"},
            "note": {"type": "string", "description": "备注，可留空"},
        },
        "required": ["title", "remind_at"],
    },
    side_effect=True,
    exposure="direct",
    owner=OWNER,
    timeout_s=10.0,
    card="notice",
)
def save_reminder(ctx: ToolContext, args: dict[str, Any]) -> Any:
    title = str(args["title"])[:60]
    remind_raw = str(args["remind_at"]).strip()
    note = str(args.get("note") or "")[:500]

    try:
        remind_at = datetime.strptime(remind_raw, "%Y-%m-%d %H:%M")
    except ValueError:
        return {
            "card": "notice",
            "payload": {
                "title": "时间格式不对",
                "body": f"提醒时间需要写成 YYYY-MM-DD HH:MM，收到的是「{remind_raw}」。",
                "level": "warning",
                "ok": False,
            },
        }

    try:
        db.execute(
            "INSERT INTO agent_reminders (user_id, title, note, remind_at, status) "
            "VALUES (%s, %s, %s, %s, 1)",
            (ctx.user_id, title, note, remind_at),
        )
    except Exception as exc:  # noqa: BLE001
        return {
            "card": "notice",
            "payload": {
                "title": "保存提醒失败",
                "body": f"数据库写入失败：{exc}",
                "level": "warning",
                "ok": False,
            },
        }

    return {
        "card": "notice",
        "payload": {
            "title": "已保存提醒",
            "body": f"{title}\n提醒时间：{remind_at.strftime('%Y-%m-%d %H:%M')}"
            + (f"\n备注：{note}" if note else ""),
            "level": "info",
            "ok": True,
            "remind_at": remind_at.strftime("%Y-%m-%d %H:%M"),
        },
    }


@tool(
    name="list_reminders",
    description="列出我保存过的提醒，按提醒时间排序。",
    parameters={
        "type": "object",
        "properties": {
            "include_done": {"type": "boolean", "description": "是否包含已完成的提醒"},
        },
        "required": [],
    },
    side_effect=False,
    exposure="both",
    owner=OWNER,
    card="reminder_list",
)
def list_reminders(ctx: ToolContext, args: dict[str, Any]) -> Any:
    status_filter = "" if args.get("include_done") else " AND status = 1"
    rows = db.fetch_all(
        "SELECT id, title, note, remind_at, status FROM agent_reminders "
        "WHERE user_id = %s" + status_filter + " ORDER BY remind_at ASC LIMIT 50",
        (ctx.user_id,),
    )
    return {
        "card": "reminder_list",
        "payload": {
            "count": len(rows),
            "items": [
                {
                    "reminder_id": r["id"],
                    "title": r["title"],
                    "note": r["note"] or "",
                    "remind_at": str(r["remind_at"]),
                    "status": int(r["status"]),
                }
                for r in rows
            ],
        },
    }
