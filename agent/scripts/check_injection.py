"""SQL 注入回归测试。

要证明的核心事实：
**模型永远拿不到 SQL。** 它只能给工具填参数，而参数走 `%s` 占位符；
即使它在参数里塞一句 `'; DROP TABLE courses; --`，那也只是一个字符串值。

这个脚本真的会尝试几种注入，然后检查：
  1. 没有报错（参数被当成普通值处理，而不是拼进 SQL）
  2. 结果没有被放大（`' OR '1'='1` 不会把整张表捞出来）
  3. 表还在（多语句没被执行）

另外顺带验证沙箱里 `import pymysql` 会被 AST 白名单直接拦下。

用法：
    cd agent
    python scripts/check_injection.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.tools import astguard  # noqa: E402
from app.tools import business  # noqa: F401,E402
from app.tools.context import ToolContext  # noqa: E402
from app.tools.direct import DIRECT  # noqa: E402
from app.tools.registry import REGISTRY  # noqa: E402

PAYLOADS = [
    "' OR '1'='1",
    "'; DROP TABLE courses; --",
    "' UNION SELECT password_hash FROM users --",
    "%",
    "_",
    "\\",
]


def main() -> int:
    ok, error = db.ping()
    if not ok:
        print(f"数据库连接失败：{error}")
        return 1

    user = db.fetch_one("SELECT id, username FROM users WHERE username = '2024001'")
    if not user:
        print("找不到 2024001，先跑 go run ./cmd/seedcampus")
        return 1

    ctx = ToolContext(
        run_id="injection",
        user_id=int(user["id"]),
        username=user["username"],
        scopes=("offering:read", "enrollment:read", "schedule:read", "agent:chat"),
        agent="query",
    )

    course_count_before = db.fetch_one("SELECT COUNT(*) AS n FROM courses")["n"]
    baseline = asyncio.run(DIRECT.invoke(ctx, "search_offerings", {}))
    # ToolOutcome.data 已经是拆掉 card 之后的 payload
    baseline_count = baseline.data["count"]

    print("=" * 60)
    print(f"基线：search_offerings 无条件返回 {baseline_count} 条，课程表 {course_count_before} 门")
    print("-" * 60)

    failures: list[str] = []

    # ---- 1. 关键词注入 ----
    for payload in PAYLOADS:
        outcome = asyncio.run(DIRECT.invoke(ctx, "search_offerings", {"keyword": payload}))
        if not outcome.ok:
            print(f"  keyword={payload!r:<42} 返回失败：{outcome.error[:40]}")
            failures.append(f"keyword={payload!r} 抛错：{outcome.error[:80]}")
            continue

        count = outcome.data["count"]
        # 通配符已被转义，所以 `%` / `_` 应该当作普通字符，匹配不到任何课程
        leaked = " OR " in payload and count > 0
        status = "OK" if count == 0 and not leaked else "FAIL"
        if status == "FAIL":
            failures.append(f"keyword={payload!r} 返回 {count} 条（应 0）")
        print(f"  keyword={payload!r:<42} {count} 条  {status}")

    # ---- 2. 类型注入：非要传字符串给整型参数 ----
    outcome = asyncio.run(DIRECT.invoke(ctx, "get_offering", {"offering_id": "1 OR 1=1"}))
    blocked = not outcome.ok
    print(f"  get_offering(offering_id='1 OR 1=1')       {'被参数校验拦下' if blocked else '竟然通过'}  "
          f"{'OK' if blocked else 'FAIL'}")
    if not blocked:
        failures.append("整型参数接受了字符串注入")

    # ---- 3. 数组参数注入 ----
    outcome = asyncio.run(
        DIRECT.invoke(ctx, "compute_conflicts", {"offering_ids": ["1; DROP TABLE courses; --"]})
    )
    # 这里要么被 int() 拒绝（抛错→工具失败），要么被当成普通值；表必须还在
    print(f"  compute_conflicts(['1; DROP ...'])         {'工具失败（安全）' if not outcome.ok else '执行了但无副作用'}")

    # ---- 4. 枚举参数注入（ORDER BY 方向）----
    outcome = asyncio.run(
        DIRECT.invoke(ctx, "compute_remaining_ranking", {"order": "ASC; DROP TABLE courses; --"})
    )
    if outcome.ok:
        direction = outcome.data.get("order")
        safe = direction in ("asc", "desc")
        print(f"  compute_remaining_ranking(order=注入串)      实际方向={direction!r}  "
              f"{'OK' if safe else 'FAIL'}")
        if not safe:
            failures.append(f"ORDER BY 方向未走白名单：{direction!r}")
    else:
        print(f"  compute_remaining_ranking(order=注入串)      工具失败（安全）")

    # ---- 5. 表还在吗 ----
    course_count_after = db.fetch_one("SELECT COUNT(*) AS n FROM courses")["n"]
    intact = course_count_after == course_count_before
    print(f"  课程表条数 {course_count_before} -> {course_count_after}  {'OK' if intact else 'FAIL'}")
    if not intact:
        failures.append("课程表被修改了")

    # ---- 6. 沙箱：模型写的代码里能不能 import pymysql ----
    report = astguard.validate("import pymysql\nprint(pymysql)")
    blocked = not report.ok
    print(f"  sandbox: import pymysql                      {'被 AST 拦下' if blocked else '竟然通过'}  "
          f"{'OK' if blocked else 'FAIL'}")
    if not blocked:
        failures.append("沙箱允许 import pymysql")

    report2 = astguard.validate("call_tool('search_offerings', {})\nprint('x')")
    allowed = report2.ok
    print(f"  sandbox: call_tool('search_offerings')       {'允许' if allowed else '被拦'}  "
          f"{'OK' if allowed else 'FAIL'}")
    if not allowed:
        failures.append(f"正常的 call_tool 被误拦：{report2.reason}")

    print("=" * 60)
    if failures:
        print("FAIL  注入防护有问题：")
        for item in failures:
            print(f"  - {item}")
        return 1

    print("OK  模型无法生成 SQL，也无法通过参数注入影响 SQL 结构")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
