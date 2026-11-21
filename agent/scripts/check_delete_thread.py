"""验证"删除会话"这个功能。

测三件事：
  1. 删自己的会话 → 204，且**会话和消息都真的没了**
  2. 删别人的会话 → 404，且对方的数据**一条没少**
  3. 消息没有外键级联，所以必须确认删除是原子的（会话没了消息也得没）

用法（Go 要已经启动）：
    cd agent
    python scripts/check_delete_thread.py
环境变量：
    GO_URL  默认 http://127.0.0.1:8080
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app import db  # noqa: E402

GO_URL = os.environ.get("GO_URL", "http://127.0.0.1:8080").rstrip("/")
PASSWORD = "Passw0rd123"
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  {name:<48} OK")
    else:
        print(f"  {name:<48} FAIL  {detail}")
        FAILURES.append(f"{name} {detail}".strip())


def login(username: str) -> tuple[str, int]:
    resp = httpx.post(
        f"{GO_URL}/api/v1/auth/login",
        json={"username": username, "password": PASSWORD},
        timeout=10.0,
    )
    resp.raise_for_status()
    body = resp.json()
    token = body.get("token") or body.get("access_token") or ""
    user = body.get("user") or {}
    if not token:
        raise SystemExit(f"{username} 登录失败：{body}")
    return token, int(user.get("user_id") or 0)


def seed_thread(user_id: int, label: str) -> str:
    """直接造一个会话 + 两条消息，比跑一次 LLM 便宜得多。"""
    thread_id = f"tcheck{uuid.uuid4().hex[:20]}"[:32]
    db.execute(
        "INSERT INTO agent_threads (thread_id, user_id, title, message_count) VALUES (%s, %s, %s, %s)",
        (thread_id, user_id, label, 2),
    )
    db.execute(
        "INSERT INTO agent_messages (thread_id, run_id, role, content) VALUES (%s, NULL, 'user', %s)",
        (thread_id, f"{label} 的提问"),
    )
    db.execute(
        "INSERT INTO agent_messages (thread_id, run_id, role, content) VALUES (%s, NULL, 'assistant', %s)",
        (thread_id, f"{label} 的回答"),
    )
    return thread_id


def counts(thread_id: str) -> tuple[int, int]:
    threads = db.fetch_one(
        "SELECT COUNT(*) AS n FROM agent_threads WHERE thread_id = %s", (thread_id,)
    )["n"]
    messages = db.fetch_one(
        "SELECT COUNT(*) AS n FROM agent_messages WHERE thread_id = %s", (thread_id,)
    )["n"]
    return int(threads), int(messages)


def delete(thread_id: str, token: str) -> int:
    resp = httpx.delete(
        f"{GO_URL}/api/v1/agent/threads/{thread_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10.0,
    )
    return resp.status_code


def main() -> int:
    ok, error = db.ping()
    if not ok:
        print(f"数据库连接失败：{error}")
        return 1

    print("=" * 66)
    try:
        token_a, user_a = login("2024002")
        token_b, user_b = login("2024003")
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  登录失败（Go 起了吗？）：{exc}")
        return 1
    print(f"  A=2024002(id={user_a})  B=2024003(id={user_b})")

    created: list[str] = []
    try:
        # ---- 1. 删自己的 ----
        mine = seed_thread(user_a, "A 的会话")
        created.append(mine)
        t, m = counts(mine)
        check("造出来的会话有 2 条消息", t == 1 and m == 2, f"thread={t} msg={m}")

        status = delete(mine, token_a)
        check("删自己的会话返回 204", status == 204, f"HTTP {status}")

        t, m = counts(mine)
        check("会话已删除", t == 0, f"还剩 {t} 条")
        check("消息也一并删除（无孤儿数据）", m == 0, f"还剩 {m} 条")

        # ---- 2. 重复删 → 404 ----
        status = delete(mine, token_a)
        check("再删一次返回 404", status == 404, f"HTTP {status}")

        # ---- 3. 删别人的 ----
        others = seed_thread(user_b, "B 的会话")
        created.append(others)
        status = delete(others, token_a)
        check("删别人的会话返回 404", status == 404, f"HTTP {status}")

        t, m = counts(others)
        check("别人的会话没被动", t == 1 and m == 2, f"thread={t} msg={m}")

        # ---- 4. 本人删得掉 ----
        status = delete(others, token_b)
        check("B 删自己的会话返回 204", status == 204, f"HTTP {status}")
        t, m = counts(others)
        check("B 的会话和消息都清干净", t == 0 and m == 0, f"thread={t} msg={m}")
    finally:
        # 兜底清理，避免测试数据留在库里
        for thread_id in created:
            db.execute("DELETE FROM agent_messages WHERE thread_id = %s", (thread_id,))
            db.execute("DELETE FROM agent_threads WHERE thread_id = %s", (thread_id,))

    print("=" * 66)
    if FAILURES:
        print(f"FAIL  {len(FAILURES)} 项未通过")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("OK  删除会话正常：本人删得掉、他人删不得、消息不残留")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
