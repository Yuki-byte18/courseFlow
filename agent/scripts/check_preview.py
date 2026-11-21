"""验证工具结果不再被无谓截断。

背景：用户问"我目前选了哪些课"，agent 回答"还差 2 门查不出来，接口返回被截断"。
根因是 my_enrollments 的 preview_chars 只有 2000，而 9 门课的真实返回要 3.5KB。

这个脚本对几个"列表类"工具量一遍：真实返回多大、预览后还剩多少条。
只要出现"原始 N 条 → 预览 M 条（M<N）"，就说明还会让模型说"查不全"。

用法：
    cd agent
    python scripts/check_preview.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.tools import business  # noqa: F401,E402
from app.tools.context import ToolContext  # noqa: E402
from app.tools.direct import _preview  # noqa: E402
from app.tools.registry import REGISTRY  # noqa: E402

FAILURES: list[str] = []


def main() -> int:
    ok, error = db.ping()
    if not ok:
        print(f"数据库连接失败：{error}")
        return 1

    user = db.fetch_one("SELECT id, username FROM users WHERE username = '2024002'")
    if not user:
        print("找不到 2024002")
        return 1

    ctx = ToolContext(
        run_id="preview-check",
        user_id=int(user["id"]),
        username=user["username"],
        scopes=("offering:read", "enrollment:read", "schedule:read"),
    )

    cases = [
        ("my_enrollments", {}, "payload.offerings"),
        ("my_schedule", {}, "payload.items"),
        ("search_offerings", {"limit": 20}, "payload.offerings"),
        ("get_current_round", {}, None),
        ("compute_credit_summary", {}, None),
    ]

    print("=" * 66)
    for name, args, counter in cases:
        spec = REGISTRY.require(name)
        data = spec.handler(ctx, args)
        preview = _preview(data, spec.preview_chars)

        try:
            parsed = json.loads(preview)
            valid = True
        except json.JSONDecodeError:
            parsed = {}
            valid = False

        if counter:
            head, field = counter.split(".")
            raw_n = len((data.get(head) or {}).get(field) or [])
            prev_n = len((parsed.get(head) or {}).get(field) or [])
            status = "OK" if raw_n == prev_n else f"丢 {raw_n - prev_n} 条"
            if raw_n != prev_n:
                FAILURES.append(f"{name}: 原始 {raw_n} 条，预览只剩 {prev_n} 条")
            print(
                f"  {name:<22} 原始 {raw_n:>2} 条 → 预览 {prev_n:>2} 条  "
                f"{len(preview):>5}/{spec.preview_chars} 字符  {status}"
            )
        else:
            print(f"  {name:<22} {len(preview):>5}/{spec.preview_chars} 字符")

        if not valid:
            FAILURES.append(f"{name}: 预览不是合法 JSON")

    print("=" * 66)
    if FAILURES:
        print(f"FAIL  {len(FAILURES)} 项会被模型当成「查不全」：")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("OK  列表类工具的结果都完整给到模型，不会出现「被截断」的托辞")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
