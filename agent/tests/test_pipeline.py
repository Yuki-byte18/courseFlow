"""端到端回放：用假 LLM 驱动整条链路。

为什么需要它：
真实运行要花 DeepSeek 的钱和时间，而且不可复现 —— "卡在计算那里"这种问题
没法靠反复试来定位。这里用一个脚本化的假模型，把
意图识别 → 建图 → 子 agent → 工具 → 沙箱 → 反思 → 收尾 全跑一遍，
**确定性**地验证编排链路不会卡住。

它会真的读写数据库（agent_runs / agent_steps / agent_threads），
所以同时也在测 tracer 和对话持久化。跑完会清掉自己造的会话。

用法：
    cd agent
    python tests/test_pipeline.py
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.events import EventKind  # noqa: E402
from app.execenv.runtime import Runtime, UserContext  # noqa: E402
from app.llm.client import Completion, ToolCall  # noqa: E402
from app.orchestration.supervisor import Supervisor  # noqa: E402
from app.tools import business  # noqa: F401,E402

DEADLINE_S = 45.0


def _system_of(messages: list[dict]) -> str:
    return " ".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")


def _last_role(messages: list[dict]) -> str:
    for message in reversed(messages):
        if message.get("role"):
            return str(message["role"])
    return ""


class FakeClient:
    """脚本化的假模型。

    它按 system prompt 里的标记判断"现在是哪一步"，
    然后返回该步应有的输出 —— 这样整条链路就能确定性地跑完。
    """

    def __init__(self) -> None:
        self.calls = 0

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
        tool_choice: str | None = None,
    ) -> Completion:
        self.calls += 1
        system = _system_of(messages)

        # 1. 意图识别（意图路由层的识别节点输出结构化信号）
        if "意图路由层的**识别节点**" in system:
            return Completion(
                content=json.dumps(
                    {
                        "intent": "query_compute",
                        "confidence": 0.96,
                        "reason": "查课并算冲突",
                        "slots": {"action": "search", "course_codes": ["CS109", "CS110"]},
                    },
                    ensure_ascii=False,
                )
            )

        # 2. 建图
        if "粗粒度任务图" in system:
            return Completion(
                content=json.dumps(
                    {
                        "nodes": [
                            {"id": "n1", "agent": "query", "goal": "查 CS109/CS110 班次", "deps": []},
                            {"id": "n2", "agent": "compute", "goal": "算时间冲突与学分", "deps": ["n1"]},
                        ]
                    },
                    ensure_ascii=False,
                )
            )

        # 3. 反思
        if "复盘" in system:
            return Completion(content=json.dumps({"action": "finish", "reason": "信息够了"}))

        # 4. 最终回答
        if "把子 agent 的工作结果整理成给用户的回答" in system:
            return Completion(content="CS109 和 CS110 时间不冲突，可以选。")

        # 5. 子 agent：第一轮点工具，第二轮给结论
        has_tool_result = _last_role(messages) == "tool"
        if has_tool_result:
            return Completion(content="已完成该子任务。")

        if "查询助手" in system:
            return Completion(
                tool_calls=[
                    ToolCall(
                        id="call_q1",
                        name="search_offerings",
                        arguments={"keyword": "CS1", "limit": 5},
                    )
                ]
            )

        if "计算助手" in system:
            code = (
                "rows = call_tool('my_enrollments', {})\n"
                "print(f'已选 {len(rows[\"payload\"][\"offerings\"])} 门课')\n"
            )
            return Completion(
                tool_calls=[ToolCall(id="call_c1", name="execute_code", arguments={"code": code})]
            )

        return Completion(content="好的。")

    async def chat_json(
        self, *, model: str, messages: list[dict], temperature: float = 0.0, max_tokens: int = 800
    ) -> dict:
        completion = await self.chat(model=model, messages=messages, json_mode=True)
        try:
            parsed = json.loads(completion.content or "{}")
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    async def chat_stream(self, *, model: str, messages: list[dict], **kwargs):
        for chunk in ("CS109 和 CS110 ", "时间不冲突，", "两门都能选。"):
            yield chunk


async def run_pipeline() -> tuple[list[dict], float]:
    settings = get_settings()
    user_row = db.fetch_one("SELECT id, username, real_name FROM users WHERE username = '2024001'")
    if not user_row:
        raise SystemExit("找不到 2024001，先跑 go run ./cmd/seedcampuse")

    runtime = Runtime(
        goal="查询 CS109 与 CS110 的班次并确认能否选课",
        user=UserContext(
            user_id=int(user_row["id"]),
            username=user_row["username"],
            real_name=user_row["real_name"],
            scopes=("offering:read", "enrollment:read", "enrollment:create", "schedule:read"),
        ),
        model="fake-model",
        api_key="sk-fake-for-test-only",
        settings=settings,
        thread_id="",
    )
    # 换成假模型；其余（工具、沙箱、治理、tracer）全是真实实现
    runtime.client = FakeClient()  # type: ignore[assignment]

    # 写操作自动批准（本测试不弹审批面板）
    async def auto_approve(tool: str, args: dict, description: str) -> bool:
        return True

    runtime.approve = auto_approve  # type: ignore[assignment]

    started = time.monotonic()
    await asyncio.wait_for(
        Supervisor(settings).run(runtime, message="CS109 和 CS110 能一起选吗", history=[]),
        timeout=DEADLINE_S,
    )
    elapsed = time.monotonic() - started

    events = runtime.bus.history_snapshot()
    db.execute("DELETE FROM agent_messages WHERE thread_id = %s", (runtime.thread_id,))
    db.execute("DELETE FROM agent_threads WHERE thread_id = %s", (runtime.thread_id,))
    return events, elapsed


def main() -> int:
    ok, error = db.ping()
    if not ok:
        print(f"数据库连接失败：{error}")
        return 1

    print("=" * 62)
    print(f"假模型驱动完整链路，硬超时 {DEADLINE_S:.0f}s")

    try:
        events, elapsed = asyncio.run(run_pipeline())
    except asyncio.TimeoutError:
        print(f"FAIL  超过 {DEADLINE_S:.0f}s 仍未结束 —— 链路里有人卡住了")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  运行抛异常：{exc}")
        return 1

    kinds: dict[str, int] = {}
    for event in events:
        kinds[event["kind"]] = kinds.get(event["kind"], 0) + 1

    order = [
        "run_started", "stage", "intent", "graph", "node_status",
        "agent_started", "tool_call", "tool_result", "code_run", "code_output",
        "agent_finished", "delta", "final", "error", "run_finished",
    ]
    print("-" * 62)
    for kind in order:
        if kind in kinds:
            print(f"  {kind:<16} x{kinds[kind]}")
    print("-" * 62)

    failures: list[str] = []

    if kinds.get("error"):
        errors = [e.get("message") for e in events if e["kind"] == "error"]
        failures.append(f"出现 error 事件：{errors[:2]}")

    if not kinds.get("final"):
        failures.append("没有 final 事件（用户拿不到回答）")

    if not kinds.get("run_finished"):
        failures.append("没有 run_finished 事件（SSE 会一直挂着）")

    # compute 节点必须真的执行了沙箱
    if not kinds.get("code_run"):
        failures.append("compute 节点没有调用 execute_code（本用例应当走代码通道）")

    final = next((e for e in events if e["kind"] == "final"), None)
    print(f"  最终回答：{(final or {}).get('content', '')[:70]}")
    print(f"  总耗时：{elapsed:.2f}s")
    print("=" * 62)

    if failures:
        for item in failures:
            print(f"FAIL  {item}")
        return 1

    print("OK  编排链路完整跑通：建图 → 子 agent → 工具/沙箱 → 反思 → 收尾")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
