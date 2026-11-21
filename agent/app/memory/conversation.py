"""对话持久化。

两次写入：
  1. 运行开始 → 建会话（若不存在）+ 存用户消息
  2. 运行结束 → 存助手消息（含卡片）

为什么要存在 Python 侧而不是 Go：
和 trace 一样的理由 —— 写它的代码就在产生它的进程里。
Go 只负责读（对话历史接口），跨进程传一遍只会多一个失败点。

**失败不能影响主流程**：对话存不下来是观测问题，不是业务问题。
所以所有写操作都吞异常并记到日志里。
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from .. import db

log = logging.getLogger("courseflow.agent.conversation")


def new_thread_id() -> str:
    """生成会话 ID（时间前缀 + 随机后缀，按时间有序）。"""
    ts = int(time.time() * 1000)
    return f"t{ts:013d}{uuid.uuid4().hex[:10]}"[:32]


def thread_owner(thread_id: str) -> int | None:
    """查会话归属。不存在返回 None。

    这是**多租户隔离的关键**：thread_id 由前端生成并随请求传上来，
    如果不校验归属，A 只要拿到 B 的 threadId 就能：
      - 把自己的消息写进 B 的会话
      - 读 B 的历史当上下文
    """
    if not thread_id:
        return None
    try:
        row = db.fetch_one(
            "SELECT user_id FROM agent_threads WHERE thread_id = %s", (thread_id[:32],)
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("查询会话归属失败：%s", exc)
        return None
    if not row:
        return None
    return int(row["user_id"])


def ensure_thread(*, thread_id: str, user_id: int, title: str) -> str:
    """确认（或新建）会话，返回**本次真正使用的 thread_id**。

    如果传进来的 thread_id 属于别人，这里会直接换成一个新会话 id 并返回它。
    调用方必须用返回值覆盖自己的 thread_id —— 否则后续消息仍会写到别人的会话里。
    """
    effective = (thread_id or "").strip()[:32]

    if effective:
        owner = thread_owner(effective)
        if owner is not None and owner != user_id:
            # 不是你的会话：不报错，也不要泄露"它存在"，直接给一个新的
            log.warning("thread %s 不属于 user %s，已改用新会话", effective, user_id)
            effective = ""

    if not effective:
        effective = new_thread_id()

    try:
        db.execute(
            """
            INSERT INTO agent_threads (thread_id, user_id, title, message_count, last_message_at)
            VALUES (%s, %s, %s, 0, NOW(3))
            ON DUPLICATE KEY UPDATE last_message_at = NOW(3)
            """,
            (effective, user_id, title[:200]),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("建会话失败：%s", exc)

    return effective


def append_message(
    *,
    thread_id: str,
    user_id: int,
    role: str,
    content: str,
    run_id: str | None = None,
    cards: list[dict[str, Any]] | None = None,
) -> None:
    """追加一条消息并更新会话统计。

    同一个事务里做两件事，避免出现"消息有了但计数没加"的半状态。

    写入前再校验一次归属：ensure_thread 已经保证过，但这是隔离边界，
    值得冗余一道 —— 一旦串号就是别人的数据被污染。
    """
    if not thread_id or not content.strip():
        return

    owner = thread_owner(thread_id)
    if owner is not None and owner != user_id:
        log.error("拒绝写入他人会话：thread=%s owner=%s user=%s", thread_id, owner, user_id)
        return

    try:
        conn = db.get_conn()
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO agent_messages (thread_id, run_id, role, content, cards_json)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    thread_id[:32],
                    (run_id or "")[:32] or None,
                    role,
                    content,
                    json.dumps(cards, ensure_ascii=False) if cards else None,
                ),
            )
            cur.execute(
                """
                UPDATE agent_threads
                   SET message_count = message_count + 1,
                       last_message_at = NOW(3)
                 WHERE thread_id = %s
                """,
                (thread_id[:32],),
            )
        conn.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("写会话消息失败：%s", exc)


def load_history(*, thread_id: str, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    """读最近若干轮对话，作为新一次运行的上下文。

    只取 user/assistant 的文本：工具调用细节在 trace 里，
    塞进模型上下文既占 token 又没有用。

    **必须校验归属**：这是最隐蔽的泄露面 ——
    如果这里不校验，B 只要拿 A 的 threadId 发一句话，
    模型就会带着 A 的历史回答，等于隔空读到了别人的对话。
    """
    if not thread_id:
        return []

    owner = thread_owner(thread_id)
    if owner is None or owner != user_id:
        return []

    try:
        rows = db.fetch_all(
            """
            SELECT role, content
              FROM agent_messages
             WHERE thread_id = %s
             ORDER BY id DESC
             LIMIT %s
            """,
            (thread_id[:32], limit),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("读会话历史失败：%s", exc)
        return []

    # 查询是倒序，返回给模型要正序
    return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]
