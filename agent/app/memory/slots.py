"""意图路由层的槽位状态：跨轮持久化。

为什么需要"状态"而不是"把历史重跑一遍"：
    澄清是**跨请求**的：这一轮问"您是想……对吗？"，用户下一轮才回"是"。
    如果每轮都把整段历史重新喂给模型再从头抽槽位，
    历史会越来越长、延迟越来越大、而且同一句话的抽取结果可能漂移。

所以这里维护一个**槽位状态对象**：
    - 每轮只把新抽到的槽位**增量合并**进来
    - 同时记住"澄清到第几轮了"和"上一句问了什么"
    - 话题结束（放行进入编排）就重置轮次，但槽位保留 —— 下一句还能接着用

存储位置：MySQL 的 agent_intent_slots（见 migrations/0009）。
不用 Redis 是因为这层状态很小（几百字节），而跨轮一致性比吞吐更重要。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .. import db
from ..orchestration.intent import SlotSet

log = logging.getLogger("courseflow.agent")

# 置信度历史只留最近这么多轮，够做回归判定就行
_MAX_HISTORY = 6


@dataclass
class PendingClarify:
    """上一轮问了什么。下一轮靠它理解"是"/"第一个"这类极短回复。"""

    kind: str = ""            # confirm / option / followup
    # **这一轮到底在澄清哪个维度**：intent / action / course / confirm
    #
    # 这是"每次只澄清一个维度"的载体：回答只用来补这一个维度，
    # 补完再回意图识别判断还缺不缺，缺就继续问下一个维度。
    dimension: str = ""
    intent: str = ""
    slots: SlotSet = field(default_factory=SlotSet)
    assumption: str = ""
    options: list[dict[str, str]] = field(default_factory=list)
    question: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "dimension": self.dimension,
            "intent": self.intent,
            "slots": self.slots.to_dict(),
            "assumption": self.assumption,
            "options": self.options,
            "question": self.question,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "PendingClarify | None":
        if not isinstance(raw, dict) or not raw.get("kind"):
            return None
        options = raw.get("options")
        return cls(
            kind=str(raw.get("kind") or ""),
            dimension=str(raw.get("dimension") or ""),
            intent=str(raw.get("intent") or ""),
            slots=SlotSet.from_dict(raw.get("slots")),
            assumption=str(raw.get("assumption") or ""),
            options=[o for o in (options or []) if isinstance(o, dict)],
            question=str(raw.get("question") or ""),
        )


@dataclass
class RouteState:
    """一条会话在意图路由层里的全部状态。"""

    thread_id: str
    user_id: int
    slots: SlotSet = field(default_factory=SlotSet)
    clarify_round: int = 0
    last_intent: str = ""
    last_confidence: float | None = None
    confidence_history: list[float] = field(default_factory=list)
    pending: PendingClarify | None = None
    # 已经澄清过的维度集合。用于"每次只澄清一个维度"的去重：
    # 同一个维度不重复问，避免原地打转。
    clarified_dimensions: list[str] = field(default_factory=list)

    # ---- 回归判定：越澄清越差 ----
    def is_regressing(self, patience: int) -> tuple[bool, str]:
        """连续 patience 轮置信度不升反降，就认为"越澄清越差"。

        只看置信度不看槽位数：置信度下降已经说明识别侧在动摇，
        而槽位偶尔少一个可能是用户主动缩小了范围（"算了只选一门"）。
        """
        history = [c for c in self.confidence_history if c is not None]
        if len(history) <= patience:
            return False, ""
        recent = history[-(patience + 1):]
        strictly_down = all(
            recent[i] > recent[i + 1] for i in range(len(recent) - 1)
        )
        if strictly_down:
            return True, f"置信度连续 {patience} 轮下降：{[round(x, 2) for x in recent]}"
        return False, ""

    def push_confidence(self, value: float) -> None:
        self.confidence_history.append(round(float(value), 3))
        if len(self.confidence_history) > _MAX_HISTORY:
            del self.confidence_history[: -_MAX_HISTORY]
        self.last_confidence = float(value)


# ---------------------------------------------------------------------------
def load(thread_id: str, user_id: int) -> RouteState:
    """读一条会话的槽位状态。没有就返回空状态。

    任何异常都不抛出：槽位状态丢失只是"少了点上下文"，
    不该让整次对话失败。但会打日志，别让问题静默。
    """
    state = RouteState(thread_id=thread_id, user_id=user_id)
    if not thread_id:
        return state

    try:
        row = db.fetch_one(
            "SELECT slots_json, clarify_round, last_intent, last_confidence, "
            "confidence_history, pending_json, clarified_dimensions FROM agent_intent_slots "
            "WHERE thread_id = %s AND user_id = %s",
            (thread_id, user_id),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("读取意图槽位失败（可能是迁移 0009 未执行）：%s", exc)
        return state

    if not row:
        return state

    state.slots = SlotSet.from_dict(_from_json(row.get("slots_json")))
    state.clarify_round = int(row.get("clarify_round") or 0)
    state.last_intent = str(row.get("last_intent") or "")
    raw_conf = row.get("last_confidence")
    state.last_confidence = float(raw_conf) if raw_conf is not None else None
    history = _from_json(row.get("confidence_history"))
    if isinstance(history, list):
        state.confidence_history = [float(x) for x in history if isinstance(x, (int, float))]
    state.pending = PendingClarify.from_dict(_from_json(row.get("pending_json")))
    dims = _from_json(row.get("clarified_dimensions"))
    if isinstance(dims, list):
        state.clarified_dimensions = [str(d) for d in dims if d]
    return state


def save(state: RouteState) -> None:
    """写回槽位状态（upsert）。"""
    if not state.thread_id:
        return
    try:
        db.execute(
            """
            INSERT INTO agent_intent_slots
                (thread_id, user_id, slots_json, clarify_round, last_intent,
                 last_confidence, confidence_history, pending_json, clarified_dimensions)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                slots_json = VALUES(slots_json),
                clarify_round = VALUES(clarify_round),
                last_intent = VALUES(last_intent),
                last_confidence = VALUES(last_confidence),
                confidence_history = VALUES(confidence_history),
                pending_json = VALUES(pending_json),
                clarified_dimensions = VALUES(clarified_dimensions)
            """,
            (
                state.thread_id,
                state.user_id,
                json.dumps(state.slots.to_dict(), ensure_ascii=False),
                state.clarify_round,
                state.last_intent,
                state.last_confidence,
                json.dumps(state.confidence_history, ensure_ascii=False),
                json.dumps(
                    state.pending.to_dict() if state.pending else None, ensure_ascii=False
                ),
                json.dumps(state.clarified_dimensions, ensure_ascii=False),
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("保存意图槽位失败（可能是迁移 0009 未执行）：%s", exc)


def clear(thread_id: str, user_id: int) -> None:
    """清空某条会话的槽位状态（删除会话时调用）。"""
    if not thread_id:
        return
    try:
        db.execute(
            "DELETE FROM agent_intent_slots WHERE thread_id = %s AND user_id = %s",
            (thread_id, user_id),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("清理意图槽位失败：%s", exc)


def _from_json(raw: Any) -> Any:
    """MySQL 的 JSON 列可能返回 str，也可能已经被驱动解析成对象。"""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None
