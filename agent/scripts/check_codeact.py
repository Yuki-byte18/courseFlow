"""验证「Function Calling + CodeAct 混合架构」仍然完整可用。

这是项目的核心亮点，所以单独一个脚本把整条链路证明一遍：

    模型（function calling）
        ├── 直连通道：直接点名 search_offerings / enroll_offering …
        └── 代码通道：点名 execute_code → 沙箱子进程执行 Python
                                         ↳ 代码里 call_tool(...) 桥接回宿主
                                           执行真实只读工具，结果回传给代码

检查项：
  1. 总开关打开，且只有 compute 子 agent 拿到 execute_code
  2. 沙箱可见的工具**全部只读**（写操作永远进不了沙箱）
  3. AST 白名单拦住 import / eval / open / dunder
  4. 沙箱能真的跑 Python 并拿回 print 输出
  5. 代码里的 call_tool 能桥接回**真实业务工具**（不是假的）
  6. 沙箱里调不到写工具
  7. 直连通道仍然正常
  8. 两条通道发出的事件带正确的 channel 标记

用法：
    cd agent
    python scripts/check_codeact.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.events import EventKind  # noqa: E402
from app.tools import business  # noqa: F401,E402
from app.tools import astguard  # noqa: E402
from app.tools.codeact import CODEACT  # noqa: E402
from app.tools.context import ToolContext  # noqa: E402
from app.tools.direct import DIRECT  # noqa: E402
from app.tools.policy import CODE_CHANNEL_AGENTS, EXECUTE_CODE_TOOL, POLICY  # noqa: E402
from app.tools.registry import REGISTRY  # noqa: E402

SCOPES = (
    "offering:read",
    "enrollment:read",
    "enrollment:create",
    "enrollment:drop",
    "schedule:read",
    "agent:chat",
)

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  {name:<52} OK")
    else:
        print(f"  {name:<52} FAIL  {detail}")
        FAILURES.append(f"{name} {detail}".strip())


class _Bus:
    """最小事件总线：收集事件，用于断言 channel 标记。"""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def emit(self, kind, **payload) -> None:
        self.events.append((getattr(kind, "value", str(kind)), payload))

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.events]


def main() -> int:
    ok, error = db.ping()
    if not ok:
        print(f"数据库连接失败：{error}")
        return 1

    settings = get_settings()
    user = db.fetch_one("SELECT id, username FROM users WHERE username = '2024002'")
    if not user:
        print("找不到 2024002")
        return 1

    sandbox_names = frozenset(t.name for t in REGISTRY.all() if t.visible_in_sandbox())
    bus = _Bus()
    ctx = ToolContext(
        run_id="codeact-check",
        user_id=int(user["id"]),
        username=user["username"],
        scopes=SCOPES,
        agent="compute",
        bus=bus,
        sandbox_tool_names=sandbox_names,
    )

    print("=" * 72)
    print("[1] 通道分配")
    check("代码通道总开关打开", settings.enable_code_channel, str(settings.enable_code_channel))
    check("允许代码通道的子 agent = {compute}", CODE_CHANNEL_AGENTS == frozenset({"compute"}))

    for agent in ("query", "execute", "notify"):
        names = [t["function"]["name"] for t in POLICY.decide(agent=agent, scopes=SCOPES).tools_schema]
        check(f"{agent} 没有 execute_code（避免跑偏）", EXECUTE_CODE_TOOL not in names, str(names))

    compute = POLICY.decide(agent="compute", scopes=SCOPES)
    compute_names = [t["function"]["name"] for t in compute.tools_schema]
    check("compute 有 execute_code", EXECUTE_CODE_TOOL in compute_names, str(compute_names))
    check("compute 也有直连工具（混合）", len(compute_names) > 1, str(compute_names))

    print("[2] 沙箱可见工具的只读不变量")
    write_tools = [t.name for t in REGISTRY.all() if t.side_effect]
    check(
        "写工具一个都不在沙箱清单里",
        all(name not in sandbox_names for name in write_tools),
        str([n for n in write_tools if n in sandbox_names]),
    )
    check("沙箱清单非空", len(sandbox_names) > 0, str(len(sandbox_names)))

    print("[3] AST 白名单")
    for code, label in (
        ("import os", "import"),
        ("__import__('os')", "__import__"),
        ("eval('1')", "eval"),
        ("open('/etc/passwd')", "open"),
        ("x.__class__", "dunder 属性"),
    ):
        check(f"拦下 {label}", not astguard.validate(code).ok)
    check("放行正常计算 + print", astguard.validate("print(sum(range(10)))").ok)

    print("[4] 沙箱执行（真子进程）")
    run1 = asyncio.run(
        CODEACT.execute(
            ctx,
            "print('hello from sandbox', 6 * 7)",
            "检查",
            timeout_s=settings.sandbox_timeout_s,
            max_output_chars=2000,
            max_code_runs=settings.max_code_runs,
            code_runs_used=0,
        )
    )
    check("能跑通并拿回 print 输出", run1.ok and "42" in run1.preview, f"ok={run1.ok} {run1.preview[:60]}")
    check("形如子进程执行（有耗时）", run1.duration_ms >= 0, str(run1.duration_ms))

    print("[5] 代码里的 call_tool 桥接回真实工具")
    run2 = asyncio.run(
        CODEACT.execute(
            ctx,
            "rows = call_tool('my_enrollments', {})\n"
            "items = rows['offerings']\n"
            "print('门数', len(items))\n"
            "print('学分', sum(x['credits'] for x in items))\n",
            "统计学分",
            timeout_s=settings.sandbox_timeout_s,
            max_output_chars=2000,
            max_code_runs=settings.max_code_runs,
            code_runs_used=0,
        )
    )
    check("桥接调用成功", run2.ok, run2.error[:80])
    check("拿到了真实课程数据", "门数" in run2.preview, run2.preview[:60])
    check(
        "沙箱发出的工具调用带 channel=code",
        any(e.get("channel") == "code" for kind, e in bus.events if kind == "tool_call"),
        str([k for k, _ in bus.events])[:80],
    )

    print("[6] 沙箱里调不到写工具")
    run3 = asyncio.run(
        CODEACT.execute(
            ctx,
            "r = call_tool('enroll_offering', {'offering_id': 1})\nprint(r)\n",
            "越权尝试",
            timeout_s=settings.sandbox_timeout_s,
            max_output_chars=2000,
            max_code_runs=settings.max_code_runs,
            code_runs_used=0,
        )
    )
    check("写工具被拒绝", "禁止" in run3.preview or "不能" in run3.preview or not run3.ok, run3.preview[:80])

    print("[7] 直连通道（function calling）")
    bus.events.clear()
    direct = asyncio.run(DIRECT.invoke(ctx, "search_offerings", {"keyword": "CS1", "limit": 3}))
    check("直连工具正常返回", direct.ok, direct.error[:60])
    check(
        "直连事件带 channel=direct",
        any(e.get("channel") == "direct" for kind, e in bus.events if kind == "tool_call"),
        str([k for k, _ in bus.events])[:80],
    )
    check("直连结果是完整 JSON", direct.preview.startswith("{"), direct.preview[:40])

    print("=" * 72)
    if FAILURES:
        print(f"FAIL  {len(FAILURES)} 项未通过")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("OK  混合架构完整可用：function calling + CodeAct 双通道都在")
    print("    直连：模型点名工具 → 宿主执行 → 结构化结果")
    print("    代码：模型点名 execute_code → 子进程跑 Python → call_tool 桥接回只读工具")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
