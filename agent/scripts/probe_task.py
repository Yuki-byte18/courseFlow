"""端到端任务探针：用真实模型跑完整的一句话任务。

和 probe_compute.py 的区别：那个只跑"计算助手"一个子 agent；
这个跑**完整链路**（意图 → 建图 → 各子 agent → 反思 → 收尾），
也就是用户在页面上看到的同一件事。

同时装了两个探测器：
  1. 事件循环阻塞探测：如果某个同步调用把 loop 卡住了，这里能看到卡了多久
  2. 事件间隔监控：任何超过 3 秒的静默都会被打上时间戳

用法：
    cd agent
    $env:DEEPSEEK_API_KEY = "sk-..."
    python scripts/probe_task.py "帮我选 CS109 和 CS110"
"""

from __future__ import annotations

import asyncio
import faulthandler
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 180 秒还没结束就把所有线程栈打出来（直指卡在哪一行）
faulthandler.dump_traceback_later(180, exit=True)

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.execenv.runtime import Runtime, UserContext  # noqa: E402
from app.orchestration.supervisor import Supervisor  # noqa: E402
from app.tools import business  # noqa: F401,E402

USERNAME = os.environ.get("PROBE_USER", "2024002")
PASSWORD = "Passw0rd123"
TASK = sys.argv[1] if len(sys.argv) > 1 else "帮我选 CS109 和 CS110"
MODEL = os.environ.get("PROBE_MODEL", "deepseek-flash")

T0 = time.monotonic()
_last_event_at = time.monotonic()
_max_gap = 0.0


def stamp() -> str:
    return f"+{time.monotonic() - T0:6.2f}s"


async def loop_lag_monitor(stop: asyncio.Event) -> None:
    """测事件循环有没有被同步调用卡住。

    如果 loop 被卡住，这个协程自己也不会被调度 —— 所以它报告的是
    "卡完之后"的滞后量，正好能证明"刚才有 X 秒完全没有人在跑协程"。
    """
    while not stop.is_set():
        started = time.monotonic()
        try:
            await asyncio.wait_for(asyncio.sleep(0.5), timeout=5.0)
        except asyncio.TimeoutError:
            pass
        lag = time.monotonic() - started - 0.5
        if lag > 1.5:
            print(f"{stamp()} [!! 事件循环被阻塞 {lag:.1f}s]")


def login() -> tuple[str, int, str]:
    settings = get_settings()
    resp = httpx.post(
        f"{settings.go_base_url}/api/v1/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=8.0,
    )
    resp.raise_for_status()
    body = resp.json()
    token = body.get("token") or body.get("access_token") or ""
    user = body.get("user") or {}
    return token, int(user.get("user_id") or 0), str(user.get("real_name") or "")


def scopes_of(user_id: int) -> tuple[str, ...]:
    rows = db.fetch_all(
        """
        SELECT DISTINCT p.code AS code
          FROM user_roles ur
          JOIN role_permissions rp ON rp.role_id = ur.role_id
          JOIN permissions p ON p.id = rp.permission_id
         WHERE ur.user_id = %s
        """,
        (user_id,),
    )
    codes = tuple(sorted({row["code"] for row in rows}))
    return codes or ("offering:read", "enrollment:read", "schedule:read")


