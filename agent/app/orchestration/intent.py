"""意图路由层 · 节点一：意图识别。

输出**结构化多维信号**，而不是一个裸类别：
    intent      意图类别
    confidence  融合后的置信度
    slots       槽位（动作 / 课程号 / 星期 / 数量）
    candidates  低置信度时的候选解释（供澄清节点做选项）
    assumption  假设性确认用的陈述句
    signals     每个来源都记下来（llm / rule），便于排查"为什么这么判"

为什么要规则匹配和 LLM 并用：
    - 只靠 LLM：同一句话两次调用可能给出不同置信度，且慢（一次 2-4s）
    - 只靠规则：课程号、动作词这些**确定性极强的信号**反而更准，但看不懂
      "我周三下午不想上课"这类表述
    两者的错误模式不重叠，融合后比任何单独一方都稳。

融合策略（可在 signals 里看到每一档）：
    规则命中且与 LLM 同类   → 抬置信度
    规则命中但与 LLM 冲突   → 压置信度（交给人确认，不硬选一边）
    LLM 失败/返回 unknown  → 用规则兜底，但**故意封顶在 0.93**，
                             让它一定走"确认"而不是直接执行
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..llm.client import DeepSeekClient
from ..llm.prompts import INTENT_SYSTEM

VALID_KINDS = ("query_compute", "write", "meta", "unknown")

# 规则兜底时置信度封顶值：**永远不给到 0.95**，因为规则只是启发式，
# 没经过语义理解，不该触发"直接放行去执行写操作"。
RULE_ONLY_CAP = 0.93

# 动作 → 人话。拼上下文和澄清问句都要用。
_ACTION_LABELS = {
    "enroll": "选课",
    "drop": "退课",
    "list_enrollments": "查已选课程",
    "check_credits": "查学分与上限",
    "check_conflict": "查时间冲突",
    "search": "查课程信息",
    "ranking": "查余量排名",
    "help": "询问用法",
}


# ---------------------------------------------------------------------------
# 槽位
# ---------------------------------------------------------------------------
@dataclass
class SlotSet:
    """一句话里抽出来的业务槽位。

    刻意只放"路由阶段真的用得上的"字段。塞太多会变成第二个意图分类器，
    而槽位的价值在于**跨轮增量累积**，不在于一次抽全。
    """

    action: str = ""          # enroll / drop / list_enrollments / search / ...
    course_codes: list[str] = field(default_factory=list)
    weekday: int | None = None
    count: int | None = None

    def merge(self, other: "SlotSet") -> "SlotSet":
        """增量合并：新信息覆盖旧信息，列表取并集。

        **这就是"槽位增量更新"的落点** —— 下一轮只把新抽到的槽位并进来，
        而不是把历史对话重跑一遍再从头抽。轮次越多，历史越长，
        重跑的代价越大，而增量合并的代价是常数。
        """
        codes = list(self.course_codes)
        for code in other.course_codes:
            if code and code not in codes:
                codes.append(code)
        return SlotSet(
            action=other.action or self.action,
            course_codes=codes,
            weekday=other.weekday if other.weekday is not None else self.weekday,
            count=other.count if other.count is not None else self.count,
        )

    @property
    def filled(self) -> int:
        """已填槽位个数。用于判断"越澄清越模糊"。"""
        return sum(
            [
                bool(self.action),
                bool(self.course_codes),
                self.weekday is not None,
                self.count is not None,
            ]
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "course_codes": list(self.course_codes),
            "weekday": self.weekday,
            "count": self.count,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "SlotSet":
        if not isinstance(raw, dict):
            return cls()
        codes = raw.get("course_codes") or []
        if not isinstance(codes, list):
            codes = []
        return cls(
            action=str(raw.get("action") or ""),
            course_codes=[str(c).upper() for c in codes if c],
            weekday=_int_or_none(raw.get("weekday")),
            count=_int_or_none(raw.get("count")),
        )

    def describe(self) -> str:
        """人话描述，用于拼澄清问句。"""
        parts: list[str] = []
        if self.course_codes:
            parts.append("、".join(self.course_codes))
        if self.weekday is not None:
            parts.append(f"周{'一二三四五六日'[self.weekday - 1]}")
        if self.count is not None:
            parts.append(f"{self.count} 门")
        return " ".join(parts)

    def describe_full(self) -> str:
        """完整人话描述，用于**拼执行上下文**。

        比 describe() 多带 action —— 下游（规划器 / 子 agent）需要知道
        "用户到底要选还是退"，光有课程号是不够的。
        """
        parts: list[str] = []
        action = _ACTION_LABELS.get(self.action)
        if action:
            parts.append(f"动作：{action}")
        if self.course_codes:
            parts.append(f"课程：{'、'.join(self.course_codes)}")
        if self.weekday is not None:
            parts.append(f"星期：周{'一二三四五六日'[self.weekday - 1]}")
        if self.count is not None:
            parts.append(f"数量：{self.count} 门")
        return "；".join(parts)


# ---------------------------------------------------------------------------
# 识别结果
# ---------------------------------------------------------------------------
@dataclass
class Candidate:
    intent: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return {"intent": self.intent, "label": self.label}


@dataclass
class IntentSignals:
    """节点一的输出：结构化多维信号。"""

    intent: str
    confidence: float
    reason: str
    slots: SlotSet = field(default_factory=SlotSet)
    candidates: list[Candidate] = field(default_factory=list)
    assumption: str = ""
    # 每一路信号的原始值，排查时不用再猜
    llm_confidence: float = 0.0
    rule_id: str = ""
    rule_confidence: float = 0.0
    source: str = "llm"      # llm / rule / llm+rule / clarify_reply

    def to_payload(self) -> dict[str, Any]:
        """转成事件 payload。

        字段名用 `intent` 而不是 `kind`：事件本身已有 `kind`，
        payload 里再来一个会覆盖事件类型（踩过这个坑）。
        """
        return {
            "intent": self.intent,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "slots": self.slots.to_dict(),
            "candidates": [c.to_dict() for c in self.candidates],
            "signals": {
                "llm_confidence": round(self.llm_confidence, 3),
                "rule_id": self.rule_id,
                "rule_confidence": round(self.rule_confidence, 3),
                "source": self.source,
            },
        }


# ---------------------------------------------------------------------------
# 规则层
# ---------------------------------------------------------------------------
@dataclass
class RuleMatch:
    rule_id: str
    intent: str
    confidence: float
    slots: SlotSet = field(default_factory=SlotSet)


_COURSE_CODE_RE = re.compile(r"\b([A-Za-z]{2,4})\s*-?\s*(\d{2,4})\b")
_COUNT_RE = re.compile(r"(两|二|三|四|五|2|3|4|5)\s*门")
_WEEKDAY_RE = re.compile(r"周([一二三四五六日七])")

_ENROLL_VERBS = ("帮我选", "帮我抢", "给我选", "选上", "选一下", "我要选", "想选", "报名", "把这两门选")
_DROP_VERBS = ("退掉", "退课", "退选", "取消选课", "帮我退", "不选了")
_LIST_ENROLL = ("选了哪些", "选了什么", "我选了", "已选课程", "我的课表", "课表", "我的课程")
_CREDITS = ("学分", "上限")
_CONFLICT = ("冲突", "撞课", "时间冲")
_RANKING = ("余量最", "最紧张", "热门", "抢手")
_SEARCH = ("有哪些", "什么课", "余量", "还有多少", "查一下", "看看", "有没有")
_META = ("怎么用", "你是谁", "帮助", "怎么选课", "你能做什么", "怎么弄")
_GREET = ("你好", "hi", "hello", "在吗", "嗨", "谢谢", "thanks")

_WEEKDAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "七": 7}


def extract_slots(text: str) -> SlotSet:
    """从文本里抽确定性槽位。课程号是最有价值的信号，单独抽。"""
    codes: list[str] = []
    for match in _COURSE_CODE_RE.finditer(text):
        code = f"{match.group(1).upper()}{match.group(2)}"
        if code not in codes:
            codes.append(code)

    weekday = None
    day = _WEEKDAY_RE.search(text)
    if day:
        weekday = _WEEKDAY_MAP.get(day.group(1))

    count = None
    cnt = _COUNT_RE.search(text)
    if cnt:
        raw = cnt.group(1)
        count = {"两": 2, "二": 2, "三": 3, "四": 4, "五": 5}.get(raw) or int(raw)

    return SlotSet(course_codes=codes, weekday=weekday, count=count)


def match_rules(text: str) -> RuleMatch | None:
    """规则匹配。返回**分最高**的那一条，没有就返回 None。

    评分逻辑刻意简单：动作词越具体、附带的槽位越多，分越高。
    复杂的打分反而无法解释——而"为什么这么判"必须能解释。
    """
    lowered = text.lower()
    slots = extract_slots(text)
    best: RuleMatch | None = None

    def consider(rule_id: str, intent: str, score: float, action: str = "") -> None:
        nonlocal best
        candidate = RuleMatch(
            rule_id=rule_id,
            intent=intent,
            confidence=min(1.0, score),
            slots=SlotSet(
                action=action,
                course_codes=list(slots.course_codes),
                weekday=slots.weekday,
                count=slots.count,
            ),
        )
        if best is None or candidate.confidence > best.confidence:
            best = candidate

    # 写操作：动作词 + 课程号 → 极高确定性
    if any(verb in text for verb in _ENROLL_VERBS):
        consider("enroll_with_code", "write", 0.96 if slots.course_codes else 0.72, "enroll")
    if any(verb in text for verb in _DROP_VERBS):
        consider("drop_with_code", "write", 0.96 if slots.course_codes else 0.72, "drop")

    # 查询类：越具体的说法分越高
    if any(phrase in text for phrase in _LIST_ENROLL):
        consider("list_enrollments", "query_compute", 0.90, "list_enrollments")
    if any(phrase in text for phrase in _CREDITS):
        consider("check_credits", "query_compute", 0.85, "check_credits")
    if any(phrase in text for phrase in _CONFLICT):
        consider("check_conflict", "query_compute", 0.85, "check_conflict")
    if any(phrase in text for phrase in _RANKING):
        consider("ranking", "query_compute", 0.82, "ranking")
    if any(phrase in text for phrase in _SEARCH):
        consider("search", "query_compute", 0.62, "search")

    # 元问题与问候
    if any(phrase in text for phrase in _META):
        consider("meta", "meta", 0.86, "help")
    if len(lowered) <= 8 and any(g in lowered for g in _GREET):
        consider("greet", "meta", 0.98, "help")

    return best


# ---------------------------------------------------------------------------
# 融合
# ---------------------------------------------------------------------------
def fuse(llm_intent: str, llm_confidence: float, rule: RuleMatch | None) -> tuple[str, float, str]:
    """把 LLM 与规则两路信号融合成一个 (intent, confidence, source)。"""
    if rule is None:
        return llm_intent, llm_confidence, "llm"

    # LLM 没给出可用判断 → 规则兜底，但封顶（不让启发式直接触发写操作）
    if llm_intent in ("", "unknown"):
        return rule.intent, min(RULE_ONLY_CAP, 0.55 + 0.4 * rule.confidence), "rule"

    # 两路一致 → 抬置信度
    if rule.intent == llm_intent:
        boosted = llm_confidence + (1.0 - llm_confidence) * 0.55 * rule.confidence
        return llm_intent, min(1.0, boosted), "llm+rule"

    # 两路冲突 → 压置信度，交给人确认，不硬选一边
    if rule.confidence >= 0.7:
        return llm_intent, llm_confidence * 0.45, "llm+rule"

    return llm_intent, llm_confidence, "llm"


# ---------------------------------------------------------------------------
# 节点一
# ---------------------------------------------------------------------------
class IntentRecognizer:
    """意图识别节点：LLM 分类 + 规则匹配 + 融合，输出结构化信号。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def recognize(
        self,
        *,
        client: DeepSeekClient,
        model: str,
        message: str,
        history: list[dict[str, Any]] | None = None,
        slot_hint: SlotSet | None = None,
        clarify_round: int = 0,
    ) -> IntentSignals:
        text = (message or "").strip()
        if not text:
            return IntentSignals(
                intent="unknown",
                confidence=0.0,
                reason="空输入",
                source="rule",
            )

        rule = match_rules(text)

        # 问候语不必花一次模型调用：确定性高、且不涉及业务
        if rule is not None and rule.rule_id == "greet":
            return IntentSignals(
                intent="meta",
                confidence=rule.confidence,
                reason="问候语",
                slots=rule.slots,
                rule_id=rule.rule_id,
                rule_confidence=rule.confidence,
                source="rule",
            )

        messages: list[dict[str, Any]] = [{"role": "system", "content": INTENT_SYSTEM}]

        # **喂槽位而不是堆历史。**
        # 多轮澄清时历史会长得很快，而真正需要的只是"已经知道了什么 + 正在等什么回答"。
        if slot_hint is not None and slot_hint.filled > 0:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        f"已从前面几轮抽到的槽位（不需要重新抽）：{slot_hint.describe() or '无'}。"
                        f"当前是第 {clarify_round} 轮澄清。"
                        "如果用户这句话是在回答澄清，请结合这些槽位理解。"
                    ),
                }
            )

        for item in (history or [])[-2:]:
            if item.get("role") in ("user", "assistant"):
                messages.append(
                    {"role": item["role"], "content": str(item.get("content") or "")[:500]}
                )
        messages.append({"role": "user", "content": text})

        llm_intent = ""
        llm_confidence = 0.0
        reason = ""
        llm_slots = SlotSet()
        candidates: list[Candidate] = []
        assumption = ""

        try:
            data = await client.chat_json(model=model, messages=messages, max_tokens=320)
            llm_intent = str(data.get("intent") or "").strip()
            if llm_intent not in VALID_KINDS:
                llm_intent = "unknown"
            llm_confidence = _clamp_float(data.get("confidence"))
            reason = str(data.get("reason") or "").strip()[:60]
            llm_slots = SlotSet.from_dict(data.get("slots"))
            assumption = str(data.get("assumption") or "").strip()[:120]
            for item in (data.get("candidates") or [])[:3]:
                if isinstance(item, dict) and item.get("label"):
                    candidates.append(
                        Candidate(
                            intent=str(item.get("intent") or ""),
                            label=str(item["label"])[:60],
                        )
                    )
        except Exception as exc:  # noqa: BLE001
            # 识别失败不阻塞流程：规则能兜就兜，兜不住就当低置信度去澄清
            reason = f"识别失败：{type(exc).__name__}"

        intent, confidence, source = fuse(llm_intent, llm_confidence, rule)

        # 槽位增量合并：规则抽到的确定性槽位（课程号等）优先于模型抽的
        slots = llm_slots.merge(rule.slots) if rule else llm_slots

        return IntentSignals(
            intent=intent if intent in VALID_KINDS else "unknown",
            confidence=confidence,
            reason=reason or (rule.rule_id if rule else "无法判断"),
            slots=slots,
            candidates=candidates,
            assumption=assumption,
            llm_confidence=llm_confidence,
            rule_id=rule.rule_id if rule else "",
            rule_confidence=rule.confidence if rule else 0.0,
            source=source,
        )


# ---------------------------------------------------------------------------
def _clamp_float(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value))


def _int_or_none(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None
