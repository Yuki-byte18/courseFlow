"""结构化事件。

整条链路（意图识别 → 任务图 → 子 agent → 工具 → 审批 → 结论）的每一步
都产生一个事件，直接推给前端。

为什么用事件而不是"最后给一段文本"：
  1. 前端要实时渲染步骤条、子 agent 卡片、审批面板 —— 需要过程而不只是结果
  2. 事件是**可重放的**：同一条流既能实时消费，也能落库后回看（trace 面板）
  3. 事件带 seq，乱序/丢包可检测

**线程安全**：emit 会被工具沙箱的工作线程调用，而 SSE 消费在主事件循环里，
所以内部加了锁。消费侧用 `snapshot_from(cursor)` 轮询 —— 比 Queue 简单，
而且天然支持"回溯前面的事件"。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EventKind(str, Enum):
    """全部事件类型。新增类型必须同时在 frontend 的 types 里加一条。"""

    # 运行生命周期
    RUN_STARTED = "run_started"
    RUN_FINISHED = "run_finished"

    # 编排层
    INTENT = "intent"           # 意图识别结果
    CLARIFY = "clarify"         # 旧的"直接反问"（保留兼容，新流程用 clarify_required）
    # 澄清闸门：运行**挂起**等用户补充信息，拿到回答后同一个 run 继续。
    # 这是"本轮任务不停"的实现方式 —— 和审批闸门同构。
    CLARIFY_REQUIRED = "clarify_required"
    CLARIFY_ANSWER = "clarify_answer"
    # 澄清越澄清越差（置信度不升反降 / 槽位越来越模糊）→ 回流治理层
    INTENT_REGRESSION = "intent_regression"
    GRAPH = "graph"             # 任务图（首次下发 + 运行时变更）
    NODE_STATUS = "node_status" # 任务节点状态变化

    # 子 agent
    AGENT_STARTED = "agent_started"
    AGENT_FINISHED = "agent_finished"
    DELTA = "delta"             # 模型流式文本增量

    # 工具层：直接通道
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"

    # 工具层：代码通道（CodeAct）
    CODE_RUN = "code_run"
    CODE_OUTPUT = "code_output"

    # 治理层
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_RESULT = "approval_result"
    BUDGET = "budget"           # 预算/超时告警

    # 结果
    CARD = "card"               # 可交互的课程卡片
    FINAL = "final"             # 最终回答
    ERROR = "error"

    # 阶段标记。不是领域事件，而是"现在在等什么"——
    # 一次运行的大部分时间在等模型，没有这个标记界面会看起来像死机。
    CUSTOM_STAGE = "stage"


@dataclass
class Event:
    """一个结构化事件。seq 在本 run 内严格自增。"""

    kind: EventKind
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind.value, **self.payload}


class EventBus:
    """事件记录 + 有序下发。

    同时服务两个消费者：
      - SSE 流（实时轮询 snapshot_from）
      - trace 落库（运行结束后一次性取 history）
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self.history: list[Event] = []
        self.closed = False
        self.failed = False
        # 最后一次发事件的时间。SSE 消费端用它判断"是不是很久没动静了"，
        # 好发心跳让界面能区分"在等模型"和"真的死了"。
        self.last_emit_at = time.monotonic()

    def emit(self, kind: EventKind, **payload: Any) -> Event:
        with self._lock:
            self._seq += 1
            event = Event(kind=kind, payload={"seq": self._seq, **payload})
            self.history.append(event)
            self.last_emit_at = time.monotonic()
            return event

    def idle_seconds(self) -> float:
        with self._lock:
            return time.monotonic() - self.last_emit_at

    def close(self) -> None:
        with self._lock:
            self.closed = True

    # ------------------------------------------------------------------
    def snapshot_from(self, cursor: int) -> list[dict[str, Any]]:
        """取出 cursor 之后的事件（dict 形式，可直接 JSON 序列化）。"""
        with self._lock:
            pending = self.history[cursor:]
            return [event.to_dict() for event in pending]

    def history_snapshot(self) -> list[dict[str, Any]]:
        """全量事件（dict 形式）。trace 落库时用。"""
        with self._lock:
            return [event.to_dict() for event in self.history]

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    @property
    def length(self) -> int:
        with self._lock:
            return len(self.history)
