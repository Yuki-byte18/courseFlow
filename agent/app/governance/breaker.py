"""熔断。

场景：DeepSeek 侧连续 401/429/超时。继续重试只会把用户的等待时间叠上去，
而且每一次都算一次预算。熔断打开之后直接快速失败，把错误暴露给用户。

刻意做成"连续失败 N 次 → 打开"，而不是统计窗口内的错误率：
一次运行只有几到几十次调用，统计窗口在这个量级上没有意义。
"""

from __future__ import annotations


class CircuitOpen(RuntimeError):
    """熔断已打开，拒绝继续调用。"""


class CircuitBreaker:
    def __init__(self, threshold: int = 3, cooldown_s: float = 30.0) -> None:
        self._threshold = threshold
        self._cooldown_s = cooldown_s
        self._consecutive_failures = 0
        self._opened = False
        self._last_error = ""

    def allow(self) -> bool:
        return not self._opened

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened = False

    def record_failure(self, error: str) -> None:
        self._consecutive_failures += 1
        self._last_error = error[:300]
        if self._consecutive_failures >= self._threshold:
            self._opened = True

    @property
    def state(self) -> str:
        return "open" if self._opened else "closed"

    @property
    def last_error(self) -> str:
        return self._last_error

    def snapshot(self) -> dict[str, object]:
        return {
            "state": self.state,
            "consecutive_failures": self._consecutive_failures,
            "last_error": self._last_error,
        }
