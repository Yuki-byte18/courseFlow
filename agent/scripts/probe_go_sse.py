"""经过 **Go 后端** 的 SSE 探针 —— 这才是浏览器真正走的路。

为什么必须单独有这一个：
    之前的 probe_sse.py 直连 Python 8091，**完全绕过了 Go**。
    而"前端卡住"恰恰是 Go 这一层的 http.Server.WriteTimeout 造成的：
    Go 的 WriteTimeout 是"读完请求头"开始的绝对超时，写入不重置，
    所以一次 30~150 秒的 Agent 运行，流会在超时那一刻被硬切 ——
    后端还在跑、日志还在动，浏览器却再也收不到任何数据。

这个脚本会：
    1. 登录拿 JWT
    2. POST /api/v1/agent/chat（走 Go）
    3. 逐帧读，记录**最后一帧到达的时刻**
    4. 如果流在 RUN_FINISHED 之前就断了，明确报"被掐断"

用法：
    cd agent
    $env:DEEPSEEK_API_KEY = "sk-..."
    $env:GO_URL = "http://127.0.0.1:8080"
    python scripts/probe_go_sse.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

GO_URL = os.environ.get("GO_URL", "http://127.0.0.1:8080").rstrip("/")
USERNAME = os.environ.get("PROBE_USER", "2024002")
PASSWORD = "Passw0rd123"
TASK = os.environ.get("PROBE_TASK", "帮我选 CS109 和 CS110")
MODEL = os.environ.get("PROBE_MODEL", "deepseek-flash")
DEADLINE_S = float(os.environ.get("PROBE_DEADLINE", "180"))


def main() -> int:
    api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        print("请先设置 DEEPSEEK_API_KEY")
        return 1

    print("=" * 68)
    print(f"经过 Go：{GO_URL}   用户={USERNAME}   任务={TASK}")

    try:
        resp = httpx.post(
            f"{GO_URL}/api/v1/auth/login",
            json={"username": USERNAME, "password": PASSWORD},
            timeout=10.0,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  登录失败（Go 起了吗？）：{exc}")
        return 1

    token = body.get("token") or body.get("access_token") or ""
    user = body.get("user") or {}
    if not token:
        print(f"FAIL  登录没拿到 token：{body}")
        return 1
    print(f"  登录 OK  user_id={user.get('user_id')}")

    headers = {
        "Authorization": f"Bearer {token}",
        "X-LLM-Api-Key": api_key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    payload = {"message": TASK, "model": MODEL, "threadId": ""}

    started = time.monotonic()
    last_frame_at = started
    frames = 0
    stages: list[str] = []
    approvals = 0
    finished = False
    error = ""

    try:
        with httpx.Client(
            timeout=httpx.Timeout(connect=10, read=DEADLINE_S, write=30, pool=10)
        ) as client:
            with client.stream(
                "POST", f"{GO_URL}/api/v1/agent/chat", headers=headers, json=payload
            ) as stream:
                if stream.status_code != 200:
                    detail = stream.read().decode("utf-8", "ignore")[:300]
                    print(f"FAIL  HTTP {stream.status_code}: {detail}")
                    return 1
                print("  HTTP 200，开始读流")

                for line in stream.iter_lines():
                    if time.monotonic() - started > DEADLINE_S:
                        print("  [超时] 停止读取")
                        break
                    if not line or not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    try:
                        frame = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    now = time.monotonic()
                    frames += 1
                    last_frame_at = now
                    ftype = str(frame.get("type") or "")

                    if ftype == "CUSTOM":
                        name = str(frame.get("name") or "")
                        value = frame.get("value") or {}
                        if name == "courseflow.stage":
                            stage = str(value.get("stage") or "")
                            if stage:
                                stages.append(stage)
                                print(f"  +{now - started:6.2f}s [阶段] {stage}")
                        elif name == "courseflow.approval_required":
                            approvals += 1
                            print(f"  +{now - started:6.2f}s [审批] {value.get('summary')}")
                            # 走 Go 提交审批，浏览器就是这么做的
                            approval_id = str(value.get("approval_id") or "")
                            if approval_id:
                                try:
                                    r = client.post(
                                        f"{GO_URL}/api/v1/agent/approvals/{approval_id}/decide",
                                        headers={
                                            "Authorization": f"Bearer {token}",
                                            "Content-Type": "application/json",
                                        },
                                        json={"approved": True},
                                        timeout=10.0,
                                    )
                                    print(f"  +{now - started:6.2f}s [已允许] HTTP {r.status_code}")
                                except Exception as exc:  # noqa: BLE001
                                    print(f"  +{now - started:6.2f}s [允许失败] {exc}")
                    elif ftype == "RUN_ERROR":
                        error = str(frame.get("message") or "")
                        print(f"  +{now - started:6.2f}s [RUN_ERROR] {error}")
                    elif ftype == "RUN_FINISHED":
                        print(f"  +{now - started:6.2f}s [RUN_FINISHED]")
                        finished = True
                        break
    except httpx.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"

    total = time.monotonic() - started
    silence = time.monotonic() - last_frame_at

    print("-" * 68)
    print(f"总耗时 {total:.2f}s   帧数 {frames}   阶段 {len(stages)}   审批 {approvals}")
    print(f"最后一帧距今 {silence:.2f}s")
    print("=" * 68)

    if finished:
        print("OK  Go 这一层没有截断流：一直读到 RUN_FINISHED")
        return 0

    # 没收到 RUN_FINISHED：最典型就是被 WriteTimeout 掐断
    print("FAIL  流没有正常结束")
    if total < 20:
        print("      很可能是 Go 的 server.write_timeout 太小（15s）把流掐断了")
    if error:
        print(f"      错误：{error}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
