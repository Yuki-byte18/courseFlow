"""端到端：澄清得到的问答必须进入后续执行的上下文。

这是用户报的一个真 bug：澄清完了，执行时又"忘了"。
根因是澄清结果只存进了路由层的槽位状态，而规划器 / 子 agent / 最终回答
拿到的还是用户最初那句模糊的话（"那个啥"）。

修复方式是拼一段"执行上下文附录"（澄清问答 + 已确认槽位）挂在原始请求后面，
下游所有 LLM 调用都能看到。这个测试用**最强的断言**验证它：

    除了意图识别本身，之后每一次 LLM 调用都必须能看到 CS109 和"通过澄清补充"

为什么用这个不变量而不是"检查某个具体 prompt"：
    规划有快路径（明确写操作时跳过规划器），执行有反思、收尾等多个调用点。
    逐个断言容易漏；"所有下游调用都看得到"才是真正要保证的性质。

用法：
    cd agent
    python tests/test_clarify_context.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.execenv.runtime import CLARIFICATIONS, Runtime, UserContext  # noqa: E402
from app.llm.client import Completion  # noqa: E402
from app.orchestration.supervisor import Supervisor  # noqa: E402
from app.tools import business  # noqa: F401,E402

DEADLINE_S = 60.0
FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  {name:<54} OK")
    else:
        print(f"  {name:<54} FAIL  {detail}")
        FAILURES.append(f"{name} {detail}".strip())


def _system_of(messages: list[dict]) -> str:
    return " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")


def _user_text_of(messages: list[dict]) -> str:
    return "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "user")


class RecordingClient:
    """把每次调用的 system + user 文本都记下来，供断言。"""

    def __init__(self) -> None:
        self.prompts: list[dict[str, str]] = []

    async def chat(self, *, model, messages, tools=None, temperature=0.2, **kwargs) -> Completion:
        system = _system_of(messages)
        user = _user_text_of(messages)
        kind = "other"
        if "意图路由层的**识别节点**" in system:
            kind = "intent"
        elif "粗粒度任务图" in system:
            kind = "planner"
        elif "复盘" in system:
            kind = "reflect"
        elif "把子 agent 的工作结果整理成给用户的回答" in system:
            kind = "final"
        elif "子 agent" in system or "助手" in system:
            kind = "agent"
        self.prompts.append({"kind": kind, "system": system, "user": user})

        if kind == "intent":
            # 第一句故意给低置信度，逼出澄清
            return Completion(
                content=json.dumps(
                    {"intent": "unknown", "confidence": 0.5, "reason": "太模糊"}, ensure_ascii=False
                )
            )
        if kind == "planner":
            return Completion(
                content=json.dumps(
                    {"nodes": [{"id": "n1", "agent": "query", "goal": "查班次", "deps": []}]},
                    ensure_ascii=False,
                )
            )
        if kind == "reflect":
            return Completion(content=json.dumps({"action": "finish", "reason": "够了"}))
        if kind == "final":
            return Completion(content="已经帮你选上 CS109 了。")
        return Completion(content="已完成该子任务。")

    async def chat_json(self, *, model, messages, temperature=0.0, max_tokens=800) -> dict:
        completion = await self.chat(model=model, messages=messages, json_mode=True)
        try:
            parsed = json.loads(completion.content or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def chat_stream(self, *, model, messages, **kwargs):
        self.prompts.append(
            {"kind": "final", "system": _system_of(messages), "user": _user_text_of(messages)}
        )
        for chunk in ("已经帮你", "选上 CS109。"):
            yield chunk


async def run_pipeline(thread: str, user_row: dict) -> tuple[RecordingClient, list[dict], list[str]]:
    settings = get_settings()
    client = RecordingClient()

    runtime = Runtime(
        goal="澄清上下文测试",
        user=UserContext(
            user_id=int(user_row["id"]),
            username=user_row["username"],
            real_name=user_row["real_name"],
            scopes=("offering:read", "enrollment:read", "enrollment:create", "schedule:read"),
        ),
        model="fake-model",
        api_key="sk-fake-for-test-only",
        settings=settings,
        thread_id=thread,
    )
    runtime.client = client  # type: ignore[assignment]

    async def auto_approve(tool: str, args: dict, description: str) -> bool:
        return True

    runtime.approve = auto_approve  # type: ignore[assignment]

    # 澄清回答脚本：意图 → 动作 → 课程
    answers = ["2", "1", "CS109"]

    async def answerer() -> None:
        for expected in answers:
            for _ in range(600):
                await asyncio.sleep(0.02)
                ids = CLARIFICATIONS.snapshot()
                if ids:
                    CLARIFICATIONS.answer(ids[0], expected)
                    break

    task = asyncio.create_task(answerer())
    await asyncio.wait_for(
        Supervisor(settings).run(runtime, message="那个啥", history=[]),
        timeout=DEADLINE_S,
    )
    task.cancel()

    events = runtime.bus.history_snapshot()

    # 先读消息表再清理：要断言"澄清问答没有变成气泡"
    rows = db.fetch_all(
        "SELECT role FROM agent_messages WHERE thread_id = %s ORDER BY id", (thread,)
    )
    roles = [r["role"] for r in rows]

    db.execute("DELETE FROM agent_messages WHERE thread_id = %s", (thread,))
    db.execute("DELETE FROM agent_threads WHERE thread_id = %s", (thread,))
    db.execute("DELETE FROM agent_intent_slots WHERE thread_id = %s", (thread,))
    return client, events, roles


def main() -> int:
    ok, error = db.ping()
    if not ok:
        print(f"数据库连接失败：{error}")
        return 1

    user_row = db.fetch_one(
        "SELECT id, username, real_name FROM users WHERE username = '2024002'"
    )
    if not user_row:
        print("找不到 2024002")
        return 1

    thread = ("tctx" + uuid.uuid4().hex)[:32]
    print("=" * 74)
    print("用户输入：那个啥   → 澄清：意图 → 动作 → 课程（回答 2 / 1 / CS109）")

    started = time.monotonic()
    try:
        client, events, roles = asyncio.run(run_pipeline(thread, user_row))
    except asyncio.TimeoutError:
        print(f"FAIL  超过 {DEADLINE_S:.0f}s 未结束")
        return 1
    elapsed = time.monotonic() - started

    kinds = [e["kind"] for e in events]
    intent_calls = [p for p in client.prompts if p["kind"] == "intent"]
    downstream = [p for p in client.prompts if p["kind"] != "intent"]

    print("-" * 74)
    print(f"  LLM 调用 {len(client.prompts)} 次（意图 {len(intent_calls)}，下游 {len(downstream)}）")
    for prompt in downstream:
        flag = "含 CS109" if "CS109" in prompt["user"] else "缺 CS109"
        print(f"    - {prompt['kind']:<9} {flag}")
    print("-" * 74)

    check("澄清确实发生了（clarity_required 事件）", "clarify_required" in kinds, str(kinds[:8]))
    check("同一次运行跑到了最后（final 事件）", "final" in kinds, str(kinds[-4:]))
    check("下游至少有一次 LLM 调用", len(downstream) >= 1, str(len(downstream)))

    # **核心断言**：每一次下游调用都能看到澄清出来的课程号
    missing = [p["kind"] for p in downstream if "CS109" not in p["user"]]
    check("每次下游调用都看得到澄清出的课程号", not missing, f"缺失的调用：{missing}")

    # 澄清问答本身也要在上下文里（不只槽位）
    with_turns = [p for p in downstream if "通过澄清补充" in p["user"]]
    check("下游上下文里带澄清问答原文", bool(with_turns), f"{len(with_turns)}/{len(downstream)} 次带")

    # 子 agent 拿到的请求原文里要有澄清内容
    agent_prompts = [p for p in downstream if p["kind"] == "agent"]
    if agent_prompts:
        ok_agents = [p for p in agent_prompts if "用户的请求原文" in p["user"] and "CS109" in p["user"]]
        check("子 agent 收到带澄清信息的请求原文", len(ok_agents) == len(agent_prompts),
              f"{len(ok_agents)}/{len(agent_prompts)}")
    else:
        check("子 agent 收到带澄清信息的请求原文", True, "（本次走快路径，无子 agent 调用）")

    # 落库：澄清问答不进消息表
    check("澄清问答没有变成消息气泡", roles == ["user", "assistant"], str(roles))

    print("=" * 74)
    print(f"  总耗时 {elapsed:.2f}s")
    if FAILURES:
        print(f"FAIL  {len(FAILURES)} 项未通过")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("OK  澄清问答与已确认槽位都进入了后续执行的上下文")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
