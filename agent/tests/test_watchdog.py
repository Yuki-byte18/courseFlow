"""看门狗测试：证明"卡住"必定在有限时间内被收尾。

要复现的问题：某个 await 卡死时，前面所有超时都失效
（工具超时、沙箱超时、审批超时、墙钟都是在"检查点"才生效的）。
用户看到的就是"两百秒没动静，也没有任何信息"。

看门狗只盯两件事：还有没有新事件、是不是在等用户。
一旦判定停滞就发错误 + 发兜底回答 + 关闭运行。

用法：
    cd agent
    python tests/test_watchdog.py
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.events import EventKind  # noqa: E402
from app.execenv.runtime import Runtime, UserContext  # noqa: E402
from app.tools import business  # noqa: F401,E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  {name:<44} OK")
    else:
        print(f"  {name:<44} FAIL  {detail}")
        FAILURES.append(f"{name} {detail}".strip())


def make_runtime() -> Runtime:
    return Runtime(
        goal="watchdog",
        user=UserContext(user_id=1, username="t", real_name="tester", scopes=("agent:chat",)),
        model="fake",
        api_key="sk-fake-for-test-only",
        settings=get_settings(),
        thread_id="",
    )


async def case_stall_cut() -> None:
    """主任务什么都不发事件，看门狗应在 ~stall_s 后收尾。"""
    runtime = make_runtime()
    runtime.bus.emit(EventKind.RUN_STARTED, run_id=runtime.run_id)

    async def stuck() -> None:
        # 模拟"某个 await 永不返回"
        await asyncio.sleep(3600)

    task = asyncio.create_task(stuck())
    watchdog = asyncio.create_task(runtime.enforce_stall(task, stall_s=2.0))

    started = time.monotonic()
    for _ in range(80):
        await asyncio.sleep(0.1)
        if runtime.bus.closed:
            break
    elapsed = time.monotonic() - started
    task.cancel()
    watchdog.cancel()

    events = runtime.bus.history_snapshot()
    kinds = [e["kind"] for e in events]

    check("看门狗在 5 秒内收尾", elapsed < 5.0, f"{elapsed:.1f}s")
    check("发出了 error 事件", "error" in kinds, str(kinds))
    check("发出了兜底 final", "final" in kinds, str(kinds))
    check("运行已关闭", runtime.bus.closed is True)

    final = next((e for e in events if e["kind"] == "final"), {})
    check(
        "兜底回答说明卡在哪",
        "卡在" in str(final.get("content")),
        str(final.get("content"))[:60],
    )
    err = next((e for e in events if e["kind"] == "error"), {})
    check("错误信息带阶段描述", "停滞" in str(err.get("message")), str(err.get("message"))[:60])


async def case_active_run_not_cut() -> None:
    """持续有事件的运行不能被看门狗误杀。"""
    runtime = make_runtime()
    runtime.bus.emit(EventKind.RUN_STARTED, run_id=runtime.run_id)

    stop = asyncio.Event()

    async def busy() -> None:
        while not stop.is_set():
            runtime.bus.emit(EventKind.DELTA, text="x")
            await asyncio.sleep(0.2)

    task = asyncio.create_task(busy())
    watchdog = asyncio.create_task(runtime.enforce_stall(task, stall_s=1.5))

    await asyncio.sleep(3.5)   # 远超 stall_s，但一直在发事件
    check("活跃运行没有被误杀", runtime.bus.closed is False)

    stop.set()
    task.cancel()
    watchdog.cancel()


async def case_approval_not_cut() -> None:
    """等审批期间不能被看门狗误杀（人慢不是运行停滞）。"""
    runtime = make_runtime()
    runtime.bus.emit(EventKind.RUN_STARTED, run_id=runtime.run_id)
    runtime._awaiting_approval = True

    async def stuck() -> None:
        await asyncio.sleep(3600)

    task = asyncio.create_task(stuck())
    watchdog = asyncio.create_task(runtime.enforce_stall(task, stall_s=1.0))

    await asyncio.sleep(3.0)
    check("等审批期间不误杀", runtime.bus.closed is False)

    runtime._awaiting_approval = False
    task.cancel()
    watchdog.cancel()


def main() -> int:
    ok, error = db.ping()
    if not ok:
        print(f"数据库连接失败：{error}")
        return 1

    print("=" * 62)
    print("[停滞收尾]")
    asyncio.run(case_stall_cut())
    print("[活跃运行]")
    asyncio.run(case_active_run_not_cut())
    print("[等审批]")
    asyncio.run(case_approval_not_cut())
    print("=" * 62)

    if FAILURES:
        print(f"FAIL  {len(FAILURES)} 项未通过")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("OK  看门狗正常：卡死会被收尾，活跃运行和等审批不会被误杀")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
