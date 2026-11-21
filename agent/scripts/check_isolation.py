"""验证 Agent 会话的多租户隔离。

这个脚本会**真的读写数据库**，但用的是两个不存在的假用户 ID，
跑完自己清理干净，不会碰到真实数据。

为什么值得单独有一个自检：
会话隔离是"一旦做错就是别人数据被看/被写"的问题，而且很难靠肉眼发现 ——
B 登录后界面上还留着 A 的对话，看起来只是"没刷新"，实际是串号。

用法：
    cd agent
    python scripts/check_isolation.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.memory import conversation  # noqa: E402

USER_A = 9_910_001
USER_B = 9_910_002


def main() -> int:
    ok, error = db.ping()
    if not ok:
        print(f"数据库连接失败：{error}")
        return 1

    failures: list[str] = []
    created: list[str] = []

    print("=" * 60)
    try:
        # 1. A 建一个会话，并写一条消息（否则第 5 步无可读内容）
        thread_a = conversation.ensure_thread(
            thread_id="", user_id=USER_A, title="A 的私有会话"
        )
        created.append(thread_a)
        conversation.append_message(
            thread_id=thread_a, user_id=USER_A, role="user", content="A 的私有消息"
        )
        print(f"[1] A 创建会话 {thread_a} 并写入 1 条消息")

        if conversation.thread_owner(thread_a) != USER_A:
            failures.append("会话归属写错了")

        # 2. B 拿 A 的 thread_id 调 ensure_thread —— 必须换成一个新会话
        effective = conversation.ensure_thread(
            thread_id=thread_a, user_id=USER_B, title="B 想蹭 A 的会话"
        )
        if effective == thread_a:
            failures.append("B 复用成功：ensure_thread 没校验归属")
        else:
            created.append(effective)
        print(f"[2] B 传 A 的 id，实际落到 {effective}  -> {'OK' if effective != thread_a else 'FAIL'}")

        # 3. B 想读 A 的历史 —— 必须是空
        leaked = conversation.load_history(thread_id=thread_a, user_id=USER_B)
        if leaked:
            failures.append(f"B 读到了 A 的历史：{leaked}")
        print(f"[3] B 读 A 的历史，返回 {len(leaked)} 条  -> {'OK' if not leaked else 'FAIL'}")

        # 4. B 想往 A 的会话写消息 —— 必须被拒绝
        before = db.fetch_one(
            "SELECT COUNT(*) AS n FROM agent_messages WHERE thread_id = %s", (thread_a,)
        )["n"]
        conversation.append_message(
            thread_id=thread_a, user_id=USER_B, role="assistant", content="越权写入"
        )
        after = db.fetch_one(
            "SELECT COUNT(*) AS n FROM agent_messages WHERE thread_id = %s", (thread_a,)
        )["n"]
        if after != before:
            failures.append("B 成功写入了 A 的会话")
        print(f"[4] B 往 A 的会话写消息，条数 {before} -> {after}  -> {'OK' if after == before else 'FAIL'}")

        # 5. A 自己读自己的历史 —— 应该能读到
        own = conversation.load_history(thread_id=thread_a, user_id=USER_A)
        print(f"[5] A 读自己的历史，返回 {len(own)} 条  -> {'OK' if own else 'FAIL（题目：A 应该能读到）'}")
        if not own:
            failures.append("A 读不到自己的历史（隔离过头了）")
    finally:
        # 清理：只删本脚本造的两条会话及其消息
        for thread_id in created:
            try:
                db.execute("DELETE FROM agent_messages WHERE thread_id = %s", (thread_id,))
                db.execute("DELETE FROM agent_threads WHERE thread_id = %s", (thread_id,))
            except Exception as exc:  # noqa: BLE001
                print(f"清理 {thread_id} 失败：{exc}")
        print("-" * 60)
        print(f"已清理 {len(created)} 条测试会话")

    print("=" * 60)
    if failures:
        print("FAIL  隔离有问题：")
        for item in failures:
            print(f"  - {item}")
        return 1

    print("OK  会话隔离正常：跨账号既读不到也写不进")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
