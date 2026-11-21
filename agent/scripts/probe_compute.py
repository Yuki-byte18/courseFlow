"""用**真实 DeepSeek API** 单独跑一次"计算助手"，定位卡在哪一层。

为什么需要这个脚本：
日志里"查询助手"每次调用都在 0.5 秒内拿到 HTTP 响应，
而"计算助手"那次 `LLM 调用开始` 之后连 httpx 的响应行都没有。
在 uvicorn 里看不出来是网络卡住、还是沙箱卡住、还是别的。
这个脚本把同一段逻辑放到**裸进程**里跑，逐层打时间戳。

用法（把 Key 填进环境变量）：
    cd agent
    $env:DEEPSEEK_API_KEY = "sk-..."
    python scripts/probe_compute.py

它会依次报告：
  1. 模型是否可用（/models）
  2. 计算助手的第一次调用耗时多久、返回什么
  3. 如果它要求 execute_code，沙箱能否跑起来、耗时多久
  4. 整体是否有任何一步超过预期

任何一步卡住，最后打印的就是卡住的那一步。
"""

from __future__ import annotations

import asyncio
import faulthandler
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 90 秒还没跑完就把所有线程栈打出来（含每个线程卡在哪一行）
faulthandler.dump_traceback_later(90, exit=True)

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.events import EventKind  # noqa: E402
from app.execenv.runtime import Runtime, UserContext  # noqa: E402
from app.llm.client import DeepSeekClient  # noqa: E402
from app.orchestration.agents import build_agents, get_agent  # noqa: E402
from app.tools import business  # noqa: F401,E402
from app.tools.policy import POLICY  # noqa: E402
from app.tools.registry import REGISTRY  # noqa: E402


def step(label: str) -> float:
    print(f"  [{label}]")
    return time.monotonic()


def report(label: str, started: float) -> None:
    print(f"    → {time.monotonic() - started:.2f}s")


