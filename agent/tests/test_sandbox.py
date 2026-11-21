"""沙箱（CodeAct）冒烟测试。

要证明的事：execute_code 在**服务进程里**（uvicorn / 异步）能可靠跑完，
不会挂住。

为什么专门测：compute 类子 agent 会被引导去写代码，
如果沙箱在某些运行环境下起不来、或者起得来但父进程永远等不到结果，
用户看到的就是"一到计算任务就卡住"。

用 faulthandler 做硬超时：超过 N 秒没结束就打印所有线程栈并退出，
这样能直接看到卡在哪一行，而不是干等。

用法：
    cd agent
    python tests/test_sandbox.py
"""

from __future__ import annotations

import asyncio
import faulthandler
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 超过 20 秒还没跑完，就把所有线程栈打出来并退出（exit=True）
faulthandler.dump_traceback_later(20, exit=True)

from app import db  # noqa: E402
from app.tools import business  # noqa: F401,E402
from app.tools.codeact import CODEACT  # noqa: E402
from app.tools.context import ToolContext  # noqa: E402
from app.tools.registry import REGISTRY  # noqa: E402

CASES = [
    ("纯计算", "print(sum(range(100)))", True),
    ("f-string", "x = 3.5\nprint(f'共 {x:.1f} 学分')", True),
    ("调用只读工具", "rows = call_tool('search_offerings', {'limit': 2})\nprint(len(rows['offerings']))", True),
    ("查课表", "data = call_tool('my_schedule', {})\nprint(len(data['items']))", True),
    # 和 scripts/probe_compute.py 里一模一样的一行 —— 用来复现探针在第 3 步的失败
    (
        "探针原样代码",
        "rows = call_tool('my_enrollments', {})\nprint('已选', len(rows['payload']['offerings']), '门')",
        True,
    ),
    ("语法错误", "print('unclosed", False),
    ("危险代码", "import os\nprint(os.getcwd())", False),
    ("死循环", "while True:\n    pass", False),
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

    sandbox_names = frozenset(t.name for t in REGISTRY.all() if t.visible_in_sandbox())
    ctx = ToolContext(
        run_id="sandbox-test",
        user_id=int(user["id"]),
        username=user["username"],
        scopes=("offering:read", "enrollment:read", "schedule:read"),
        agent="compute",
        sandbox_tool_names=sandbox_names,
    )

    failures: list[str] = []
    print("=" * 60)

    for name, code, should_succeed in CASES:
        started = time.monotonic()
        try:
            outcome = asyncio.run(
                CODEACT.execute(
                    ctx,
                    code,
                    "测试",
                    timeout_s=3.0,          # 死循环靠这个终止
                    max_output_chars=2000,
                    max_code_runs=10,
                    code_runs_used=0,
                )
            )
            elapsed = time.monotonic() - started
            good = outcome.ok == should_succeed
            mark = "OK" if good else "FAIL"
            detail = (outcome.preview or outcome.error or "").replace("\n", " ")[:60]
            print(f"  {name:<12} {elapsed:5.2f}s  ok={outcome.ok!s:<5} {mark:<4} {detail}")
            if not good:
                failures.append(f"{name}: 期望 ok={should_succeed}，实际 {outcome.ok}（{outcome.error[:80]}）")
                # 失败时把完整错误打出来，否则只看到一行 traceback 开头没法定位
                print("  ---- 完整错误 ----")
                for line in (outcome.error or "").splitlines()[-12:]:
                    print(f"    {line}")
                # repr 全是 ASCII 转义，能安全看出源码里是否有坏字符
                print(f"    code repr: {code!r}")
        except Exception as exc:  # noqa: BLE001
            elapsed = time.monotonic() - started
            print(f"  {name:<12} {elapsed:5.2f}s  抛异常 {exc}")
            failures.append(f"{name}: {exc}")

    print("=" * 60)
    if failures:
        print(f"FAIL  {len(failures)} 项不符合预期")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("OK  沙箱在异步环境下可靠执行，无挂起")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
