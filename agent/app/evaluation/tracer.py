"""轨迹录制与重放。

一次运行结束后，把事件流写进 `agent_runs` / `agent_steps` 两张表。
trace 观测面板从这两张表读，前端就能看到"每一步是哪个子 agent、调了什么工具、
输入是什么、输出是什么、花了多久"。

为什么自己存而不是只靠 LangSmith：
  1. 第三方服务需要网络和 Key，答辩现场不可依赖
  2. 我们要在**自己的界面里**展示，跨系统拼数据很麻烦
  3. LangSmith 的价值在"评估数据集与对比"，不在于"看一次运行的细节"

表结构见 backend/migrations/0007_agent_trace.sql。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .. import db
from ..config import Settings
from ..events import EventKind


def new_run_id() -> str:
    """26 位、按时间有序的 run id（ULID 风格）。

    用时间戳前缀 + 随机后缀：既能按时间排序，又不引入 ulid 依赖。
    """
    ts = int(datetime.now().timestamp() * 1000)
    return f"{ts:013d}{uuid.uuid4().hex[:13]}"


@dataclass
class TracerEvent:
    """落库用的扁平事件。"""

    seq: int
    kind: str
    agent: str | None
    name: str | None
    status: int
    input: Any
    output: Any
    error: str | None
    latency_ms: int | None


# 事件类型 → 落库的 kind 字符串（与前端 trace 面板的图标映射对齐）
_TRACEABLE = {
    EventKind.INTENT,
    # 意图路由层的质量信号：澄清越澄清越差时回流到这里，落库后可统计
    EventKind.INTENT_REGRESSION,
    EventKind.CLARIFY,
    EventKind.GRAPH,
    EventKind.NODE_STATUS,
    EventKind.AGENT_STARTED,
    EventKind.AGENT_FINISHED,
    EventKind.TOOL_CALL,
    EventKind.TOOL_RESULT,
    EventKind.CODE_RUN,
    EventKind.CODE_OUTPUT,
    EventKind.APPROVAL_REQUIRED,
    EventKind.APPROVAL_RESULT,
    EventKind.BUDGET,
    EventKind.CARD,
    EventKind.ERROR,
}


class Tracer:
    """一次运行的录制器。

    **绝不因为落库失败而影响主流程**：trace 是观测，不是业务。
    所以每个写操作都吞异常，只记到 self.errors 里。
    """

    def __init__(self, *, run_id: str, user_id: int, model: str) -> None:
        self.run_id = run_id
        self.user_id = user_id
        self.model = model
        self.started_at = datetime.now()
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.errors: list[str] = []
        self._seq = 0

    # ------------------------------------------------------------------
    def start(self, *, title: str, intent: str = "", confidence: float = 0.0) -> None:
        sql = """
            INSERT INTO agent_runs
                (run_id, user_id, title, intent, confidence, status, model, started_at)
            VALUES (%s, %s, %s, %s, %s, 1, %s, %s)
        """
        try:
            db.execute(
                sql,
                (
                    self.run_id,
                    self.user_id,
                    title[:200],
                    intent[:32],
                    confidence,
                    self.model[:64],
                    self.started_at,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"start: {exc}")

    def add_tokens(self, prompt: int, completion: int) -> None:
        self.prompt_tokens += prompt
        self.completion_tokens += completion

    # ------------------------------------------------------------------
    def record(self, event: dict[str, Any]) -> None:
        """把一个事件写进 agent_steps。事件来自 EventBus 的 history。"""
        kind = event.get("kind")
        try:
            kind_enum = EventKind(kind)
        except ValueError:
            return
        if kind_enum not in _TRACEABLE:
            return

        self._seq += 1
        agent = event.get("agent")
        name = (
            event.get("tool")
            or event.get("node_id")
            or event.get("card")
            or event.get("dimension")
        )
        error = event.get("error") or event.get("reason")
        latency = event.get("duration_ms")

        status = 1
        if error:
            status = 3
        elif event.get("ok") is False or event.get("blocked") is True:
            status = 3
        elif event.get("approved") is False:
            status = 3

        sql = """
            INSERT INTO agent_steps
                (run_id, seq, kind, agent, name, status, input_json, output_json, error, latency_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        try:
            db.execute(
                sql,
                (
                    self.run_id,
                    self._seq,
                    kind,
                    (agent or "")[:32] or None,
                    (str(name) if name else None),
                    status,
                    _json_or_none(_pick_input(event)),
                    _json_or_none(_pick_output(event)),
                    (str(error)[:1000] if error else None),
                    latency if isinstance(latency, int) else None,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            # 失败不重试、不影响主流程，只累计错误数
            self.errors.append(f"step {kind}: {exc}")

    # ------------------------------------------------------------------
    def finish(self, *, status: int, error: str = "") -> None:
        sql = """
            UPDATE agent_runs
               SET status = %s,
                   finished_at = %s,
                   prompt_tokens = %s,
                   completion_tokens = %s,
                   error = %s
             WHERE run_id = %s
        """
        try:
            db.execute(
                sql,
                (
                    status,
                    datetime.now(),
                    self.prompt_tokens,
                    self.completion_tokens,
                    (error[:2000] or None),
                    self.run_id,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self.errors.append(f"finish: {exc}")


def _json_or_none(data: Any) -> str | None:
    if data is None:
        return None
    try:
        return json.dumps(data, ensure_ascii=False, default=str)[:60000]
    except (TypeError, ValueError):
        return None


def _pick_input(event: dict[str, Any]) -> Any:
    """从事件里挑"输入"该展示什么。"""
    for key in ("args", "code", "nodes", "question", "payload"):
        if key in event:
            return event[key]
    return None


def _pick_output(event: dict[str, Any]) -> Any:
    for key in ("preview", "output", "result", "summary", "nodes"):
        if key in event:
            return event[key]
    return None


def load_tracer_settings(settings: Settings) -> Settings:
    """占位：保留签名，方便以后按配置决定是否落库。"""
    return settings
