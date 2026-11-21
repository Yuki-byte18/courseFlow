"""找一个适合测试"选 CS109 + CS110"的账号。

条件：
  1. 没有选过 CS109，也没有选过 CS110
  2. 学分余量够两门课
  3. 没有时间冲突（简单起见只查学分，冲突让 agent 自己发现）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402


def main() -> int:
    round_row = db.fetch_one(
        "SELECT max_credits FROM rounds WHERE status = 1 ORDER BY id DESC LIMIT 1"
    )
    max_credits = float(round_row["max_credits"]) if round_row else 30.0

    targets = db.fetch_all(
        """
        SELECT c.id AS course_id, c.code AS code, c.credits AS credits
          FROM courses c WHERE c.code IN ('CS109', 'CS110')
        """
    )
    print("=" * 64)
    print(f"学分上限 {max_credits}；目标课程 {[(t['code'], float(t['credits'])) for t in targets]}")
    need = sum(float(t["credits"]) for t in targets)
    course_ids = [int(t["course_id"]) for t in targets]
    placeholders = ",".join(["%s"] * len(course_ids))
    print(f"两门课共 {need} 学分")

    rows = db.fetch_all(
        f"""
        SELECT u.id AS id, u.username AS username, u.real_name AS real_name,
               COALESCE(SUM(CASE WHEN e.status = 1 THEN e.credits ELSE 0 END), 0) AS used,
               SUM(CASE WHEN e.status = 1 AND o.course_id IN ({placeholders}) THEN 1 ELSE 0 END) AS has_target
          FROM users u
          JOIN user_roles ur ON ur.user_id = u.id
          JOIN roles r ON r.id = ur.role_id AND r.code = 'student'
          LEFT JOIN enrollments e ON e.student_id = u.id
          LEFT JOIN offerings o ON o.id = e.offering_id
         WHERE u.username LIKE '2024%%'   -- 只挑 seedcampus 造的那些，密码统一是 Passw0rd123
         GROUP BY u.id, u.username, u.real_name
         HAVING has_target = 0 AND used + %s <= %s
         ORDER BY used DESC
         LIMIT 8
        """,
        (*course_ids, need, max_credits),
    )

    print("-" * 64)
    if not rows:
        print("没有合适的账号：所有人都已经选过这两门，或者学分余量不够")
        return 1
    print(f"可用账号 {len(rows)} 个：")
    for row in rows:
        print(
            f"  {row['username']}  {row['real_name']}  "
            f"已用 {float(row['used']):.1f}/{max_credits}  余 {max_credits - float(row['used']):.1f}"
        )
    print("=" * 64)
    print(f"建议用 PROBE_USER={rows[0]['username']} 跑 probe_sse.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