async def run() -> int:
    api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        print("请先设置 DEEPSEEK_API_KEY")
        return 1

    settings = get_settings()
    print("=" * 68)
    print(f"任务：{TASK}")
    print(f"用户：{USERNAME}  模型：{MODEL}")

    try:
        token, user_id, real_name = login()
        print(f"Go 登录 OK，user_id={user_id}")
    except Exception as exc:  # noqa: BLE001
        print(f"Go 登录失败（{exc}）—— 写操作会失败，但链路仍可观察")
        token, user_id, real_name = "", 0, ""
        row = db.fetch_one("SELECT id, real_name FROM users WHERE username = %s", (USERNAME,))
        if row:
            user_id, real_name = int(row["id"]), str(row["real_name"] or "")

    scopes = scopes_of(user_id)
    print(f"权限 {len(scopes)} 个；墙钟 {settings.max_wall_seconds}s，"
          f"单次调用 {settings.request_timeout_s}s，审批 {settings.approval_timeout_s}s")
    print("-" * 68)

    runtime = Runtime(
        goal=TASK,
        user=UserContext(
            user_id=user_id,
            username=USERNAME,
            real_name=real_name,
            scopes=scopes,
            token=token,
        ),
        model=MODEL,
        api_key=api_key,
        settings=settings,
        thread_id="",
    )

    async def auto_approve(tool: str, args: dict, description: str) -> bool:
        print(f"{stamp()} [审批] {tool} {json.dumps(args, ensure_ascii=False)} → 允许")
        return True

    runtime.approve = auto_approve  # type: ignore[assignment]

    stop = asyncio.Event()
    monitor = asyncio.create_task(loop_lag_monitor(stop))

    # 打印所有事件，并统计最长静默
    printed = 0
    watchdog_task = asyncio.create_task(runtime.enforce_stall(
        asyncio.current_task(),  # type: ignore[arg-type]
        stall_s=60.0,
    ))

    state = {"printed": 0, "last": time.monotonic(), "max_gap": 0.0}

    async def printer() -> None:
        while not stop.is_set():
            await asyncio.sleep(0.1)
            events = runtime.bus.history_snapshot()
            while state["printed"] < len(events):
                event = events[state["printed"]]
                state["printed"] += 1
                now = time.monotonic()
                gap = now - state["last"]
                state["max_gap"] = max(state["max_gap"], gap)
                if gap > 3.0:
                    print(f"{stamp()} [-- 静默 {gap:.1f}s --]")
                state["last"] = now
                kind = event["kind"]
                if kind in ("stage", "node_status", "agent_started", "agent_finished",
                            "tool_call", "tool_result", "code_run", "code_output",
                            "error", "final", "graph"):
                    print(f"{stamp()} {kind:<14} {_brief(event)}")

    printer_task = asyncio.create_task(printer())

    started = time.monotonic()
    try:
        await asyncio.wait_for(
            Supervisor(settings).run(runtime, message=TASK, history=[]),
            timeout=170.0,
        )
    except asyncio.TimeoutError:
        print(f"{stamp()} [!! 整体超过 170s]")
    finally:
        await asyncio.sleep(0.3)
        stop.set()
        monitor.cancel()
        printer_task.cancel()
        watchdog_task.cancel()

    elapsed = time.monotonic() - started
    events = runtime.bus.history_snapshot()
    kinds: dict[str, int] = {}
    for event in events:
        kinds[event["kind"]] = kinds.get(event["kind"], 0) + 1

    print("-" * 68)
    print(f"总耗时 {elapsed:.2f}s，最长静默 {state['max_gap']:.1f}s")
    print(f"事件统计 {kinds}")
    final = next((e for e in events if e["kind"] == "final"), None)
    if final:
        print(f"最终回答（前 200 字）：{str(final.get('content'))[:200]}")
    else:
        print("没有 final 事件 —— 用户拿不到回答")

    # 清理本次探针留下的会话
    try:
        db.execute("DELETE FROM agent_messages WHERE thread_id = %s", (runtime.thread_id,))
        db.execute("DELETE FROM agent_threads WHERE thread_id = %s", (runtime.thread_id,))
    except Exception:  # noqa: BLE001
        pass

    print("=" * 68)
    ok = bool(final) and not kinds.get("error")
    print("OK  链路完成" if ok else "FAIL 链路没有正常收尾")
    return 0 if ok else 1


def _brief(event: dict) -> str:
    kind = event["kind"]
    if kind == "stage":
        return str(event.get("stage"))
    if kind == "node_status":
        return f"{event.get('agent')} {event.get('status')}"
    if kind == "agent_started":
        return f"{event.get('agent')} :: {event.get('goal')}"
    if kind == "agent_finished":
        return f"{event.get('agent')} ok={event.get('ok')} {str(event.get('summary'))[:50]}"
    if kind == "tool_call":
        return f"{event.get('tool')} args={str(event.get('args'))[:60]}"
    if kind == "tool_result":
        return f"{event.get('tool')} ok={event.get('ok')} {str(event.get('preview'))[:60]}"
    if kind == "code_run":
        return f"blocked={event.get('blocked')} {str(event.get('reason'))[:50]}"
    if kind == "code_output":
        return str(event.get("output"))[:70]
    if kind == "error":
        return str(event.get("message"))[:80]
    if kind == "final":
        return f"len={len(str(event.get('content')))}"
    if kind == "graph":
        return f"nodes={len(event.get('nodes') or [])}"
    return ""


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
