"""治理与安全的不变量测试。

不依赖 pytest，直接跑：
    cd agent
    python tests/test_guards.py

测的是"保证"本身，不是业务流程：
  - 预算用尽后必须拒绝（否则运行会一直转圈）
  - 沙箱的 AST 白名单必须拒绝 import 与 dunder 访问
  - 代码通道连续被拒时能把它从工具列表里摘掉
  - LIKE 通配符必须被转义
  - db 层必须拒绝多语句
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.governance.budget import Budget  # noqa: E402
from app.orchestration.agents import _without_execute_code  # noqa: E402
from app.orchestration.graph import NodeStatus, TaskGraph, TaskNode  # noqa: E402
from app.tools import astguard, business  # noqa: F401,E402
from app.tools.business.common import like_pattern  # noqa: E402
from app.tools.policy import POLICY  # noqa: E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  {name:<46} OK")
    else:
        print(f"  {name:<46} FAIL  {detail}")
        FAILURES.append(f"{name} {detail}".strip())


def test_budget() -> None:
    print("[预算]")
    budget = Budget(max_tool_calls=2, max_code_runs=1, max_wall_seconds=60, max_llm_calls=3)

    check("tool 前两次通过", budget.spend_tool() and budget.spend_tool())
    check("tool 第三次被拒", not budget.spend_tool())

    check("code 第一次通过", budget.spend_code())
    check("code 第二次被拒", not budget.spend_code())

    # 模型调用预算是防"一直转圈"的最后一根闸
    results = [budget.spend_llm() for _ in range(4)]
    check("llm 前三次通过", results[:3] == [True, True, True], str(results))
    check("llm 第四次被拒", results[3] is False, str(results))

    # 审批等待必须从墙钟里扣掉
    paused = Budget(max_tool_calls=1, max_code_runs=1, max_wall_seconds=1.0, max_llm_calls=1)
    time.sleep(0.3)
    before = paused.elapsed_s
    paused.pause(5.0)
    after = paused.elapsed_s
    check(
        "审批等待会从墙钟扣除",
        after < before and after >= 0,
        f"暂停前 {before:.2f}s，暂停后 {after:.2f}s",
    )


def test_astguard() -> None:
    print("[AST 白名单]")

    blocked = [
        ("import pymysql", "import"),
        ("import os", "import"),
        ("__import__('os')", "dunder/__import__"),
        ("x.__class__.__bases__", "dunder 属性"),
        ("eval('1+1')", "eval"),
        ("open('/etc/passwd')", "open"),
        ("exec('x=1')", "exec"),
        ("getattr(x, 'y')", "getattr"),
    ]
    for code, label in blocked:
        report = astguard.validate(code)
        check(f"拒绝 {label}", not report.ok, f"却通过了：{code!r}")

    allowed = [
        ("print('hi')", "print"),
        ("call_tool('search_offerings', {'keyword': 'x'})\nprint('ok')", "call_tool + print"),
        ("rows = call_tool('my_enrollments', {})\nprint(len(rows))\nprint(sum([1,2,3]))", "取值与计算"),
        ("items = [{'a': 1}]\nprint(sorted(items, key=lambda i: -i['a']))", "lambda/sorted"),
        ("print(f'{3.0:.1f}')", "f-string 格式化"),
        ("for i in range(3):\n    print(i)", "循环"),
    ]
    for code, label in allowed:
        report = astguard.validate(code)
        check(f"允许 {label}", report.ok, f"被误拦：{report.reason}")

    # 没有任何输出手段时应拒绝
    report = astguard.validate("x = 1 + 1")
    check("拒绝无输出的代码", not report.ok, "应当提示必须 print")


def test_tool_pruning() -> None:
    print("[代码通道摘除]")
    schema = [
        {"type": "function", "function": {"name": "execute_code"}},
        {"type": "function", "function": {"name": "search_offerings"}},
    ]
    pruned = _without_execute_code(schema)
    names = [(item.get("function") or {}).get("name") for item in (pruned or [])]
    check("摘掉 execute_code", "execute_code" not in names, str(names))
    check("保留其他工具", "search_offerings" in names, str(names))
    check("只剩 execute_code 时返回 None", _without_execute_code(schema[:1]) is None)


def test_like_pattern() -> None:
    print("[LIKE 转义]")
    check("% 被转义", like_pattern("100%") == "%100\\%%", like_pattern("100%"))
    check("_ 被转义", like_pattern("a_b") == "%a\\_b%", like_pattern("a_b"))
    check("反斜杠被转义", like_pattern("a\\b") == "%a\\\\b%", like_pattern("a\\b"))
    check("普通文字不变", like_pattern("数据") == "%数据%")


def test_db_guard() -> None:
    print("[SQL 多语句拦截]")
    for sql in ("SELECT 1; DROP TABLE users", "SELECT 1; ", "DELETE FROM x; DELETE FROM y"):
        try:
            db._guard(sql)
            check(f"拒绝 {sql[:28]!r}", False, "未拦截")
        except ValueError:
            check(f"拒绝 {sql[:28]!r}", True)

    try:
        db._guard("SELECT * FROM offerings WHERE id = %s")
        check("允许单条查询", True)
    except ValueError as exc:
        check("允许单条查询", False, str(exc))


def test_graph() -> None:
    print("[任务图：依赖 / 剪枝 / 回退]")

    def make() -> TaskGraph:
        return TaskGraph(
            [
                TaskNode(id="n1", agent="query", goal="查课"),
                TaskNode(id="n2", agent="compute", goal="算冲突", deps=["n1"]),
                TaskNode(id="n3", agent="execute", goal="选课", deps=["n2"]),
            ]
        )

    # 依赖满足才可执行
    graph = make()
    check("初始只有 n1 可执行", graph.next_ready().id == "n1")  # type: ignore[union-attr]
    graph.mark("n1", NodeStatus.DONE, result="ok")
    ready = graph.next_ready()
    check("n1 完成后 n2 可执行", ready is not None and ready.id == "n2")

    # 失败时下游是 skipped 而不是被静默丢掉
    graph = make()
    graph.mark("n1", NodeStatus.DONE)
    graph.mark("n2", NodeStatus.FAILED, error="boom")
    graph.prune_dependents("n2")
    check("失败后下游被剪掉", graph.get("n3") is None)

    # 回退：把失败节点重开，图恢复可执行
    graph = make()
    graph.mark("n1", NodeStatus.DONE)
    graph.mark("n2", NodeStatus.FAILED, error="临时故障")
    reopened = graph.reopen_all_failed()
    check("回退重开了 1 个节点", reopened == 1, str(reopened))
    again = graph.next_ready()
    check("回退后 n2 重新可执行", again is not None and again.id == "n2")

    # 已完成的节点不能被回退重开（否则会重复干活）
    check("不回退已完成节点", not graph.reopen("n1"))


def test_policy_channels() -> None:
    print("[工具通道分配]")
    scopes = (
        "offering:read",
        "enrollment:read",
        "enrollment:create",
        "enrollment:drop",
        "schedule:read",
        "agent:chat",
    )

    def names(agent: str) -> list[str]:
        return [
            str((item.get("function") or {}).get("name"))
            for item in POLICY.decide(agent=agent, scopes=scopes).tools_schema
        ]

    # 代码通道只给 compute。
    # 给 query 的代价是真实的：它会在取数的节点里连写十几次代码，
    # 把预算和步骤全烧光，后面的 execute 节点就再也没机会跑。
    for agent in ("query", "execute", "notify"):
        got = names(agent)
        check(f"{agent} 没有代码通道", "execute_code" not in got, str(got))

    check("compute 有代码通道", "execute_code" in names("compute"), str(names("compute")))

    execute_tools = names("execute")
    check("execute 有写工具", "enroll_offering" in execute_tools and "drop_offering" in execute_tools)
    check("execute 能借用只读工具查班次", "get_offering" in execute_tools, str(execute_tools))

    # 借用机制绝不能把写工具借给别的 agent
    for agent in ("query", "compute", "notify"):
        got = names(agent)
        check(
            f"{agent} 拿不到写工具",
            "enroll_offering" not in got and "drop_offering" not in got,
            str(got),
        )


def main() -> int:
    print("=" * 60)
    test_budget()
    test_astguard()
    test_tool_pruning()
    test_like_pattern()
    test_db_guard()
    test_graph()
    test_policy_channels()
    print("=" * 60)
    if FAILURES:
        print(f"FAIL  {len(FAILURES)} 项未通过")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("OK  全部不变量成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
