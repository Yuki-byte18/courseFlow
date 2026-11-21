"""工具层 L4：治理层。

**不参与路由**，但每个调用都经过它：

    audit.py     审计日志（谁、在哪个 agent、调了什么、成没成、多久）
    budget.py    预算控制（工具次数、代码次数、token、墙钟）
    breaker.py   超时与熔断（连续失败就快速失败，别把延迟叠加上去）

轨迹录制与重放不在这里，在 evaluation/tracer.py ——
治理关心"要不要继续跑"，评测关心"刚才发生了什么"，是两件事。
"""

from .budget import Budget, BudgetExceeded
from .breaker import CircuitBreaker, CircuitOpen
from .core import Governance

__all__ = ["Budget", "BudgetExceeded", "CircuitBreaker", "CircuitOpen", "Governance"]
