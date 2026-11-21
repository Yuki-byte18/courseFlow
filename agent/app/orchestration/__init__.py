"""编排层：意图路由层 + 任务图 + supervisor + 四个子 agent。

    意图路由层（三节点）
      节点一 识别   LLM 分类 + 规则匹配 → 结构化信号（置信度 / 槽位 / 候选）
      节点二 门控   按置信度分四档：放行 / 假设性确认 / 选项式澄清 / 拒绝
      节点三 澄清   生成"您是想……对吗？"或选项列表，终止本轮等回复
                   （支持多轮；槽位增量累积；越澄清越差则回流治理层）
       ↓ 放行
    Supervisor：先产粗粒度任务图，再按依赖调度子 agent，支持运行时增删节点与回退
       ↓
    query / compute / execute / notify 四个子 agent
"""

from .graph import NodeStatus, TaskGraph, TaskNode
from .intent import IntentRecognizer, IntentSignals, SlotSet
from .router import IntentRouter, RouteOutcome
from .supervisor import Supervisor

__all__ = [
    "IntentRecognizer",
    "IntentRouter",
    "IntentSignals",
    "RouteOutcome",
    "SlotSet",
    "Supervisor",
    "TaskGraph",
    "TaskNode",
    "NodeStatus",
]
