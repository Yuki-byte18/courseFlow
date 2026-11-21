"""预算控制。

一次运行能花多少：工具调用次数、代码执行次数、token、墙钟时间。

为什么必须有上限：Agent 是循环的，一旦模型陷入"查了不满意再查"的循环，
没有预算就会一直烧钱。这是 Agent 与普通 API 最大的运维差异。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


class BudgetExceeded(RuntimeError):
    """预算用尽。上层据此中止本次运行。"""

    def __init__(self, dimension: str) -> None:
        super().__init__(f"预算已用尽：{dimension}")
        self.dimension = dimension


@dataclass
class Budget:
    max_tool_calls: int
    max_code_runs: int
    max_wall_seconds: float
    max_llm_calls: int = 12

    tool_calls: int = 0
    code_runs: int = 0
    llm_calls: int = 0
    tokens: int = 0
    _started: float = field(default_factory=time.monotonic)
    # 被"人为等待"占掉的时间（主要是审批）。
    # 这段时间不能算进运行墙钟 —— 否则用户慢慢点一下确认，
    # 回来就发现整件事已经超时失败了。
    _paused: float = 0.0

    # ------------------------------------------------------------------
    def spend_tool(self) -> bool:
        if self.tool_calls >= self.max_tool_calls:
            return False
        self.tool_calls += 1
        return True

    def spend_code(self) -> bool:
        if self.code_runs >= self.max_code_runs:
            return False
        self.code_runs += 1
        return True

    def spend_llm(self) -> bool:
        """模型调用预算。

        这是防"卡死"的最后一根闸：工具和代码次数都用尽时，
        模型仍可能一轮轮地"再想想"，每次都花几秒。
        没有这个上限，一次运行可以无限拖。
        """
        if self.llm_calls >= self.max_llm_calls:
            return False
        self.llm_calls += 1
        return True

    def spend_tokens(self, count: int) -> None:
        if count > 0:
            self.tokens += count

    def pause(self, seconds: float) -> None:
        """把一段"不是系统在干活"的时间从墙钟里扣掉。

        典型场景：审批。用户在思考要不要允许，这不该算作 Agent 慢。
        """
        if seconds > 0:
            self._paused += seconds

    # ------------------------------------------------------------------
    @property
    def elapsed_s(self) -> float:
        """真实工作时间，已经扣掉审批等人工等待。"""
        return max(0.0, time.monotonic() - self._started - self._paused)

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.max_wall_seconds - self.elapsed_s)

    def wall_ok(self) -> bool:
        return self.remaining_s > 0

    def snapshot(self) -> dict[str, int | float]:
        return {
            "tool_calls": self.tool_calls,
            "max_tool_calls": self.max_tool_calls,
            "code_runs": self.code_runs,
            "max_code_runs": self.max_code_runs,
            "llm_calls": self.llm_calls,
            "max_llm_calls": self.max_llm_calls,
            "tokens": self.tokens,
            "elapsed_s": round(self.elapsed_s, 1),
            "max_wall_seconds": self.max_wall_seconds,
        }
