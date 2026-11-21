"""治理层门面。

把预算、熔断、审计三件事收在一个对象里交给工具层（ToolContext.governance）。
工具层只认 `spend_tool` / `audit` 两个方法 —— 接口窄，职责就清楚。
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..events import EventBus, EventKind
from .budget import Budget
from .breaker import CircuitBreaker


class Governance:
    def __init__(self, *, run_id: str, settings: Settings, bus: EventBus) -> None:
        self.run_id = run_id
        self._settings = settings
        self._bus = bus
        self.budget = Budget(
            max_tool_calls=settings.max_tool_calls,
            max_code_runs=settings.max_code_runs,
            max_wall_seconds=settings.max_wall_seconds,
            max_llm_calls=settings.max_llm_calls,
        )
        self.breaker = CircuitBreaker()
        self.records: list[dict[str, Any]] = []
        self._warned_budget = False

    # ------------------------------------------------------------------
    # 工具层用的窄接口
    # ------------------------------------------------------------------
    def spend_tool(self, name: str) -> bool:
        ok = self.budget.spend_tool()
        if not ok:
            self._warn_budget("工具调用次数")
        return ok

    def spend_code(self) -> bool:
        ok = self.budget.spend_code()
        if not ok:
            self._warn_budget("代码执行次数")
        return ok

    def spend_llm(self) -> bool:
        ok = self.budget.spend_llm()
        if not ok:
            self._warn_budget("模型调用次数")
        return ok

    def pause_clock(self, seconds: float) -> None:
        """审批等等待时间不计入墙钟。"""
        self.budget.pause(seconds)

    def audit(self, record: dict[str, Any]) -> None:
        self.records.append(record)

    # ------------------------------------------------------------------
    def spend_tokens(self, count: int) -> None:
        self.budget.spend_tokens(count)

    def check_wall(self) -> bool:
        ok = self.budget.wall_ok()
        if not ok:
            self._warn_budget("运行时长")
        return ok

    def _warn_budget(self, dimension: str) -> None:
        """预算告警只发一次：每超一次都发事件没有信息量。"""
        if self._warned_budget:
            return
        self._warned_budget = True
        self._bus.emit(
            EventKind.BUDGET,
            dimension=dimension,
            snapshot=self.budget.snapshot(),
        )

    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        return {
            "budget": self.budget.snapshot(),
            "breaker": self.breaker.snapshot(),
            "audit_count": len(self.records),
        }
