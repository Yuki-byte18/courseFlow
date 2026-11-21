"""审批闸门测试。

"执行"卡住的头号嫌疑就是它：写操作（选课/退课）会 emit 一次
`approval_required`，然后**阻塞等用户点确认**，最长 approval_timeout_s（默认 240s）。
如果前端没拿到那个事件，表现就是"卡在执行这一步"，最后超时失败。

这个测试把闸门单独验一遍，包括 AG-UI 编码 —— 前端能不能收到面板全靠它。

用法：
    cd agent
    python tests/test_approval.py
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.events import EventKind  # noqa: E402
from app.execenv.runtime import APPROVALS, Runtime, UserContext  # noqa: E402
from app.protocol.agui import AgUiEncoder  # noqa: E402
from app.tools import business  # noqa: F401,E402

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  {name:<44} OK")
    else:
        print(f"  {name:<44} FAIL  {detail}")
        FAILURES.append(f"{name} {detail}".strip())


def make_runtime(timeout_s: float = 5.0) -> Runtime:
    settings = dataclasses.replace(get_settings(), approval_timeout_s=timeout_s)
    return Runtime(
        goal="测试审批",
        user=UserContext(user_id=1, username="t", real_name="测试", scopes=("enrollment:create",)),
        model="fake",
        api_key="sk-fake-for-test-only",
        settings=settings,
        thread_id="",
    )


async def case_approve() -> None:
    runtime = make_runtime()
    result: dict[str, bool] = {}

    async def decide_later() -> None:
        # 等审批事件出现，再"点允许"
        for _ in range(100):
            await asyncio.sleep(0.02)
            required = [e for e in runtime.bus.history_snapshot() if e["kind"] == "approval_required"]
            if required:
                approval_id = required[0]["approval_id"]
                await asyncio.sleep(0.05)
                result["decided"] = APPROVALS.decide(approval_id, True)
                return

    decider = asyncio.create_task(decide_later())
    started = time.monotonic()
    approved = await runtime.approve("enroll_offering", {"offering_id": 9}, "选课")
    elapsed = time.monotonic() - started
    await decider

    check("允许 → approve() 返回 True", approved is True, f"得到 {approved}")
    check("决议被受理", result.get("decided") is True, str(result))
    check("响应及时（<2s）", elapsed < 2.0, f"{elapsed:.2f}s")

    events = runtime.bus.history_snapshot()
    required = [e for e in events if e["kind"] == "approval_required"]
    finished = [e for e in events if e["kind"] == "approval_result"]
    check("发出 approval_required", len(required) == 1, str(len(required)))
    check("发出 approval_result", len(finished) == 1, str(len(finished)))
    if required:
        payload = required[0]
        check(
            "事件带 approval_id/summary/timeout_s",
            bool(payload.get("approval_id"))
            and bool(payload.get("summary"))
            and payload.get("timeout_s") is not None,
            str(sorted(payload.keys())),
        )

    # 前端能不能拿到面板，全靠 AG-UI 这一层
    encoder = AgUiEncoder(thread_id=runtime.thread_id, run_id=runtime.run_id)
    encoded = encoder.encode(required[0]) if required else []
    kinds = [e.get("type") for e in encoded]
    check("AG-UI 产出 STATE_DELTA", "STATE_DELTA" in kinds, str(kinds))
    check("AG-UI 产出 CUSTOM 事件", "CUSTOM" in kinds, str(kinds))
    delta = next((e for e in encoded if e.get("type") == "STATE_DELTA"), None)
    if delta:
        ops = delta.get("delta") or []
        check(
            "补丁路径是 /approval",
            bool(ops) and ops[0].get("path") == "/approval",
            str(ops)[:80],
        )
        check("补丁带 args", bool(ops and ops[0].get("value", {}).get("args") is not None))
    custom = next((e for e in encoded if e.get("type") == "CUSTOM"), None)
    if custom:
        check(
            "CUSTOM 名字是 courseflow.approval_required",
            custom.get("name") == "courseflow.approval_required",
            str(custom.get("name")),
        )


async def case_reject() -> None:
    runtime = make_runtime()

    async def decide_later() -> None:
        for _ in range(100):
            await asyncio.sleep(0.02)
            required = [e for e in runtime.bus.history_snapshot() if e["kind"] == "approval_required"]
            if required:
                APPROVALS.decide(required[0]["approval_id"], False)
                return

    decider = asyncio.create_task(decide_later())
    approved = await runtime.approve("drop_offering", {"offering_id": 1}, "退课")
    await decider
    check("拒绝 → approve() 返回 False", approved is False, f"得到 {approved}")


async def case_timeout() -> None:
    runtime = make_runtime(timeout_s=1.0)
    started = time.monotonic()
    approved = await runtime.approve("enroll_offering", {"offering_id": 1}, "选课")
    elapsed = time.monotonic() - started

    check("超时 → approve() 返回 False", approved is False, f"得到 {approved}")
    check("超时耗时接近 1s", 0.9 <= elapsed < 3.0, f"{elapsed:.2f}s")

    events = runtime.bus.history_snapshot()
    finished = [e for e in events if e["kind"] == "approval_result"]
    check(
        "超时会发 approval_result(reason=timeout)",
        bool(finished) and finished[0].get("reason") == "timeout",
        str(finished[:1])[:120],
    )
    check("超时后不残留 pending", APPROVALS.pending_count == 0, str(APPROVALS.pending_count))


def test_stage_frame() -> None:
    """阶段帧必须带非空 stage —— 前端活动条全靠它。

    这个用例是补漏的：之前 set_stage 发的是 `name=` / `value={"stage": ...}`，
    而编码器读 `event["stage"]`，结果 26 个阶段帧全是空字符串，
    界面上永远显示"正在理解你的问题…"，用户根本不知道卡在哪一步。
    """
    runtime = make_runtime()
    runtime.set_stage("执行助手·第 1 轮思考")
    events = runtime.bus.history_snapshot()
    stage_events = [e for e in events if e["kind"] == "stage"]
    check("set_stage 发出了 stage 事件", len(stage_events) == 1, str(len(stage_events)))
    if not stage_events:
        return

    encoder = AgUiEncoder(thread_id=runtime.thread_id, run_id=runtime.run_id)
    encoded = encoder.encode(stage_events[0])
    frame = next((e for e in encoded if e.get("type") == "CUSTOM"), None)
    check("编码出 CUSTOM 帧", frame is not None, str(encoded)[:80])
    if frame:
        value = frame.get("value") or {}
        check(
            "阶段值非空",
            bool(value.get("stage")),
            f"实际 {value!r}（空就说明字段名对不上）",
        )
        check(
            "阶段值正确",
            value.get("stage") == "执行助手·第 1 轮思考",
            str(value.get("stage")),
        )


def main() -> int:
    ok, error = db.ping()
    if not ok:
        print(f"数据库连接失败：{error}")
        return 1

    print("=" * 62)
    print("[允许]")
    asyncio.run(case_approve())
    print("[拒绝]")
    asyncio.run(case_reject())
    print("[超时]")
    asyncio.run(case_timeout())
    print("[阶段帧]")
    test_stage_frame()
    print("=" * 62)

    if FAILURES:
        print(f"FAIL  {len(FAILURES)} 项未通过")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("OK  审批闸门正常：事件发得出、决议收得到、超时会兜底")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
