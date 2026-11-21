"""量一下给 compute 子 agent 的工具声明和系统提示有多大。

背景：日志里查询助手的每次调用都能在 0.5s 内看到 httpx 的 "HTTP Request" 行，
而计算助手那次**连请求都没发出去**。所以先怀疑"这一路的 payload 有问题"。

这个脚本把 compute 的 tools 数组和 system prompt 真的构造出来，量长度，
并检查能不能 JSON 序列化。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.llm.prompts import AGENT_SYSTEM_TEMPLATE, SANDBOX_TOOLS_HEADER  # noqa: E402
from app.orchestration.agents import build_agents  # noqa: E402
from app.tools import business  # noqa: F401,E402
from app.tools.policy import POLICY  # noqa: E402

SCOPES = (
    "offering:read",
    "enrollment:read",
    "enrollment:create",
    "enrollment:drop",
    "schedule:read",
    "agent:chat",
)


def main() -> int:
    settings = get_settings()
    agents = build_agents(settings)

    for name in ("query", "compute", "execute"):
        decision = POLICY.decide(agent=name, scopes=SCOPES)
        schema = decision.tools_schema

        try:
            serialized = json.dumps(schema, ensure_ascii=False)
            json_ok = True
        except (TypeError, ValueError) as exc:
            serialized = ""
            json_ok = False
            print(f"  [{name}] JSON 序列化失败：{exc}")

        print("=" * 64)
        print(f"[{name}] 工具 {len(schema)} 个，tools JSON {len(serialized)} 字符，可序列化={json_ok}")

        for item in schema:
            fn = item.get("function") or {}
            desc = str(fn.get("description") or "")
            params = json.dumps(fn.get("parameters") or {}, ensure_ascii=False)
            print(f"    {fn.get('name'):<24} desc={len(desc):>5} 参数={len(params):>4}")

        # 系统提示有多大
        agent = agents.get(name) if isinstance(agents, dict) else None
        if agent is not None:
            try:
                prompt = AGENT_SYSTEM_TEMPLATE.format(
                    agent_label=getattr(agent, "label", name),
                    agent_role="",
                    sandbox_tools=(SANDBOX_TOOLS_HEADER + decision.sandbox_notes)
                    if decision.sandbox_notes
                    else "（本轮不提供代码通道）",
                )
                print(f"    系统提示 {len(prompt)} 字符")
            except (KeyError, IndexError) as exc:
                print(f"    系统提示构造失败：{exc}")

    print("=" * 64)
    print("对照：DeepSeek 单次请求体通常几 KB 到几十 KB 都正常；")
    print("但如果 execute_code 的 description 把 14 个工具签名全塞进去，会膨胀得很快。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
