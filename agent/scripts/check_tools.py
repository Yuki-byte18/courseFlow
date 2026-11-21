"""逐个调用业务工具，确认它们真的能跑通。

为什么需要这个脚本：
工具里的 SQL 写错一个列名，Agent 不会报错，它只会「降级」——
比如 my_enrollments 挂了，它就退回用 my_schedule，然后告诉你
"选课列表接口有故障，以上清单来自周课表"。
用户看到的是"Agent 好像不太聪明"，实际是一行 SQL 拼错了。

所以每个只读工具都要单独冒烟一次。写工具的调用（选课/退课/存提醒）
不在其中：它们要过审批闸门和 Go 后端，用 check_isolation / 手工验更合适。

用法：
    cd agent
    python scripts/check_tools.py
    python scripts/check_tools.py 2024005     # 指定用哪个学生账号
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.tools import business  # noqa: F401,E402  —— import 即注册
from app.tools.context import ToolContext  # noqa: E402
from app.tools.registry import REGISTRY  # noqa: E402

# 需要审批或需要 Go 后端的写工具，跳过
SKIP = {"enroll_offering", "drop_offering", "save_reminder"}


def main() -> int:
    ok, error = db.ping()
    if not ok:
        print(f"数据库连接失败：{error}")
        return 1

    # 自己解析参数：--show 后面跟的是工具名，不能当成用户名
    argv = sys.argv[1:]
    positional: list[str] = []
    show_tool = ""
    explain_tool = ""
    explain_args = ""
    verbose = "--trace" in argv
    i = 0
    while i < len(argv):
        if argv[i] == "--show":
            show_tool = argv[i + 1] if i + 1 < len(argv) else ""
            i += 2
        elif argv[i] == "--explain":
            explain_tool = argv[i + 1] if i + 1 < len(argv) else ""
            explain_args = argv[i + 2] if i + 2 < len(argv) and not argv[i + 2].startswith("-") else ""
            i += 2 if not explain_args else 3
        elif argv[i].startswith("-"):
            i += 1
        else:
            positional.append(argv[i])
            i += 1

    username = positional[0] if positional else "2024001"
    user = db.fetch_one(
        "SELECT id, username, real_name FROM users WHERE username = %s", (username,)
    )
    if not user:
        print(f"找不到用户 {username}")
        return 1

    # 取两个真实班次，给需要 offering_id 的工具用
    offerings = db.fetch_all(
        "SELECT id FROM offerings ORDER BY id LIMIT 4"
    )
    offering_ids = [int(r["id"]) for r in offerings]
    if len(offering_ids) < 2:
        print("班次太少，先跑 go run ./cmd/seedcampus")
        return 1

    ctx = ToolContext(
        run_id="smoke",
        user_id=int(user["id"]),
        username=user["username"],
        scopes=(
            "offering:read",
            "enrollment:create",
            "enrollment:drop",
            "enrollment:read",
            "schedule:read",
            "roster:read",
            "agent:chat",
        ),
        user_token="",  # 只读工具不需要
        agent="query",
        sandbox_tool_names=frozenset(t.name for t in REGISTRY.all() if t.visible_in_sandbox()),
    )

    # 每个工具一份最小可用参数
    cases: dict[str, dict] = {
        "get_current_term": {},
        "list_terms": {},
        "search_offerings": {},
        "get_offering": {"offering_id": offering_ids[0]},
        "my_enrollments": {},
        "my_enrollments#dropped": {"include_dropped": True},
        "my_schedule": {},
        "get_current_round": {},
        "compute_credit_summary": {},
        "compute_conflicts": {"offering_ids": offering_ids[:2]},
        "compute_remaining_ranking": {},
        "compute_free_slots": {},
        "compute_candidates": {},
        "compose_notice": {"title": "冒烟测试", "body": "正文"},
        "list_reminders": {},
    }

    # --explain <tool> [json]：把"模型看到什么 / 填了什么 / 实际跑什么 SQL"全打出来
    if explain_tool:
        import json

        spec = REGISTRY.require(explain_tool)
        args = cases.get(explain_tool, {})
        if explain_args:
            try:
                args = json.loads(explain_args)
            except json.JSONDecodeError:
                print(f"--explain 的第二参数必须是 JSON，收到：{explain_args}")
                return 1

        print("=" * 60)
        print(f"① 模型能看到什么（只有工具名与参数 schema，没有 SQL）")
        print(f"   工具: {spec.name}")
        print(f"   参数: {json.dumps(spec.parameters, ensure_ascii=False)}")
        print()
        print(f"② 模型填的参数（这就是它产出的全部内容）")
        print(f"   {json.dumps(args, ensure_ascii=False)}")
        print()

        db.start_trace()
        result = spec.handler(ctx, args)
        records = db.stop_trace()

        print(f"③ 工具内部实际执行的 SQL（常量串 + %s 占位符）")
        for sql, params in records:
            flat = " ".join(sql.split())
            print(f"   SQL   : {flat[:180]}{'…' if len(flat) > 180 else ''}")
            print(f"   绑定值: {list(params)}")
        if not records:
            print("   （这个工具没查库，纯计算）")
        print()
        print(f"④ 返回给模型的内容（结构化数据，不是 SQL 结果集）")
        preview = json.dumps(result, ensure_ascii=False, default=str)
        print(f"   {preview[:400]}{'…' if len(preview) > 400 else ''}")
        print("=" * 60)
        return 0

    print("=" * 60)
    print(f"用 {user['real_name']}（{user['username']}）跑工具冒烟测试")
    print("-" * 60)

    failures: list[tuple[str, str]] = []
    tested = 0

    for case_name, args in cases.items():
        tool_name = case_name.split("#")[0]
        if tool_name in SKIP:
            continue

        spec = REGISTRY.get(tool_name)
        if spec is None:
            failures.append((case_name, "工具未注册"))
            continue

        tested += 1
        try:
            result = spec.handler(ctx, args)
            # 只检查"没抛异常"和"不是空得离谱"
            size = len(str(result))
            status = "OK" if size > 2 else "WARN 返回过短"
            print(f"  {case_name:<26} {status}  ({size} 字符)")
            if size <= 2:
                failures.append((case_name, f"返回过短：{result!r}"))
        except Exception as exc:  # noqa: BLE001
            print(f"  {case_name:<26} FAIL  {exc}")
            failures.append((case_name, str(exc)))
            if "--trace" in sys.argv:
                traceback.print_exc()

    print("-" * 60)
    print(f"共测 {tested} 个工具，失败 {len(failures)} 个")
    print("=" * 60)

    if failures:
        print("FAIL  以下工具不可用（Agent 会静默降级，表现为「接口故障」）：")
        for name, msg in failures:
            print(f"  - {name}: {msg}")
        return 1

    print("OK  所有只读工具都能正常执行")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
