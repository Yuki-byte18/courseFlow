"""意图路由层：三个节点 + 四个出口。

    节点一  recognize()   意图识别   LLM 分类 + 规则匹配 → 结构化信号（置信度/槽位/候选）
    节点二  gate()        门控       按置信度分档，决定走哪个出口
    节点三  clarify()     意图澄清   **每次只澄清一个维度**，多轮推进

出口（用户明确要求的分档）：

    0.95 ~ 1.00   pass     直接放行，进编排层开始执行
    0.75 ~ 0.95   clarify  假设性确认："您是想……对吗？"
    0.50 ~ 0.75   clarify  选项式/追问式
    0.00 ~ 0.50   reject   听不懂，请用户换个说法

## 澄清是"每次一个维度"

一次澄清只问一件事，问的东西按这个顺序推进：

    intent   你是想查，还是想让我选/退？
    action   具体做哪件事？（选 / 退 / 查已选 / 查学分 / 查余量…）
    course   哪门课？（说课程号）
    confirm  都齐了，但置信度没到 0.95 → 整体确认一次

每答完一个维度，**回到识别节点重新判断**：
还缺维度就继续问下一个；都齐了才放行。这就是多轮澄清。
所以不会出现"一次问三个事、用户不知道该答哪个"的情况。

选项式澄清 = 一个要澄清的问题 + 几个针对**这一个维度**的选项。
比如问 action 时，选项就是"选课 / 退课 / 先查一下"，
而不是"我不太确定你的意思"这种什么都没说的话。

三个边界（用户明确要求）：

    ① 最多澄清 `max_clarify_rounds` 轮（默认 3）。超限**降级为选项式**；
       再超一轮就明确放弃并请用户换说法，避免无限来回。
    ② 槽位状态对象跨轮**增量合并**（memory/slots.py），不重跑历史。
    ③ 澄清越搞越差（置信度连续下降）→ 判定识别侧或话术侧有问题，
       发 INTENT_REGRESSION 事件 + 审计记录**回流治理层**，并停止继续澄清。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import Any

from ..config import Settings
from ..events import EventKind
from ..memory import slots as slot_store
from .intent import (
    IntentRecognizer,
    IntentSignals,
    SlotSet,
    extract_slots,
    match_rules,
)

# 出口类型
PASS = "pass"
CLARIFY = "clarify"
REJECT = "reject"

# 置信度分档
BAND_DIRECT = "direct"
BAND_CONFIRM = "confirm"
BAND_OPTION = "option"
BAND_REJECT = "reject"

# 澄清维度。顺序即推进顺序：先搞清意图，再搞清动作，最后问具体课程。
DIM_INTENT = "intent"
DIM_ACTION = "action"
DIM_COURSE = "course"
DIM_CONFIRM = "confirm"
DIMENSION_ORDER = (DIM_INTENT, DIM_ACTION, DIM_COURSE, DIM_CONFIRM)

# 写操作的动作集合 / 查询类动作集合
WRITE_ACTIONS = ("enroll", "drop")
QUERY_ACTIONS = ("search", "list_enrollments", "check_credits", "check_conflict", "ranking")

_NEGATIONS = ("不是", "不对", "不用", "不要", "不用了", "取消", "算了", "先不", "no", "nope")
_AFFIRMATIONS = (
    "是", "对", "嗯", "好", "是的", "对的", "没错", "确定", "可以", "行",
    "确认", "好的", "是啊", "对呀", "要", "需要", "yes", "y", "ok", "okay",
)

_ORDINAL_PATTERNS = (
    re.compile(r"^\s*第?\s*([1-9])\s*(个|项|条)?\s*$"),
    re.compile(r"^\s*选\s*([1-9])\s*$"),
    re.compile(r"^\s*选项\s*([1-9])\s*$"),
)
_CN_ORDINAL = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}
_CN_ORDINAL_RE = re.compile(r"^(?:那我?选|我要|要|就)?\s*第\s*([一二三四五])\s*(?:个|项|条)?\s*$")


# ---------------------------------------------------------------------------
# 每个维度的选项。选项自带 dimension + value，
# 用户点了之后**不需要再过一遍模型**就能落位到槽位。
# ---------------------------------------------------------------------------
_INTENT_OPTIONS: list[dict[str, str]] = [
    {"dimension": DIM_INTENT, "value": "query_compute", "label": "查课程信息 / 算学分"},
    {"dimension": DIM_INTENT, "value": "write", "label": "帮我选课或退课"},
    {"dimension": DIM_INTENT, "value": "meta", "label": "问这个系统怎么用"},
]

_WRITE_ACTION_OPTIONS: list[dict[str, str]] = [
    {"dimension": DIM_ACTION, "value": "enroll", "label": "帮我选上（会写数据）"},
    {"dimension": DIM_ACTION, "value": "drop", "label": "帮我退掉（会写数据）"},
    {"dimension": DIM_ACTION, "value": "search", "label": "先只查一下，不写"},
]

_QUERY_ACTION_OPTIONS: list[dict[str, str]] = [
    {"dimension": DIM_ACTION, "value": "list_enrollments", "label": "查我已经选了哪些课"},
    {"dimension": DIM_ACTION, "value": "check_credits", "label": "查我的学分和上限"},
    {"dimension": DIM_ACTION, "value": "search", "label": "查某门课的班次和余量"},
    {"dimension": DIM_ACTION, "value": "check_conflict", "label": "查课程之间有没有时间冲突"},
]

_CONFIRM_OPTIONS: list[dict[str, str]] = [
    {"dimension": DIM_CONFIRM, "value": "yes", "label": "对，就是这个意思"},
    {"dimension": DIM_CONFIRM, "value": "no", "label": "不对，我换个说法"},
]


@dataclass
class ClarifyQuestion:
    """节点三的产物：**一个问题**（+ 该维度的若干选项）。"""

    kind: str            # confirm / option / followup / reject
    dimension: str
    text: str
    options: list[dict[str, str]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "dimension": self.dimension,
            "text": self.text,
            "options": self.options,
        }


@dataclass
class RouteOutcome:
    """三个节点跑完之后的最终路由结果。"""

    action: str                      # pass / clarify / reject
    band: str
    signals: IntentSignals
    question: ClarifyQuestion | None = None
    clarify_round: int = 0
    degraded: bool = False
    regress_reason: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload = self.signals.to_payload()
        payload.update(
            {
                "route": self.action,
                "band": self.band,
                "clarify_round": self.clarify_round,
                "degraded": self.degraded,
            }
        )
        if self.question is not None:
            payload["clarify_kind"] = self.question.kind
            payload["clarify_dimension"] = self.question.dimension
            payload["options"] = self.question.options
        if self.regress_reason:
            payload["regress_reason"] = self.regress_reason
        return payload


class IntentRouter:
    """意图路由层。持有节点一（识别器），自己实现节点二、三。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._recognizer = IntentRecognizer(settings)

    # ------------------------------------------------------------------
    async def route(
        self,
        *,
        runtime: Any,
        message: str,
        history: list[dict[str, Any]] | None = None,
    ) -> RouteOutcome:
        text = (message or "").strip()
        state = slot_store.load(runtime.thread_id, runtime.user.user_id)

        # ---- 先看这句话是不是在回答上一轮的澄清 ----
        if state.pending is not None:
            handled = self._resume_from_clarify(runtime, state, text)
            if handled is not None:
                slot_store.save(state)
                return handled

        # ---- 节点一：识别 ----
        runtime.set_stage("意图识别")
        signals = await self._recognizer.recognize(
            client=runtime.client,
            model=runtime.model,
            message=text,
            history=history,
            slot_hint=state.slots,
            clarify_round=state.clarify_round,
        )

        # 先把本轮置信度记进历史，**再**判断回归 ——
        # 否则"连续下降"永远慢一轮才被发现。
        state.push_confidence(signals.confidence)

        # ---- 节点二：门控 ----
        band = self._gate(signals.confidence)

        # ---- 边界③：越澄清越差 → 回流治理层 ----
        regressing, why = state.is_regressing(self._settings.intent_regress_patience)
        if regressing and band != BAND_DIRECT:
            outcome = self._report_regression(runtime, state, signals, why)
            slot_store.save(state)
            return outcome

        if band == BAND_DIRECT:
            outcome = self._pass(runtime, state, signals)
        elif band == BAND_REJECT:
            outcome = self._reject(runtime, state, signals, band=BAND_REJECT)
        else:
            outcome = self._clarify(runtime, state, signals, band)

        slot_store.save(state)
        return outcome

    # ------------------------------------------------------------------
    def _gate(self, confidence: float) -> str:
        """节点二：门控。只做一件事 —— 把置信度映射成出口档位。"""
        s = self._settings
        if confidence >= s.intent_direct_threshold:
            return BAND_DIRECT
        if confidence >= s.intent_confirm_threshold:
            return BAND_CONFIRM
        if confidence >= s.intent_reject_threshold:
            return BAND_OPTION
        return BAND_REJECT

    # ------------------------------------------------------------------
    def _pass(self, runtime: Any, state: slot_store.RouteState, signals: IntentSignals) -> RouteOutcome:
        """放行：清掉澄清轮次与待确认，但**保留槽位**给下一句用。

        注意返回的 signals.slots 是**累计后的槽位**，不是本轮新抽到的那些。
        下游（规划器 / 子 agent）拿到的必须是"用户到目前为止确认过的全部信息"，
        否则就会出现"澄清过了、执行时又忘了"。
        """
        merged = state.slots.merge(signals.slots)
        state.slots = merged
        state.clarify_round = 0
        state.pending = None
        state.last_intent = signals.intent if signals.intent not in ("", "unknown") else state.last_intent
        # 话题结束：回归历史与"已澄清维度"都清零，下一句是新话题
        state.confidence_history.clear()
        state.clarified_dimensions.clear()
        return RouteOutcome(
            action=PASS,
            band=BAND_DIRECT,
            signals=replace(signals, slots=merged),
            clarify_round=0,
        )

    def _clarify(
        self,
        runtime: Any,
        state: slot_store.RouteState,
        signals: IntentSignals,
        band: str,
        forced_dimension: str = "",
    ) -> RouteOutcome:
        """节点三：**只针对一个维度**生成澄清问题。"""
        next_round = state.clarify_round + 1
        max_rounds = self._settings.max_clarify_rounds

        # 边界①：超过硬上限（再多问一轮就明确放弃）
        if next_round > max_rounds + 1:
            return self._reject(runtime, state, signals, band="exhausted")

        # 边界①：超过软上限 → 降级为选项式（更容易答，也更容易收敛）
        degraded = next_round > max_rounds

        # 先把本轮抽到的槽位并进去，再判断"缺哪个维度"。
        # 不先合并的话，用户这句话里明说的课程号会被后面的推断忽略。
        merged = state.slots.merge(signals.slots)
        dimension = (
            forced_dimension
            or self._missing_dimension(merged, signals.intent)
            or DIM_CONFIRM
        )
        # 同一维度不重复问：问过就换下一个没问过的
        if dimension in state.clarified_dimensions and not forced_dimension:
            for candidate in DIMENSION_ORDER:
                if candidate not in state.clarified_dimensions:
                    dimension = candidate
                    break

        kind, question_text, options = self._build_question(
            dimension, signals, merged, degraded
        )
        if degraded and not options:
            # 已经问了几轮、这个维度又给不出选项 → 再问也没意义
            return self._reject(runtime, state, signals, band="exhausted")
        if degraded:
            kind = "option"

        state.slots = merged
        if signals.intent not in ("", "unknown"):
            state.last_intent = signals.intent
        state.clarify_round = next_round
        if dimension not in state.clarified_dimensions:
            state.clarified_dimensions.append(dimension)
        state.pending = slot_store.PendingClarify(
            kind=kind,
            dimension=dimension,
            intent=state.last_intent,
            slots=state.slots,
            assumption=signals.assumption,
            options=options,
            question=question_text,
        )

        return RouteOutcome(
            action=CLARIFY,
            band=band,
            signals=signals,
            question=ClarifyQuestion(
                kind=kind, dimension=dimension, text=question_text, options=options
            ),
            clarify_round=next_round,
            degraded=degraded,
        )

    def _reject(
        self,
        runtime: Any,
        state: slot_store.RouteState,
        signals: IntentSignals,
        *,
        band: str,
    ) -> RouteOutcome:
        """拒绝：听不懂就明说，并给能照着改的例子。"""
        state.clarify_round = 0
        state.pending = None
        state.last_intent = signals.intent
        state.confidence_history.clear()
        state.clarified_dimensions.clear()

        if band == "exhausted":
            text = (
                "我连着问了几轮还是没对上你的意思，先不猜了。\n\n"
                "直接写清楚要做的事就行，比如：\n"
                "• 帮我选 CS109 和 CS110\n"
                "• 我这学期选了多少学分"
            )
        else:
            hints = [c.label for c in signals.candidates][:3]
            if hints:
                lines = ["这句我没太听懂。你是不是想说：", ""]
                for index, label in enumerate(hints, 1):
                    lines.append(f"{index}. {label}")
                lines.append("")
                lines.append("直接说一句更具体的也行，比如「帮我选 CS109 和 CS110」。")
                text = "\n".join(lines)
            else:
                text = (
                    "这句我没太听懂。\n\n"
                    "可以这样说：\n"
                    "• 帮我选 CS109 和 CS110\n"
                    "• 我这学期选了多少学分\n"
                    "• 还有哪些三学分的课"
                )

        return RouteOutcome(
            action=REJECT,
            band=band,
            signals=signals,
            question=ClarifyQuestion(kind="reject", dimension="", text=text),
            clarify_round=0,
        )

    # ------------------------------------------------------------------
    def _report_regression(
        self,
        runtime: Any,
        state: slot_store.RouteState,
        signals: IntentSignals,
        why: str,
    ) -> RouteOutcome:
        """边界③：把"越澄清越差"回流治理层。

        这是**质量信号**而不是用户错误：说明识别侧的提示词或澄清话术
        有系统性问题，继续问下去只会让用户更烦。所以要留痕、要能统计。
        """
        runtime.bus.emit(
            EventKind.INTENT_REGRESSION,
            reason=why,
            clarify_round=state.clarify_round,
            confidence_history=list(state.confidence_history),
            clarified_dimensions=list(state.clarified_dimensions),
            slots=state.slots.to_dict(),
            intent=signals.intent,
            confidence=round(signals.confidence, 3),
        )
        runtime.governance.audit(
            {
                "run_id": runtime.run_id,
                "agent": "router",
                "tool": "intent_router",
                "channel": "router",
                "side_effect": False,
                "ok": False,
                "error": f"clarify_regression: {why}",
                "duration_ms": 0,
            }
        )

        state.clarify_round = 0
        state.pending = None
        state.confidence_history.clear()
        state.clarified_dimensions.clear()
        return RouteOutcome(
            action=REJECT,
            band="regress",
            signals=signals,
            question=ClarifyQuestion(
                kind="reject",
                dimension="",
                text=(
                    "我这边反复没理解对，先停一下，免得越问越偏。\n\n"
                    "直接写清楚要做的事就行，比如「帮我选 CS109 和 CS110」。"
                ),
            ),
            regress_reason=why,
        )

    # ------------------------------------------------------------------
    # 维度判定
    # ------------------------------------------------------------------
    @staticmethod
    def _missing_dimension(slots: SlotSet, intent: str) -> str:
        """还缺哪个维度？返回 "" 表示够了。"""
        if intent in ("", "unknown"):
            return DIM_INTENT

        if intent == "write":
            if slots.action not in WRITE_ACTIONS:
                return DIM_ACTION
            if not slots.course_codes:
                return DIM_COURSE
        elif intent == "query_compute":
            if slots.action == "":
                return DIM_ACTION
        elif intent == "meta":
            return ""

        # 用户说要处理 N 门课，但只识别到更少的课程号
        if slots.count and slots.count > len(slots.course_codes):
            return DIM_COURSE
        return ""

    def _build_question(
        self,
        dimension: str,
        signals: IntentSignals,
        slots: SlotSet,
        degraded: bool,
    ) -> tuple[str, str, list[dict[str, str]]]:
        """按维度生成（kind, 问句, 选项）。一个维度一个问题。"""
        if dimension == DIM_INTENT:
            options = _INTENT_OPTIONS
            text = "你是想查课程信息，还是想让我帮你选课 / 退课？"
            if degraded:
                text = "我们再确认一下方向：你要做的是下面哪一件？"
            return "option", text, options

        if dimension == DIM_ACTION:
            if signals.intent == "write" or slots.action in WRITE_ACTIONS:
                options = _WRITE_ACTION_OPTIONS
                text = "你想让我做哪件事？"
            else:
                options = _QUERY_ACTION_OPTIONS
                text = "你想查哪方面的信息？"
            if degraded:
                text = "换个更直接的方式：你要的是下面哪一个？"
            return "option", text, options

        if dimension == DIM_COURSE:
            action = slots.action
            verb = "退掉" if action == "drop" else "选上" if action == "enroll" else "处理"
            if slots.count and slots.course_codes and slots.count > len(slots.course_codes):
                have = "、".join(slots.course_codes)
                text = (
                    f"你说要处理 {slots.count} 门课，我目前只认到 {have}。"
                    f"还差哪{('几' if slots.count - len(slots.course_codes) > 1 else '一')}门？"
                    "说课程号就行。"
                )
            else:
                text = f"你想{verb}哪门课？说课程号就行，比如 CS109。"
            # 这一维度没有可枚举的选项，只能自由回答（追问式）
            return "followup", text, []

        # DIM_CONFIRM：都齐了，但置信度没到 0.95 → 整体确认一次
        text = self._confirm_text(signals, slots)
        return "confirm", text, _CONFIRM_OPTIONS

    @staticmethod
    def _confirm_text(signals: IntentSignals, slots: SlotSet) -> str:
        """假设性确认句。用模板而不是让模型现编：
        澄清发生在"没听懂"的时候，模型的措辞同样不可靠；
        而模板能保证**一定带上已抽到的槽位**。
        """
        action = slots.action
        codes = "、".join(slots.course_codes)

        if action == "enroll" and codes:
            return f"你是想让我帮你把 {codes} 选上，对吗？"
        if action == "drop" and codes:
            return f"你是想退掉 {codes}，对吗？"
        if action == "list_enrollments":
            return "你是想看这学期你已经选了哪些课，对吗？"
        if action == "check_credits":
            return "你是想知道现在选了多少学分、离上限还差多少，对吗？"
        if action == "check_conflict" and codes:
            return f"你是想确认 {codes} 之间有没有时间冲突，对吗？"
        if action == "ranking":
            return "你是想看哪些班次余量最紧张，对吗？"
        if action == "search":
            hint = f"（{codes}）" if codes else ""
            return f"你是想查一下有哪些课{hint}，对吗？"
        if signals.assumption:
            return f"你是想{signals.assumption}，对吗？"
        verb = {
            "write": "让我真的去执行选课或退课",
            "query_compute": "查课程或算一下选课相关的东西",
            "meta": "问这个系统怎么用",
        }.get(signals.intent, "做点什么")
        return f"你是想让我{verb}，对吗？"

    # ------------------------------------------------------------------
    # 澄清回答的处理
    # ------------------------------------------------------------------
    def _resume_from_clarify(
        self, runtime: Any, state: slot_store.RouteState, text: str
    ) -> RouteOutcome | None:
        """把用户这句话当作"对上一轮澄清的回答"来处理。

        返回 None 表示**没听懂在回答什么**，交给识别节点当新输入处理。
        """
        status = self._apply_clarify_answer(state, text)

        if status == "negate":
            state.pending = None
            state.clarify_round = 0
            state.confidence_history.clear()
            state.clarified_dimensions.clear()
            signals = self._signals_from_state(state, 0.2, "clarify_reply", "用户否定了假设")
            return RouteOutcome(
                action=REJECT,
                band=BAND_REJECT,
                signals=signals,
                question=ClarifyQuestion(
                    kind="reject",
                    dimension="",
                    text=(
                        "好，那我不动。\n\n"
                        "重新说一句就行，比如「帮我选 CS109 和 CS110」"
                        "或「我这学期选了多少学分」。"
                    ),
                ),
            )

        if status != "applied":
            return None

        # ---- 答完一个维度 → **回到识别判断还缺不缺** ----
        dimension = self._missing_dimension(state.slots, state.last_intent)
        if dimension == "":
            signals = self._signals_from_state(
                state, 0.97, "clarify_reply", "澄清后信息已齐"
            )
            return self._pass(runtime, state, signals)

        signals = self._signals_from_state(
            state, 0.60, "clarify_reply", f"还差「{dimension}」这一项"
        )
        return self._clarify(runtime, state, signals, BAND_OPTION, forced_dimension=dimension)

    def _apply_clarify_answer(self, state: slot_store.RouteState, text: str) -> str:
        """把回答落到**当前这一个维度**上。返回 applied / negate / ""。"""
        pending = state.pending
        if pending is None:
            return ""
        raw = text.strip()
        low = raw.lower()

        # 否定先判："不是"里含"是"
        if _is_negation(low):
            state.pending = None
            return "negate"

        # 点了选项（或回了序号）：选项自带 dimension/value，直接落位
        picked = self._pick_option(raw, pending.options)
        if picked is not None:
            return self._fill_from_option(state, picked, pending)

        # 肯定回答：确认维度直接通过；选项维度取第一项
        if _is_affirmation(low):
            if pending.dimension == DIM_CONFIRM:
                state.slots = state.slots.merge(pending.slots)
                if pending.intent:
                    state.last_intent = pending.intent
                state.pending = None
                return "applied"
            if pending.kind == "option" and pending.options:
                return self._fill_from_option(state, pending.options[0], pending)
            return ""

        # 自由文本：只按**当前维度**去解析，不去改别的维度
        dimension = pending.dimension
        rule = match_rules(raw)
        slots = extract_slots(raw)

        if dimension == DIM_COURSE:
            if slots.course_codes:
                state.slots = state.slots.merge(slots)
                state.pending = None
                return "applied"
            return ""

        if dimension == DIM_ACTION:
            if rule is not None and rule.slots.action:
                state.slots = state.slots.merge(SlotSet(action=rule.slots.action))
                if rule.slots.action in WRITE_ACTIONS:
                    state.last_intent = "write"
                elif rule.slots.action in QUERY_ACTIONS:
                    state.last_intent = "query_compute"
                state.pending = None
                return "applied"
            return ""

        if dimension == DIM_INTENT:
            if rule is not None:
                state.last_intent = rule.intent
                state.slots = state.slots.merge(rule.slots)
                state.pending = None
                return "applied"
            return ""

        # confirm 维度下用户说了别的话 → 当作没回答，交给识别
        return ""

    def _fill_from_option(
        self, state: slot_store.RouteState, option: dict[str, str], pending: slot_store.PendingClarify
    ) -> str:
        dimension = option.get("dimension") or pending.dimension
        value = str(option.get("value") or "")
        label = str(option.get("label") or "")

        if dimension == DIM_INTENT:
            if value:
                state.last_intent = value
        elif dimension == DIM_ACTION:
            if not value:
                return ""
            state.slots = state.slots.merge(SlotSet(action=value))
            if value in WRITE_ACTIONS:
                state.last_intent = "write"
            elif value in QUERY_ACTIONS:
                state.last_intent = "query_compute"
        elif dimension == DIM_COURSE:
            codes = extract_slots(label).course_codes
            if not codes and value:
                codes = [value.upper()]
            if not codes:
                return ""
            state.slots = state.slots.merge(SlotSet(course_codes=codes))
        elif dimension == DIM_CONFIRM:
            if value == "no":
                state.pending = None
                return "negate"
            state.slots = state.slots.merge(pending.slots)
            if pending.intent:
                state.last_intent = pending.intent
        else:
            return ""

        state.pending = None
        return "applied"

    @staticmethod
    def _signals_from_state(
        state: slot_store.RouteState, confidence: float, source: str, reason: str
    ) -> IntentSignals:
        return IntentSignals(
            intent=state.last_intent or "query_compute",
            confidence=confidence,
            reason=reason,
            slots=state.slots,
            source=source,
        )

    @staticmethod
    def _pick_option(text: str, options: list[dict[str, str]]) -> dict[str, str] | None:
        """判断用户这句话是不是在选某个选项。

        必须**严格**：只有"整句就是一个序号"才算选择。
        曾经用 `"第三" in text` 判断，结果"第三句话更模糊了"被当成"选第 3 项"，
        把一次澄清静默地变成了放行 —— 这类误判比不识别更危险。
        """
        if not options:
            return None
        raw = text.strip()
        if not raw or len(raw) > 10:
            return None

        for pattern in _ORDINAL_PATTERNS:
            match = pattern.match(raw)
            if match:
                index = int(match.group(1)) - 1
                if 0 <= index < len(options):
                    return options[index]

        match = _CN_ORDINAL_RE.match(raw)
        if match:
            index = _CN_ORDINAL[match.group(1)] - 1
            if 0 <= index < len(options):
                return options[index]

        for item in options:
            label = str(item.get("label") or "").strip()
            if len(label) >= 4 and label in raw:
                return item
        return None


# ---------------------------------------------------------------------------
def _is_affirmation(text: str) -> bool:
    stripped = text.strip("。.!！,，~ ")
    if not stripped:
        return False
    return any(stripped == word or stripped.startswith(word) for word in _AFFIRMATIONS)


def _is_negation(text: str) -> bool:
    stripped = text.strip("。.!！,，~ ")
    if not stripped:
        return False
    return any(stripped == word or stripped.startswith(word) for word in _NEGATIONS)