async def main() -> int:
    api_key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        print("请先设置 DEEPSEEK_API_KEY 环境变量（用你自己在页面上配置的那个 Key）")
        return 1

    settings = get_settings()
    print("=" * 64)
    print(f"模型偏好 {settings.default_model}，单次调用上限 {settings.request_timeout_s}s")

    # ---- 1. Key 与模型列表 ----
    # 注意：模型校验原本写在 Go 那边（internal/agent/llm.go 的 VerifyKey），
    # Python 的 DeepSeekClient 只有 chat / chat_json / chat_stream，没有 list_models。
    # 所以这里直接用 httpx 打 /models，别去调不存在的方法。
    started = step("1. 校验 Key 与可用模型")
    model = settings.default_model
    try:
        resp = httpx.get(
            f"{settings.deepseek_base_url}/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        if resp.status_code >= 400:
            print(f"    /models 返回 {resp.status_code}：{resp.text[:160]}")
            print("    先继续用默认模型试，后面要是 401 就说明 Key 不对")
        else:
            ids = [str(m.get("id")) for m in (resp.json().get("data") or [])]
            chat_ids = [
                i
                for i in ids
                if "chat" in i or i.startswith("deepseek")
            ]
            print(f"    可用模型 {len(ids)} 个：{ids[:6]}")
            if settings.default_model in ids:
                model = settings.default_model
            elif chat_ids:
                model = sorted(chat_ids)[0]
            print(f"    本次使用 {model}")
    except Exception as exc:  # noqa: BLE001
        print(f"    /models 请求失败（{exc}），先继续用默认模型 {model}")
    report("校验", started)

    # ---- 2. 直接发一次带工具的请求，量延迟 ----
    started = step(f"2. 带工具的直接请求（model={model}）")
    client = DeepSeekClient(api_key, timeout_s=settings.request_timeout_s)
    scopes = ("offering:read", "enrollment:read", "enrollment:create", "schedule:read")
    decision = POLICY.decide(agent="compute", scopes=scopes)
    try:
        completion = await client.chat(
            model=model,
            messages=[
                {"role": "system", "content": "你是计算助手。只输出一句话。"},
                {"role": "user", "content": "念一遍你能用的工具名。"},
            ],
            tools=decision.tools_schema,
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"    FAIL 带工具请求失败：{exc}")
        print("    如果提示 Key 无效/无权限，先把 DEEPSEEK_API_KEY 换成页面上那个真的")
        return 1
    print(f"    content={completion.content[:80]!r} tool_calls={len(completion.tool_calls)}")
    report("带工具请求", started)

    # ---- 3. 沙箱能否起来 ----
    started = step("3. 沙箱执行")
    from app.tools.codeact import CODEACT
    from app.tools.context import ToolContext

    user_row = db.fetch_one("SELECT id, username FROM users WHERE username = '2024001'")
    if not user_row:
        print("    FAIL 找不到 2024001，先跑 go run ./cmd/seedcampus")
        return 1

    sandbox_names = frozenset(t.name for t in REGISTRY.all() if t.visible_in_sandbox())
    ctx = ToolContext(
        run_id="probe",
        user_id=int(user_row["id"]),
        username=user_row["username"],
        scopes=scopes,
        agent="compute",
        sandbox_tool_names=sandbox_names,
    )
    outcome = await CODEACT.execute(
        ctx,
        # 直接用顶层字段（现在 payload 和顶层都能取到，两种写法都成立）
        "rows = call_tool('my_enrollments', {})\n"
        "print('已选', len(rows['offerings']), '门')\n"
        "print('键名', sorted(rows.keys()))",
        "探针",
        timeout_s=settings.sandbox_timeout_s,
        max_output_chars=2000,
        max_code_runs=settings.max_code_runs,
        code_runs_used=0,
    )
    print(f"    ok={outcome.ok} output={str(outcome.preview)[:80]!r} err={outcome.error[:80]}")
    report("沙箱", started)
    if not outcome.ok:
        print("    FAIL 沙箱这一步有问题，这就是「计算卡死」的原因")
        return 1

    # ---- 4. 完整跑一次计算助手（真模型 + 真工具 + 真沙箱）----
    started = step("4. 完整计算助手（真模型闭环）")
    runtime = Runtime(
        goal="计算助手探针",
        user=UserContext(
            user_id=int(user_row["id"]),
            username=user_row["username"],
            real_name="",
            scopes=scopes,
            token="",
        ),
        model=model,
        api_key=api_key,
        settings=settings,
        thread_id="",
    )
    runtime.start(title="探针：计算助手")

    # get_agent 依赖一个全局注册表，必须先 build_agents 才能取到
    # （应用里是 create_app 时调的；裸进程里要自己调一次）
    build_agents(settings)
    agent = get_agent("compute")
    if agent is None:
        print("    FAIL 取不到 compute 子 agent，build_agents 没成功")
        return 1
    try:
        result = await asyncio.wait_for(
            agent.run(
                runtime,
                goal="统计我这学期已选课程的学分总数，并说明离上限还差多少",
                observations="（无）",
                history=[],
            ),
            timeout=80.0,
        )
        print(f"    ok={result.ok} 工具调用 {result.tool_calls} 次")
        print(f"    summary={result.summary[:160]!r}")
    except asyncio.TimeoutError:
        print("    FAIL 计算助手 80 秒内没有结束")
        dump_events(runtime)
        return 1
    except Exception:  # noqa: BLE001
        import traceback

        print("    FAIL 计算助手抛异常：")
        for line in traceback.format_exc().splitlines()[-14:]:
            print(f"      {line}")
        dump_events(runtime)
        return 1
    report("计算助手闭环", started)

    dump_events(runtime)
    print("=" * 64)
    print("OK  计算链路（模型 → 工具 → 沙箱）在本机可以跑通")
    return 0


def dump_events(runtime: Runtime) -> None:
    events = runtime.bus.history_snapshot()
    kinds: dict[str, int] = {}
    for event in events:
        kinds[event["kind"]] = kinds.get(event["kind"], 0) + 1
    print(f"    事件：{kinds}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
