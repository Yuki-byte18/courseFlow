"""快速看一眼业务库现状，用于造数据前后确认前提条件。

用法：
    cd agent

    python scripts/inspect_db.py            # 总览
    python scripts/inspect_db.py --schema   # 表结构（写数据脚本时核对列名）
    python scripts/inspect_db.py --verify   # 数据自洽性检查

为什么要有这个脚本：
Agent 的工具全部依赖真实业务数据。数据没造好时，现象是"Agent 好像很笨"，
而不是报错 —— 先确认基数与前提，能省掉一大半排查时间。

注意：输出只用 ASCII 标记（OK / FAIL），不用 emoji。
Windows 控制台默认 GBK，emoji 会直接抛 UnicodeEncodeError。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402


def main() -> int:
    ok, error = db.ping()
    if not ok:
        print(f"数据库连接失败：{error}")
        print("先跑 python scripts/sync_env.py 同步 .env")
        return 1

    if "--schema" in sys.argv:
        return dump_schema()
    if "--verify" in sys.argv:
        return verify()
    if "--student" in sys.argv:
        idx = sys.argv.index("--student")
        username = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "2024001"
        return student_view(username)
    return overview()


# ---------------------------------------------------------------------------
def student_view(username: str) -> int:
    """打印某个学生「Agent 会看到的样子」：已选课程、学分、课表。

    调试时很有用：Agent 答错了，先确认它看到的数据到底是什么。
    """
    user = db.fetch_one("SELECT id, username, real_name, grade FROM users WHERE username = %s", (username,))
    if not user:
        print(f"FAIL 找不到用户 {username}")
        return 1

    term = db.fetch_one("SELECT id, name FROM terms WHERE is_current = 1 LIMIT 1")
    if not term:
        print("FAIL 没有当前学期")
        return 1

    print("=" * 60)
    print(f"{user['real_name']}（{user['username']}，{user['grade']} 年级）学期 {term['name']}")

    rows = db.fetch_all(
        """
        SELECT c.code, c.name, c.credits, o.section_no, o.location,
               GROUP_CONCAT(CONCAT(s.weekday, '-', s.start_period, '-', s.end_period)
                            ORDER BY s.weekday, s.start_period) AS slots
          FROM enrollments e
          JOIN offerings o      ON o.id = e.offering_id
          JOIN courses c        ON c.id = o.course_id
          LEFT JOIN offering_slots s ON s.offering_id = o.id
         WHERE e.student_id = %s AND e.status = 1 AND o.term_id = %s
         GROUP BY c.code, c.name, c.credits, o.section_no, o.location
         ORDER BY c.code
        """,
        (user["id"], term["id"]),
    )

    used = sum(float(r["credits"]) for r in rows)
    weekday = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}

    print(f"已选 {len(rows)} 门 / {used} 学分")
    print("-" * 60)
    for r in rows:
        slot_text = []
        for token in (r["slots"] or "").split(","):
            if not token:
                continue
            d, a, b = token.split("-")
            slot_text.append(f"{weekday.get(int(d), d)}第{a}-{b}节")
        print(f"  {r['code']:<8} {r['name']:<18} {r['section_no']}班  "
              f"{'、'.join(slot_text) or '未排时间'}  {r['location']}")
    print("=" * 60)
    return 0
def dump_schema() -> int:
    targets = [a for a in sys.argv[1:] if not a.startswith("-")] or [
        "users",
        "courses",
        "offerings",
        "offering_slots",
        "rounds",
        "enrollments",
    ]
    for table in targets:
        print(f"===== {table} =====")
        for row in db.fetch_all(f"SHOW COLUMNS FROM `{table}`"):
            null = "NULL" if row["Null"] == "YES" else "NOT NULL"
            print(f"  {row['Field']:<18} {row['Type']:<18} {null}")
        print()
    return 0


def overview() -> int:
    print("=" * 60)

    term = db.fetch_one("SELECT id, code, name FROM terms WHERE is_current = 1 LIMIT 1")
    print("当前学期:", term if term else "FAIL 没有 is_current=1 的学期（去 /admin/terms 建）")

    rounds = db.fetch_all(
        """
        SELECT id, name, (NOW(3) BETWEEN opens_at AND closes_at) AS is_open, max_credits
          FROM rounds
         WHERE status = 1
         ORDER BY opens_at DESC
        """
    )
    if rounds:
        for row in rounds:
            flag = "开放中" if row["is_open"] else "未开放"
            print(f"轮次: id={row['id']} {row['name']}（{flag}，上限 {row['max_credits']} 学分）")
    else:
        print("轮次: FAIL 没有开放状态的轮次（学生无法选课，去 /admin/rounds 建）")

    print("-" * 60)
    print("课程:", db.fetch_one("SELECT COUNT(*) AS n FROM courses")["n"])
    print("班次:", db.fetch_one("SELECT COUNT(*) AS n FROM offerings")["n"])
    print("选课记录:", db.fetch_one("SELECT COUNT(*) AS n FROM enrollments")["n"])

    for role in ("student", "teacher", "admin"):
        row = db.fetch_one(
            """
            SELECT COUNT(*) AS n
              FROM users u
              JOIN user_roles ur ON ur.user_id = u.id
              JOIN roles r       ON r.id = ur.role_id
             WHERE r.code = %s
            """,
            (role,),
        )
        print(f"{role}: {row['n'] if row else 0}")

    print("-" * 60)
    rows = db.fetch_all("SELECT code, name, credits FROM courses ORDER BY code LIMIT 40")
    if rows:
        print("课程号清单（导入班次时只能用这些）:")
        for row in rows:
            print(f"  {row['code']:<10} {row['name']:<20} {row['credits']} 学分")
    else:
        print("FAIL 一门课都没有 —— 导入班次必然全部失败")

    print("=" * 60)
    return 0


# ---------------------------------------------------------------------------
def verify() -> int:
    """检查演示数据是否自洽。

    这几条是"数据是否像真实校园"的底线。数据不自洽时 Agent 的表现会很诡异 ——
    比如它检测出一堆冲突，而那其实是造数脚本自己造出来的非法数据。
    """
    print("=" * 60)

    term = db.fetch_one(
        """
        SELECT t.id AS id, COALESCE(r.max_credits, 0) AS max_credits
          FROM terms t
          LEFT JOIN rounds r ON r.term_id = t.id AND r.status = 1
         WHERE t.is_current = 1
         LIMIT 1
        """
    )
    if not term:
        print("FAIL 没有当前学期")
        return 1

    term_id = int(term["id"])
    limit = float(term["max_credits"])
    print(f"学期 {term_id}，轮次学分上限 {limit}")

    over = db.fetch_all(
        """
        SELECT e.student_id, SUM(e.credits) AS used
          FROM enrollments e
          JOIN offerings o ON o.id = e.offering_id
         WHERE e.status = 1 AND o.term_id = %s
         GROUP BY e.student_id
        HAVING used > %s
        """,
        (term_id, limit),
    )
    print(f"学分超限的学生: {len(over)}" + ("" if not over else f"  <- 有问题 {over[:3]}"))

    conflicts = db.fetch_all(
        """
        SELECT DISTINCT e1.student_id
          FROM enrollments e1
          JOIN offerings o1      ON o1.id = e1.offering_id
          JOIN offering_slots s1 ON s1.offering_id = o1.id
          JOIN enrollments e2    ON e2.student_id = e1.student_id
                                AND e2.offering_id > e1.offering_id
          JOIN offerings o2      ON o2.id = e2.offering_id
          JOIN offering_slots s2 ON s2.offering_id = o2.id
         WHERE e1.status = 1 AND e2.status = 1
           AND o1.term_id = %s AND o2.term_id = %s
           AND s1.weekday = s2.weekday
           AND s1.start_period <= s2.end_period
           AND s2.start_period <= s1.end_period
        """,
        (term_id, term_id),
    )
    print(f"存在时间冲突的学生: {len(conflicts)}" + ("" if not conflicts else f"  <- 有问题 {conflicts[:3]}"))

    mismatch = db.fetch_all(
        """
        SELECT o.id, o.taken, COUNT(e.id) AS real_taken
          FROM offerings o
          LEFT JOIN enrollments e ON e.offering_id = o.id AND e.status = 1
         WHERE o.term_id = %s
         GROUP BY o.id, o.taken
        HAVING o.taken <> real_taken
        """,
        (term_id,),
    )
    print(f"taken 与真实人数不一致的班次: {len(mismatch)}" + ("" if not mismatch else f"  <- 有问题 {mismatch[:3]}"))

    no_slots = db.fetch_one(
        """
        SELECT COUNT(*) AS n FROM offerings o
         WHERE o.term_id = %s
           AND NOT EXISTS (SELECT 1 FROM offering_slots s WHERE s.offering_id = o.id)
        """,
        (term_id,),
    )
    print(f"没有时间段的班次: {no_slots['n'] if no_slots else 0}")

    stat = db.fetch_one(
        """
        SELECT COUNT(*) AS offerings,
               COALESCE(SUM(CASE WHEN o.taken = 0 THEN 1 ELSE 0 END), 0) AS empty_offerings,
               COALESCE(SUM(CASE WHEN o.taken >= o.capacity THEN 1 ELSE 0 END), 0) AS full_offerings
          FROM offerings o WHERE o.term_id = %s
        """,
        (term_id,),
    )
    print(
        f"班次 {stat['offerings']} 个：空班 {stat['empty_offerings']}，"
        f"已满 {stat['full_offerings']}"
    )

    # 6. 会话串号：消息所属的 run 属于另一个用户
    crossed = db.fetch_all(
        """
        SELECT t.thread_id, COUNT(*) AS n
          FROM agent_messages m
          JOIN agent_threads t ON t.thread_id = m.thread_id
          JOIN agent_runs    r ON r.run_id    = m.run_id
         WHERE r.user_id <> t.user_id
         GROUP BY t.thread_id
        """
    )
    print(
        f"串号的会话（消息来自别的用户）: {len(crossed)}"
        + ("" if not crossed else f"  <- 有问题 {crossed[:3]}")
    )

    ok = not over and not conflicts and not mismatch and not crossed
    print("=" * 60)
    print("OK  数据自洽" if ok else "FAIL  数据有问题，上面标出了")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
