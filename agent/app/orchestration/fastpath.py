"""明确写操作的快路径。

用户说「帮我选 CS109 和 CS110」时，意图已经清楚：查这两门的班次，然后选。
再让模型产一张可能带 compute、可能把两门拆成四五步的任务图，只会多烧
几轮 LLM —— 表现就是前端卡在某一步一两分钟。

这里用课程号正则 + 动词识别，直接给出最小图（query → execute），
规划器那一轮模型调用直接跳过。
"""

from __future__ import annotations

import re

from .graph import TaskGraph, build_graph_from_plan

# CS109、MATH-101、CS 110 都能识别
_CODE_RE = re.compile(r"\b([A-Za-z]{2,6})\s*-?\s*(\d{3}[A-Za-z]?)\b")

_ENROLL_HINTS = ("选", "加选", "报名", "enroll", "加入")
_DROP_HINTS = ("退课", "退掉", "退选", "drop")
_META_HINTS = ("怎么选", "如何选", "怎样选", "选课规则", "选课流程")


def extract_course_codes(message: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in _CODE_RE.finditer(message or ""):
        code = f"{match.group(1)}{match.group(2)}".upper()
        if code in seen:
            continue
        seen.add(code)
        found.append(code)
    return found


def try_write_graph(message: str, intent_kind: str) -> TaskGraph | None:
    """能确定「对这几门课做选/退」时返回最小图，否则返回 None 走模型规划。"""
    if intent_kind != "write":
        return None
    text = message or ""
    if any(hint in text for hint in _META_HINTS):
        return None
    codes = extract_course_codes(text)
    if not codes:
        return None

    joined = "、".join(codes)
    dropping = any(hint in text for hint in _DROP_HINTS) and not any(
        hint in text for hint in ("帮我选", "给我选", "选上", "选课")
    )
    if dropping:
        return build_graph_from_plan(
            {
                "nodes": [
                    {
                        "id": "n1",
                        "agent": "query",
                        "goal": f"确认已选的 {joined} 及 offering_id",
                        "deps": [],
                    },
                    {
                        "id": "n2",
                        "agent": "execute",
                        "goal": f"退课 {joined}",
                        "deps": ["n1"],
                    },
                ]
            }
        )

    if not any(hint in text for hint in _ENROLL_HINTS):
        return None

    return build_graph_from_plan(
        {
            "nodes": [
                {
                    "id": "n1",
                    "agent": "query",
                    "goal": f"用课程号一次查出 {joined} 的班次、余量和 offering_id",
                    "deps": [],
                },
                {
                    "id": "n2",
                    "agent": "execute",
                    "goal": (
                        f"一次性选课 {joined}。"
                        "观察结果里已有 offering_id 就直接 enroll_offerings，不要再查，也不要一门一门调。"
                    ),
                    "deps": ["n1"],
                },
            ]
        }
    )
