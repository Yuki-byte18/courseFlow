"""写路径端到端测试：真的通过 Go 后端退课 + 选课。

测什么：
  1. 审批通过后，工具带用户 token 回调 Go 的 `/my/enrollments` 能通
  2. **业务失败必须让工具报 ok=False**（否则模型会谎称"已经帮你选上了"）

为什么是"退课 → 再选回"：
2024001 已经 29/30 学分，选新课会被学分上限拒绝。退一门再选回，
既覆盖两个写工具，又能把数据库恢复到原样。

前置：Go 后端必须已经启动（go run ./cmd/api）。
用法：
    cd agent
    python tests/test_write_path.py
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.execenv.runtime import Runtime, UserContext  # noqa: E402
from app.llm.client import Completion, ToolCall  # noqa: E402
from app.orchestration.supervisor import Supervisor  # noqa: E402
from app.tools import business  # noqa: F401,E402

USERNAME = "2024001"
PASSWORD = "Passw0rd123"
DEADLINE_S = 30.0


def login() -> tuple[str, int, str]:
    resp = httpx.post(
        f"{get_settings().go_base_url}/api/v1/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=10.0,
    )
    resp.raise_for_status()
    body = resp.json()
    token = body.get("token") or body.get("access_token")
    user = body.get("user") or {}
    if not token:
        raise SystemExit(f"登录没拿到 token：{body}")
    return token, int(user.get("user_id")), str(user.get("real_name") or "")


def _system_of(messages: list[dict]) -> str:
    return " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")


def _last_role(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role"):
            return str(message["role"])
    return ""


class FakeClient:
    """让 execute 子 agent 点名调用指定的写工具。"""

    def __init__(self, tool_name: str, offering_id: int) -> None:
        self.tool_name = tool_name
        self.offering_id = offering_id

    async def chat(self, *, model, messages, tools=None, temperature=0.2, **kwargs) -> Completion:
        system = _system_of(messages)

        if "意图路由层的**识别节点**" in system:
            return Completion(
                content=json.dumps(
                    {
                        "intent": "write",
                        "confidence": 0.96,
                        "reason": "写操作",
                        "slots": {"action": "enroll"},
                    },
                    ensure_ascii=False,
                )
            )
        if "粗粒度任务图" in system:
            return Completion(
                content=json.dumps(
                    {"nodes": [{"id": "n1", "agent": "execute", "goal": "执行写操作", "deps": []}]},
                    ensure_ascii=False,
                )
            )
        if "复盘" in system:
            return Completion(content=json.dumps({"action": "finish", "reason": "已完成"}))
        if "整理成给用户的回答" in system:
            return Completion(content="操作完成。")

        if _last_role(messages) == "tool":
            return Completion(content="已处理。")

        if "执行助手" in system:
            return Completion(
                tool_calls=[
                    ToolCall(
                        id="call_w1",
                        name=self.tool_name,
                        arguments={"offering_id": self.offering_id},
                    )
                ]
            )
        return Completion(content="好。")

    async def chat_json(self, *, model, messages, temperature=0.0, max_tokens=800) -> dict:
        completion = await self.chat(model=model, messages=messages, json_mode=True)
        try:
            parsed = json.loads(completion.content or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def chat_stream(self, *, model, messages, **kwargs):
        for chunk in ("操作", "完成。"):
            yield chunk


async def run_once(
    *, token: str, user_id: int, real_name: str, tool_name: str, offering_id: int
) -> tuple[list, float]:
    # **故意把墙钟压到 1 秒**，而下面的审批要等 2.5 秒。
    #
    # 这是为了复现一个真实 bug：审批等待被算进了运行墙钟，
    # 用户等几十秒点一下"允许"，下一条语句立刻判定"运行超时"→ 节点失败。
    # 表现就是"卡在执行然后失败"。
    # 修好之后（Budget.pause 把审批时间扣掉），这个用例必须通过。
    settings = dataclasses.replace(get_settings(), max_wall_seconds=1.0, approval_timeout_s=15.0)
    runtime = Runtime(
        goal=f"执行 {tool_name}",
        user=UserContext(
            user_id=user_id,
            username=USERNAME,
            real_name=real_name,
            scopes=("enrollment:create", "enrollment:drop", "enrollment:read", "offering:read"),
            token=token,
        ),
        model="fake-model",
        api_key="sk-fake-for-test-only",
        settings=settings,
        thread_id="",
    )
    runtime.client = FakeClient(tool_name, offering_id)  # type: ignore[assignment]

    async def slow_approve(tool: str, args: dict, description: str) -> bool:
        # 模拟"用户想了 2.5 秒才点允许"
        await asyncio.sleep(2.5)
        return True

    runtime.approve = slow_approve  # type: ignore[assignment]

    started = time.monotonic()
    await asyncio.wait_for(
        Supervisor(settings).run(runtime, message="执行写操作", history=[]),
        timeout=DEADLINE_S,
    )
    elapsed = time.monotonic() - started

    events = runtime.bus.history_snapshot()
    db.execute("DELETE FROM agent_messages WHERE thread_id = %s", (runtime.thread_id,))
    db.execute("DELETE FROM agent_threads WHERE thread_id = %s", (runtime.thread_id,))
    return events, elapsed


def active_count(user_id: int, offering_id: int) -> int:
    row = db.fetch_one(
        "SELECT COUNT(*) AS n FROM enrollments WHERE student_id = %s AND offering_id = %s AND status = 1",
        (user_id, offering_id),
    )
    return int(row["n"])


def pick_enrolled(user_id: int) -> tuple[int, str]:
    row = db.fetch_one(
        """
        SELECT o.id AS id, c.name AS name
          FROM enrollments e
          JOIN offerings o ON o.id = e.offering_id
          JOIN courses c ON c.id = o.course_id
         WHERE e.student_id = %s AND e.status = 1
         ORDER BY o.id
         LIMIT 1
        """,
        (user_id,),
    )
    if not row:
        raise SystemExit("2024001 没有任何在选课程，先跑 go run ./cmd/seedcampus")
    return int(row["id"]), str(row["name"])


def tool_results(events: list) -> list[dict]:
    return [e for e in events if e["kind"] == "tool_result"]


def main() -> int:
    ok, error = db.ping()
    if not ok:
        print(f"数据库连接失败：{error}")
        return 1

    print("=" * 62)
    try:
        token, user_id, real_name = login()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  登录失败（Go 后端起了吗？）：{exc}")
        return 1

    offering_id, course_name = pick_enrolled(user_id)
    print(f"测试班次 {offering_id}（{course_name}）")

    failures: list[str] = []
    taken_before = db.fetch_one(
        "SELECT taken FROM offerings WHERE id = %s", (offering_id,)
    )["taken"]

    # ---- 1. 退课 ----
    try:
        events, elapsed = asyncio.run(
            run_once(
                token=token, user_id=user_id, real_name=real_name,
                tool_name="drop_offering", offering_id=offering_id,
            )
        )
    except asyncio.TimeoutError:
        print(f"FAIL  退课超过 {DEADLINE_S:.0f}s 未结束")
        return 1

    after_drop = active_count(user_id, offering_id)
    result = (tool_results(events) or [{}])[0]
    print(f"  [退课] ok={result.get('ok')} 耗时 {elapsed:.2f}s 记录 {after_drop}")
    if after_drop != 0:
        failures.append(f"退课没生效（在选记录 {after_drop}）")
    if not result.get("ok"):
        failures.append(f"退课被报失败：{result.get('preview')}")

    # ---- 2. 再选回来 ----
    try:
        events2, elapsed2 = asyncio.run(
            run_once(
                token=token, user_id=user_id, real_name=real_name,
                tool_name="enroll_offering", offering_id=offering_id,
            )
        )
    except asyncio.TimeoutError:
        print(f"FAIL  选课超过 {DEADLINE_S:.0f}s 未结束")
        return 1

    after_enroll = active_count(user_id, offering_id)
    result2 = (tool_results(events2) or [{}])[0]
    taken_after = db.fetch_one(
        "SELECT taken FROM offerings WHERE id = %s", (offering_id,)
    )["taken"]
    print(f"  [选课] ok={result2.get('ok')} 耗时 {elapsed2:.2f}s 记录 {after_enroll} taken {taken_before}->{taken_after}")
    if after_enroll != 1:
        failures.append(f"选课没生效（在选记录 {after_enroll}）")
    if not result2.get("ok"):
        failures.append(f"选课被报失败：{result2.get('preview')}")
    if taken_after != taken_before:
        failures.append(f"taken 没回到原值（{taken_before} -> {taken_after}）")

    # ---- 3. 业务失败必须报 ok=False ----
    # 用一个不存在或已满的班次，确认工具层不会把它当成成功
    print("-" * 62)
    print("[业务失败传播] 用一个必然失败的班次（学分上限用尽/不存在）")
    try:
        events3, _ = asyncio.run(
            run_once(
                token=token, user_id=user_id, real_name=real_name,
                tool_name="enroll_offering", offering_id=999_999,
            )
        )
        result3 = (tool_results(events3) or [{}])[0]
        print(f"  [失败用例] ok={result3.get('ok')} preview={str(result3.get('preview'))[:70]}")
        if result3.get("ok") is not False:
            failures.append("业务失败被当成工具成功（模型会谎称已完成）")
    except asyncio.TimeoutError:
        failures.append("失败用例超时")

    print("=" * 62)
    if failures:
        for item in failures:
            print(f"FAIL  {item}")
        return 1

    print("OK  写路径通：审批 → 带 token 回调 Go → 真落库；业务失败也报 ok=False")
    print("    （审批等了 2.5s 而墙钟只有 1s，仍然成功 —— 审批时间已从墙钟扣除）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
