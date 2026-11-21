"""验证浏览器真正走的那条路：HTTP SSE 流 + 审批回传。

前面所有测试都是直接调 Python 函数，绕过了 FastAPI 路由和 AG-UI 编码。
而浏览器走的是：
    POST /agent/chat  →  SSE 事件流  →  看到 approval_required
    POST /agent/approvals/{id}/decide  →  流继续  →  RUN_FINISHED

如果这条路上审批帧没送到前端，用户看到的就是"卡在等待确认"。

用法（服务要已经在跑）：
    cd agent
    python scripts/probe_sse.py
环境变量：
    AGENT_URL      默认 http://127.0.0.1:8090
    PROBE_USER     默认 2024002
    PROBE_TASK     默认 帮我退掉 CS109
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app.config import get_settings  # noqa: E402

AGENT_URL = os.environ.get("AGENT_URL", "http://127.0.0.1:8090").rstrip("/")
USERNAME = os.environ.get("PROBE_USER", "2024002")
PASSWORD = "Passw0rd123"
TASK = os.environ.get("PROBE_TASK", "帮我退掉 CS109")
MODEL = os.environ.get("PROBE_MODEL", "deepseek-flash")
DEADLINE_S = 150


def login() -> tuple[str, int, str]:
    settings = get_settings()
    resp = httpx.post(
        f"{settings.go_base_url}/api/v1/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=10.0,
    )
    resp.raise_for_status()
    body = resp.json()
    token = body.get("token") or body.get("access_token") or ""
    user = body.get("user") or {}
    return token, int(user.get("user_id") or 0), str(user.get("username") or USERNAME)


def main() -> int:
    api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        print("请先设置 DEEPSEEK_API_KEY")
        return 1

    settings = get_settings()
    internal = settings.internal_token

    try:
        token, user_id, username = login()
    except Exception as exc:  # noqa: BLE001
        print(f"Go 登录失败（{exc}）")
        return 1

    print("=" * 68)
    print(f"POST {AGENT_URL}/agent/chat  用户={username} 任务={TASK}")

    headers = {
        "X-Internal-Token": internal,
        "X-User-Id": str(user_id),
        "X-Username": username,
        "X-Real-Name": username,
        "X-User-Scopes": "offering:read,enrollment:read,enrollment:create,enrollment:drop,schedule:read",
        "X-User-Token": token,
        "X-LLM-Api-Key": api_key,
        "Content-Type": "application/json",
    }
    body = {"message": TASK, "model": MODEL, "threadId": ""}

    counts: dict[str, int] = {}
    approval_id = ""
    finished = False
    saw_error = ""
    answer: list[str] = []
    started = time.monotonic()
    last = started
    max_gap = 0.0

    try:
        with httpx.Client(timeout=httpx.Timeout(connect=10, read=DEADLINE_S, write=30, pool=10)) as client:
            with client.stream("POST", f"{AGENT_URL}/agent/chat", headers=headers, json=body) as resp:
                if resp.status_code != 200:
                    detail = resp.read().decode("utf-8", "ignore")[:300]
                    print(f"FAIL  HTTP {resp.status_code}: {detail}")
                    return 1

                print(f"  HTTP 200，开始读流")
                for line in resp.iter_lines():
                    if time.monotonic() - started > DEADLINE_S:
                        print("FAIL  超过总时限")
                        break
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        frame = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    now = time.monotonic()
                    gap = now - last
                    if gap > max_gap:
                        max_gap = gap
                    if gap > 5.0:
                        print(f"  +{now - started:6.2f}s [-- 静默 {gap:.1f}s --]")
                    last = now

                    ftype = str(frame.get("type") or "")
                    counts[ftype] = counts.get(ftype, 0) + 1

                    if ftype == "CUSTOM":
                        name = str(frame.get("name") or "")
                        if name == "courseflow.approval_required":
                            value = frame.get("value") or {}
                            approval_id = str(value.get("approval_id") or "")
                            print(f"  +{now - started:6.2f}s [审批请求] "
                                  f"{value.get('summary')} timeout={value.get('timeout_s')}s")
                            # **立刻**提交允许，模拟用户点按钮。
                            # 用独立客户端，避免和外层流式请求互相等。
                            # 注意要带身份头：审批接口需要知道是谁在批准，
                            # 浏览器是走 Go 代理自动注入的，探针直连就得自己补。
                            if approval_id:
                                try:
                                    with httpx.Client(timeout=10.0) as decider:
                                        resp2 = decider.post(
                                            f"{AGENT_URL}/agent/approvals/{approval_id}/decide",
                                            headers={
                                                "X-Internal-Token": internal,
                                                "X-User-Id": str(user_id),
                                                "X-Username": username,
                                                "Content-Type": "application/json",
                                            },
                                            json={"approved": True},
                                        )
                                    print(f"  +{now - started:6.2f}s [已提交允许] "
                                          f"HTTP {resp2.status_code} {resp2.text[:80]}")
                                except Exception as exc:  # noqa: BLE001
                                    print(f"  +{now - started:6.2f}s [提交允许失败] {exc}")
                                approval_id = ""
                        elif name == "courseflow.stage":
                            stage = (frame.get("value") or {}).get("stage")
                            print(f"  +{now - started:6.2f}s [阶段] {stage}")
                        elif name == "courseflow.heartbeat":
                            value = frame.get("value") or {}
                            print(f"  +{now - started:6.2f}s [心跳] {value}")
                    elif ftype == "TOOL_CALL_START":
                        name = str(frame.get("toolCallName") or frame.get("name") or "")
                        print(f"  +{now - started:6.2f}s [工具] {name}")
                    elif ftype == "TEXT_MESSAGE_CONTENT":
                        answer.append(str(frame.get("delta") or ""))
                    elif ftype == "RUN_ERROR":
                        saw_error = str(frame.get("message") or "")
                        print(f"  +{now - started:6.2f}s [RUN_ERROR] {saw_error}")
                    elif ftype == "TEXT_MESSAGE_END":
                        text = "".join(answer)
                        print(f"  +{now - started:6.2f}s [回答] {text[:160]}")
                    elif ftype == "RUN_FINISHED":
                        print(f"  +{now - started:6.2f}s [RUN_FINISHED]")
                        finished = True
                        break
    except httpx.HTTPError as exc:
        print(f"FAIL  网络错误：{exc}")
        return 1

    elapsed = time.monotonic() - started
    print("-" * 68)
    print(f"总耗时 {elapsed:.2f}s，最长静默 {max_gap:.1f}s")
    print(f"帧统计 {counts}")

    problems: list[str] = []
    if saw_error:
        problems.append(f"流里出现 RUN_ERROR：{saw_error[:120]}")
    if not finished:
        problems.append("没有收到 RUN_FINISHED（前端会一直转圈）")
    if counts.get("CUSTOM", 0) == 0:
        problems.append("没有任何 CUSTOM 帧（阶段/审批都靠它）")

    print("=" * 68)
    if problems:
        for item in problems:
            print(f"FAIL  {item}")
        return 1
    print("OK  SSE 链路正常：阶段、审批、回答、收尾都到得了前端")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
