"""为"帮我选 CS109 + CS110"准备前置条件。

60 个种子学生每人已经 10 门课，CS109/CS110 都在里面 —— 所以直接测"帮我选"
永远只会得到"你已经选过了"。这个脚本把某个学生的这两门退掉，
让 agent 真的有活可干；跑完 agent 会自己把它们选回来（等于自愈）。

用法：
    cd agent
    python scripts/prep_select_case.py 2024002          # 退掉这两门
    python scripts/prep_select_case.py 2024002 --show   # 只查看当前状态
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402

CODES = ("CS109", "CS110")


def show(user_id: int, username: str) -> int:
    rows = db.fetch_all(
        """
        SELECT c.code AS code, o.id AS offering_id, c.credits AS credits
          FROM enrollments e
          JOIN offerings o ON o.id = e.offering_id
          JOIN courses c ON c.id = o.course_id
         WHERE e.student_id = %s AND e.status = 1 AND c.code IN ('CS109', 'CS110')
        """,
        (user_id,),
    )
    used = db.fetch_one(
        "SELECT COALESCE(SUM(credits),0) AS used FROM enrollments WHERE student_id = %s AND status = 1",
        (user_id,),
    )
    print(f"  {username}: 已用 {float(used['used']):.1f} 学分")
    for row in rows:
        print(f"    已选 {row['code']} (offering_id={row['offering_id']}, {float(row['credits'])} 学分)")
    if not rows:
        print("    这两门都没选 —— 正好可以测选课")
    return len(rows)


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：python scripts/prep_select_case.py <username> [--show]")
        return 1
    username = sys.argv[1]
    show_only = "--show" in sys.argv

    row = db.fetch_one("SELECT id FROM users WHERE username = %s", (username,))
    if not row:
        print(f"找不到用户 {username}")
        return 1
    user_id = int(row["id"])

    print("=" * 60)
    print("退课之前：")
    count = show(user_id, username)
    if show_only or count == 0:
        print("=" * 60)
        return 0

    offerings = db.fetch_all(
        """
        SELECT e.offering_id AS offering_id, c.code AS code
          FROM enrollments e
          JOIN offerings o ON o.id = e.offering_id
          JOIN courses c ON c.id = o.course_id
         WHERE e.student_id = %s AND e.status = 1 AND c.code IN ('CS109', 'CS110')
        """,
        (user_id,),
    )

    for item in offerings:
        db.execute(
            "UPDATE enrollments SET status = 2, dropped_at = NOW() "
            "WHERE student_id = %s AND offering_id = %s AND status = 1",
            (user_id, item["offering_id"]),
        )
        db.execute(
            """
            UPDATE offerings o
               SET o.taken = (SELECT COUNT(*) FROM enrollments e
                               WHERE e.offering_id = o.id AND e.status = 1)
             WHERE o.id = %s
            """,
            (item["offering_id"],),
        )
        print(f"  已退 {item['code']} (offering_id={item['offering_id']})，taken 已重算")

    print("-" * 60)
    print("退课之后：")
    show(user_id, username)
    print("=" * 60)
    print(f"现在可以测：PROBE_USER={username} PROBE_TASK='帮我选 CS109 和 CS110'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
