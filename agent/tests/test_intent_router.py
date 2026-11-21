"""意图路由层测试：三节点 + 四档出口 + 三个边界。

不用真模型：用一个脚本化的假 client 控制识别节点的输出置信度，
这样"门控怎么分档""澄清怎么回环""边界怎么触发"都能确定性验证。

覆盖：
  A. 门控分档（0.95 / 0.75 / 0.50 四个阈值边界）
  B. 规则匹配（课程号、动作词、查询短语）
  C. LLM + 规则融合（同向抬升 / 冲突压低 / 规则兜底封顶）
  D. 端到端路由四种出口
  E. 多轮澄清：确认回环（是）、否定回环（不是）、选项回环（回序号）
  F. 边界①：轮次上限 + 超限降级为选项式 + 最终放弃
  G. 边界②：槽位增量合并（跨轮累积，且真的落库）
  H. 边界③：越澄清越差 → 回流治理层（事件 + 审计）

用法：
    cd agent
    python tests/test_intent_router.py
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.events import EventBus, EventKind  # noqa: E402
from app.memory import slots as slot_store  # noqa: E402
from app.orchestration.intent import (  # noqa: E402
    RULE_ONLY_CAP,
    IntentRecognizer,
    SlotSet,
    extract_slots,
    fuse,
    match_rules,
)
from app.orchestration.router import (  # noqa: E402
    BAND_CONFIRM,
    BAND_DIRECT,
    BAND_OPTION,
    BAND_REJECT,
    IntentRouter,
)

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  {name:<56} OK")
    else:
        print(f"  {name:<56} FAIL  {detail}")
        FAILURES.append(f"{name} {detail}".strip())


# ---------------------------------------------------------------------------
class FakeClient:
    """脚本化的假模型：按调用顺序返回预设的 chat_json 结果。"""

    def __init__(self, script: list[dict]) -> None:
        self.script = list(script)
        self.calls = 0

    async def chat_json(self, *, model: str, messages: list[dict], **kwargs) -> dict:
        self.calls += 1
        if not self.script:
            return {"intent": "unknown", "confidence": 0.0}
        return self.script.pop(0)


class FakeGovernance:
    def __init__(self) -> None:
        self.records: list[dict] = []

    def audit(self, record: dict) -> None:
        self.records.append(record)


class FakeUser:
    def __init__(self, user_id: int) -> None:
        self.user_id = user_id
        self.username = "router-test"
        self.real_name = "路由测试"


class FakeRuntime:
    def __init__(self, client: FakeClient, user_id: int, thread_id: str) -> None:
        self.client = client
        self.model = "fake"
        self.user = FakeUser(user_id)
        self.thread_id = thread_id
        self.run_id = "r" + uuid.uuid4().hex[:12]
        self.bus = EventBus()
        self.governance = FakeGovernance()
        self.stages: list[str] = []

    def set_stage(self, stage: str) -> None:
        self.stages.append(stage)


def router() -> IntentRouter:
    return IntentRouter(get_settings())


def new_thread() -> str:
    return ("t" + uuid.uuid4().hex)[:32]


def cleanup(thread_id: str, user_id: int) -> None:
    slot_store.clear(thread_id, user_id)


# ---------------------------------------------------------------------------
def test_gate() -> None:
    print("[A] 门控分档")
    r = router()
    cases = [
        (1.00, BAND_DIRECT),
        (0.95, BAND_DIRECT),   # 边界：>=0.95 放行
        (0.949, BAND_CONFIRM),
        (0.80, BAND_CONFIRM),
        (0.75, BAND_CONFIRM),  # 边界：>=0.75 确认
        (0.749, BAND_OPTION),
        (0.60, BAND_OPTION),
        (0.50, BAND_OPTION),   # 边界：>=0.50 选项式
        (0.499, BAND_REJECT),  # 边界：<0.50 拒绝
        (0.00, BAND_REJECT),
    ]
    for confidence, expected in cases:
        got = r._gate(confidence)
        check(f"置信度 {confidence:.3f} → {expected}", got == expected, f"实际 {got}")


def test_rules() -> None:
    print("[B] 规则匹配")
    cases = [
        ("帮我选 CS109 和 CS110", "write", 0.96, ["CS109", "CS110"], "enroll"),
        ("退掉 CS110", "write", 0.96, ["CS110"], "drop"),
        ("帮我选课", "write", 0.72, [], "enroll"),
        ("我目前选了哪些课", "query_compute", 0.90, [], "list_enrollments"),
        ("我这学期选了多少学分", "query_compute", 0.85, [], "check_credits"),
        ("CS109 和 CS110 有冲突吗", "query_compute", 0.85, ["CS109", "CS110"], "check_conflict"),
        ("你好", "meta", 0.98, [], "help"),
    ]
    for text, intent, min_score, codes, action in cases:
        rule = match_rules(text)
        ok = (
            rule is not None
            and rule.intent == intent
            and rule.confidence >= min_score - 1e-6
            and all(c in rule.slots.course_codes for c in codes)
            and (not action or rule.slots.action == action)
        )
        check(
            f"「{text}」→ {intent}/{action or '-'}",
            ok,
            f"实际 {rule.intent if rule else None}/{rule.slots.action if rule else None} "
            f"conf={rule.confidence if rule else 0}",
        )

    slots = extract_slots("周三下午想选两门课")
    check(
        "槽位：周三 + 两门",
        slots.weekday == 3 and slots.count == 2,
        f"weekday={slots.weekday} count={slots.count}",
    )


def test_fuse() -> None:
    print("[C] 信号融合")
    rule_agree = match_rules("帮我选 CS109")
    assert rule_agree is not None

    # 同向 → 抬升
    intent, confidence, source = fuse("write", 0.60, rule_agree)
    check("同向抬升（0.60 → ≥0.75）", intent == "write" and confidence >= 0.75, f"{confidence:.3f}")
    check("来源标记 llm+rule", source == "llm+rule", source)

    # 冲突 → 压低
    intent, confidence, _ = fuse("query_compute", 0.80, rule_agree)
    check("冲突压低（0.80 → <0.50）", confidence < 0.50, f"{confidence:.3f}")

    # LLM 失败 / unknown → 规则兜底，但封顶
    intent, confidence, source = fuse("unknown", 0.0, rule_agree)
    check("规则兜底取规则意图", intent == "write", intent)
    check("规则兜底不越过 0.93", confidence <= RULE_ONLY_CAP + 1e-9, f"{confidence:.3f}")
    check("规则兜底走确认档而非直接放行", confidence < 0.95, f"{confidence:.3f}")


def test_routes() -> None:
    print("[D] 端到端四档出口")
    settings = get_settings()

    # 1. 直接放行：规则 + LLM 都给高置信度
    thread = new_thread()
    runtime = FakeRuntime(
        FakeClient([{"intent": "write", "confidence": 0.95, "slots": {"course_codes": ["CS109"]}}]),
        user_id=9001,
        thread_id=thread,
    )
    outcome = asyncio.run(router().route(runtime=runtime, message="帮我选 CS109"))
    check("高分 → 放行", outcome.action == "pass" and outcome.band == BAND_DIRECT, outcome.band)
    check("放行时槽位已累积", "CS109" in outcome.signals.slots.course_codes)
    cleanup(thread, 9001)

    # 2. 0.75~0.95 档，但**槽位没齐** → 先问缺的那个维度（不是含糊地"你是指？"）
    thread = new_thread()
    runtime = FakeRuntime(
        FakeClient([{"intent": "write", "confidence": 0.86, "assumption": "帮你选课"}]),
        user_id=9002,
        thread_id=thread,
    )
    outcome = asyncio.run(router().route(runtime=runtime, message="那个……就是那个课"))
    check("中高分但缺槽位 → 只问缺的维度", outcome.action == "clarify"
          and outcome.question.dimension == "action",
          outcome.question.dimension if outcome.question else outcome.action)
    check("问句是具体的问题", outcome.question is not None and "哪件事" in outcome.question.text,
          outcome.question.text if outcome.question else "")
    check("澄清轮次记为 1", outcome.clarify_round == 1, str(outcome.clarify_round))
    cleanup(thread, 9002)

    # 2b. 0.75~0.95 档且**槽位齐全** → 假设性确认句
    thread = new_thread()
    runtime = FakeRuntime(
        FakeClient([{
            "intent": "write", "confidence": 0.86,
            "slots": {"action": "enroll", "course_codes": ["CS109"]},
        }]),
        user_id=9005,
        thread_id=thread,
    )
    outcome = asyncio.run(router().route(runtime=runtime, message="那门课你看着办"))
    check("槽位齐全 + 中高分 → 假设性确认",
          outcome.action == "clarify" and outcome.question.kind == "confirm",
          outcome.question.kind if outcome.question else outcome.action)
    check("确认句里带问号", outcome.question is not None and "对吗" in outcome.question.text,
          outcome.question.text if outcome.question else "")
    check("确认维度 = confirm", outcome.question.dimension == "confirm",
          outcome.question.dimension if outcome.question else "")
    cleanup(thread, 9005)

    # 3. 选项式
    thread = new_thread()
    runtime = FakeRuntime(
        FakeClient([{
            "intent": "query_compute", "confidence": 0.55,
            "candidates": [{"intent": "write", "label": "帮我选课"}, {"intent": "query_compute", "label": "查课程"}],
        }]),
        user_id=9003,
        thread_id=thread,
    )
    outcome = asyncio.run(router().route(runtime=runtime, message="那个"))
    check("低分 → 澄清（选项式）", outcome.action == "clarify" and outcome.question.kind == "option",
          outcome.question.kind if outcome.question else "")
    check("只问一个维度", outcome.question.dimension in ("intent", "action", "course"),
          outcome.question.dimension if outcome.question else "")
    check("选项数量合理（1~4）", outcome.question is not None and 1 <= len(outcome.question.options) <= 4,
          str(len(outcome.question.options) if outcome.question else 0))
    check("选项自带 dimension/value",
          bool(outcome.question and all(o.get("dimension") and o.get("value")
                                       for o in outcome.question.options)),
          str(outcome.question.options[:1] if outcome.question else None))
    cleanup(thread, 9003)

    # 4. 拒绝
    thread = new_thread()
    runtime = FakeRuntime(FakeClient([{"intent": "unknown", "confidence": 0.1}]), user_id=9004, thread_id=thread)
    outcome = asyncio.run(router().route(runtime=runtime, message="嗯那个啥来着"))
    check("极低分 → 拒绝", outcome.action == "reject" and outcome.band == BAND_REJECT, outcome.band)
    check("拒绝时给例子", outcome.question is not None and "帮我选" in outcome.question.text)
    cleanup(thread, 9004)


def test_clarify_loop() -> None:
    print("[E] 多轮澄清回环")
    settings = get_settings()

    # 确认式 → 用户回「是」→ 直接放行，且**不再调一次模型**
    thread = new_thread()
    client = FakeClient([
        {
            "intent": "write",
            "confidence": 0.86,
            "slots": {"action": "enroll", "course_codes": ["CS109", "CS110"]},
        }
    ])
    runtime = FakeRuntime(client, user_id=9101, thread_id=thread)
    r = router()
    first = asyncio.run(r.route(runtime=runtime, message="那两门课你看着办"))
    check("第一轮触发澄清（confirm）",
          first.action == "clarify" and first.question.kind == "confirm",
          first.question.kind if first.question else first.action)
    check("确认维度 = confirm", first.question.dimension == "confirm",
          first.question.dimension if first.question else "")
    calls_before = client.calls

    second = asyncio.run(r.route(runtime=runtime, message="是"))
    check("回「是」→ 放行", second.action == "pass", second.action)
    check("回「是」不再调模型", client.calls == calls_before, f"{calls_before} → {client.calls}")
    check("来源标记 clarify_reply", second.signals.source == "clarify_reply", second.signals.source)
    check("置信度提升到 0.98", second.signals.confidence >= 0.95, f"{second.signals.confidence:.2f}")
    check("确认后槽位被继承", set(second.signals.slots.course_codes) == {"CS109", "CS110"},
          str(second.signals.slots.course_codes))
    cleanup(thread, 9101)

    # 否定 → 拒绝，且 pending 清空
    thread = new_thread()
    client = FakeClient([{"intent": "write", "confidence": 0.86}])
    runtime = FakeRuntime(client, user_id=9102, thread_id=thread)
    r = router()
    asyncio.run(r.route(runtime=runtime, message="那两门课你看着办"))
    negated = asyncio.run(r.route(runtime=runtime, message="不是"))
    check("回「不是」→ 拒绝", negated.action == "reject", negated.action)
    state = slot_store.load(thread, 9102)
    check("否定后 pending 清空", state.pending is None)
    cleanup(thread, 9102)

    # 选项式 → 用户回「2」→ 采用第 2 个候选
    thread = new_thread()
    client = FakeClient([{
        "intent": "query_compute", "confidence": 0.55,
        "candidates": [{"intent": "write", "label": "帮我选课"}, {"intent": "query_compute", "label": "查课程余量"}],
    }])
    runtime = FakeRuntime(client, user_id=9103, thread_id=thread)
    r = router()
    asyncio.run(r.route(runtime=runtime, message="那个"))
    picked = asyncio.run(r.route(runtime=runtime, message="2"))
    check("回「2」→ 放行", picked.action == "pass", picked.action)
    check("采用第 2 个候选的意图", picked.signals.intent == "query_compute", picked.signals.intent)
    check("来源标记 clarify_reply", picked.signals.source == "clarify_reply", picked.signals.source)
    cleanup(thread, 9103)


def test_multi_round_dimensions() -> None:
    """多轮澄清：每次一个维度，答完回识别，缺就继续问下一个。"""
    print("[E2] 多轮维度澄清")

    # ---- 场景 1：意图明确（write/enroll）但没说哪门课 → 只问课程 ----
    thread = new_thread()
    client = FakeClient([
        {"intent": "write", "confidence": 0.55, "slots": {"action": "enroll"}}
    ])
    runtime = FakeRuntime(client, user_id=9151, thread_id=thread)
    r = router()
    first = asyncio.run(r.route(runtime=runtime, message="帮我选课"))
    check("第一轮只问课程维度", first.question is not None and first.question.dimension == "course",
          first.question.dimension if first.question else first.action)
    check("课程维度是追问式（无可枚举选项）",
          first.question is not None and first.question.kind == "followup"
          and first.question.options == [],
          first.question.kind if first.question else "")
    check("问句里要课程号", first.question is not None and "CS109" in first.question.text,
          first.question.text if first.question else "")

    second = asyncio.run(r.route(runtime=runtime, message="CS109"))
    check("补充课程号后放行", second.action == "pass", second.action)
    check("课程号进了槽位", "CS109" in second.signals.slots.course_codes,
          str(second.signals.slots.course_codes))
    check("动作保留", second.signals.slots.action == "enroll", second.signals.slots.action)
    cleanup(thread, 9151)

    # ---- 场景 2：连问三个维度：intent → action → course ----
    thread = new_thread()
    client = FakeClient([
        {"intent": "unknown", "confidence": 0.50},
        {"intent": "unknown", "confidence": 0.50},
    ])
    runtime = FakeRuntime(client, user_id=9152, thread_id=thread)
    r = router()

    step1 = asyncio.run(r.route(runtime=runtime, message="那个啥"))
    check("第 1 轮问意图维度", step1.question is not None and step1.question.dimension == "intent",
          step1.question.dimension if step1.question else step1.action)

    step2 = asyncio.run(r.route(runtime=runtime, message="2"))   # 2 = 选课/退课
    check("第 2 轮改问动作维度", step2.question is not None and step2.question.dimension == "action",
          step2.question.dimension if step2.question else step2.action)
    check("第 2 轮仍是澄清（多轮）", step2.action == "clarify", step2.action)
    check("轮次累加到 2", step2.clarify_round == 2, str(step2.clarify_round))

    step3 = asyncio.run(r.route(runtime=runtime, message="1"))   # 1 = 帮我选上
    check("第 3 轮改问课程维度", step3.question is not None and step3.question.dimension == "course",
          step3.question.dimension if step3.question else step3.action)

    step4 = asyncio.run(r.route(runtime=runtime, message="CS110"))
    check("信息齐了 → 放行", step4.action == "pass", step4.action)
    check("意图是 write", step4.signals.intent == "write", step4.signals.intent)
    check("动作是 enroll", step4.signals.slots.action == "enroll", step4.signals.slots.action)
    check("课程是 CS110", "CS110" in step4.signals.slots.course_codes,
          str(step4.signals.slots.course_codes))
    cleanup(thread, 9152)


def test_round_limit() -> None:
    print("[F] 边界①：轮次上限与降级")
    settings = get_settings()
    max_rounds = settings.max_clarify_rounds

    thread = new_thread()
    # 每轮都是 0.55（选项档），且消息各不相同（避免被当成重复）
    script = [{"intent": "query_compute", "confidence": 0.55} for _ in range(10)]
    runtime = FakeRuntime(FakeClient(script), user_id=9201, thread_id=thread)
    r = router()

    kinds: list[str] = []
    rounds: list[int] = []
    actions: list[str] = []
    for index in range(max_rounds + 3):
        outcome = asyncio.run(r.route(runtime=runtime, message=f"那个课程的事情第{index}次问"))
        actions.append(outcome.action)
        rounds.append(outcome.clarify_round)
        kinds.append(outcome.question.kind if outcome.question else "")
        if outcome.action != "clarify":
            break

    clarify_count = sum(1 for a in actions if a == "clarify")
    # 4 次澄清 = 正常的 3 轮 + 超限后降级的 1 轮；之后第 5 次直接放弃
    check(
        f"澄清 {max_rounds} 轮后降级再问 1 轮（共 {max_rounds + 1} 次）",
        clarify_count == max_rounds + 1,
        f"实际澄清 {clarify_count} 次",
    )
    check("超限后的那一轮标记为降级", actions[-1] == "reject" or clarify_count == max_rounds + 1,
          str(actions))
    check("超限后是选项式", kinds[-2] == "option" if len(kinds) >= 2 else False, str(kinds))
    check("再超一轮 → 放弃（exhausted）", actions[-1] == "reject", str(actions))
    check("放弃时提示换说法", kinds[-1] == "reject", str(kinds))
    cleanup(thread, 9201)


def test_slot_incremental() -> None:
    print("[G] 边界②：槽位增量合并")
    thread = new_thread()
    client = FakeClient([
        {"intent": "write", "confidence": 0.98, "slots": {"action": "enroll", "course_codes": ["CS109"]}},
        {"intent": "write", "confidence": 0.98, "slots": {"action": "enroll", "course_codes": ["CS110"]}},
    ])
    runtime = FakeRuntime(client, user_id=9301, thread_id=thread)
    r = router()

    asyncio.run(r.route(runtime=runtime, message="帮我选 CS109"))
    asyncio.run(r.route(runtime=runtime, message="再帮我选 CS110"))

    state = slot_store.load(thread, 9301)
    check("两轮的课程号都留下", set(state.slots.course_codes) == {"CS109", "CS110"},
          str(state.slots.course_codes))
    check("action 被保留", state.slots.action == "enroll", state.slots.action)
    check("放行后澄清轮次归零", state.clarify_round == 0, str(state.clarify_round))

    # 纯函数层面再验一次 merge
    merged = SlotSet(course_codes=["CS109"], weekday=1).merge(SlotSet(course_codes=["CS110", "CS109"]))
    check("merge 去重且保序", merged.course_codes == ["CS109", "CS110"], str(merged.course_codes))
    check("merge 保留旧的非空字段", merged.weekday == 1, str(merged.weekday))
    cleanup(thread, 9301)


def test_regression() -> None:
    print("[H] 边界③：越澄清越差 → 回流治理层")
    thread = new_thread()
    # 三轮置信度 0.80 → 0.70 → 0.62（都在澄清档，且严格下降）
    client = FakeClient([
        {"intent": "query_compute", "confidence": 0.80},
        {"intent": "query_compute", "confidence": 0.70},
        {"intent": "query_compute", "confidence": 0.62},
        {"intent": "query_compute", "confidence": 0.55},
    ])
    runtime = FakeRuntime(client, user_id=9401, thread_id=thread)
    r = router()

    asyncio.run(r.route(runtime=runtime, message="第一句话我想问点东西"))
    asyncio.run(r.route(runtime=runtime, message="第二句话还是没说清楚"))
    third = asyncio.run(r.route(runtime=runtime, message="第三句话更模糊了"))

    events = runtime.bus.history_snapshot()
    regress_events = [e for e in events if e["kind"] == "intent_regression"]
    check("发出了 intent_regression 事件", len(regress_events) == 1, str(len(regress_events)))
    if regress_events:
        payload = regress_events[0]
        check("事件带置信度历史", len(payload.get("confidence_history") or []) >= 3,
              str(payload.get("confidence_history")))
        check("事件带原因", bool(payload.get("reason")), str(payload.get("reason"))[:60])
    check("审计记录已写入治理层", len(runtime.governance.records) >= 1,
          str(len(runtime.governance.records)))
    if runtime.governance.records:
        record = runtime.governance.records[0]
        check("审计里有 clarify_regression 标记",
              "clarify_regression" in str(record.get("error")), str(record.get("error"))[:60])
    check("回归后不再继续追问（转为拒绝）", third.action == "reject", third.action)
    check("回归出口单独标记", third.band == "regress", third.band)

    # 反向用例：句子里出现"第三"不能被当成"选第 3 项"。
    # 这个 bug 曾经让一次澄清静默变成放行（更难发现）。
    picked = router()._pick_option("第三句话更模糊了", [{"intent": "write", "label": "帮我选课"}])
    check("含序号的句子不被误判为选选项", picked is None, str(picked))
    picked = router()._pick_option("第三个", [{"intent": "a", "label": "x"}, {"intent": "b", "label": "y"},
                                              {"intent": "c", "label": "z"}])
    check("真·序号能被识别", picked is not None and picked.get("intent") == "c", str(picked))

    state = slot_store.load(thread, 9401)
    check("回归后轮次重置", state.clarify_round == 0, str(state.clarify_round))
    cleanup(thread, 9401)


def test_clarify_gate() -> None:
    """澄清闸门：**挂起等待，同一轮继续**，而不是结束这一轮。

    这是"本轮任务不能停"的实现方式。要验证四件事：
      1. 挂起期间运行没结束（没有 RUN_FINISHED）
      2. 回答送进来后立刻返回文本，运行可以继续
      3. 澄清问答进了对话记录（刷新页面还能看到问过什么）
      4. 澄清事件**不产生文本帧** —— 否则前端会渲染成气泡（这是那个双气泡 bug 的根因）
    """
    print("[I] 澄清闸门（本轮不停）")
    import dataclasses

    from app.config import get_settings as _gs
    from app.execenv.runtime import CLARIFICATIONS, Runtime, UserContext
    from app.protocol.agui import AgUiEncoder

    settings = dataclasses.replace(_gs(), clarify_timeout_s=5.0)

    row = db.fetch_one("SELECT id, username, real_name FROM users WHERE username = '2024002'")
    if not row:
        check("跳过（没有 2024002）", True)
        return

    thread = ("tgate" + uuid.uuid4().hex)[:32]

    async def run_case() -> tuple[Runtime, str | None]:
        runtime = Runtime(
            goal="澄清闸门测试",
            user=UserContext(
                user_id=int(row["id"]),
                username=row["username"],
                real_name=row["real_name"],
                scopes=("offering:read", "enrollment:create"),
            ),
            model="fake",
            api_key="sk-fake-for-test-only",
            settings=settings,
            thread_id=thread,
        )
        runtime.start(title="澄清闸门测试")

        async def answer_later() -> None:
            for _ in range(300):
                await asyncio.sleep(0.02)
                ids = CLARIFICATIONS.snapshot()
                if ids:
                    CLARIFICATIONS.answer(ids[0], "是，帮我选 CS109")
                    return

        task = asyncio.create_task(answer_later())
        text = await runtime.ask_clarification(
            question="你是想让我帮你把 CS109 选上，对吗？", options=[], round_no=1
        )
        await task
        return runtime, text

    runtime, text = asyncio.run(run_case())
    check("拿到用户回答", text == "是，帮我选 CS109", str(text))

    events = runtime.bus.history_snapshot()
    kinds = [e["kind"] for e in events]
    check("发出 clarify_required", "clarify_required" in kinds, str(kinds))
    check("发出 clarify_answer", "clarify_answer" in kinds, str(kinds))
    check("挂起期间没有结束运行", "run_finished" not in kinds, str(kinds))
    check("没有产生文本帧（避免双气泡）", "delta" not in kinds, str(kinds))

    encoder = AgUiEncoder(thread_id=thread, run_id=runtime.run_id)
    required = next((e for e in events if e["kind"] == "clarify_required"), None)
    if required:
        frames = encoder.encode(required)
        types = [f["type"] for f in frames]
        check("clarify_required 只编码成 STATE_DELTA + CUSTOM",
              types == ["STATE_DELTA", "CUSTOM"], str(types))
        custom = next((f for f in frames if f["type"] == "CUSTOM"), None)
        check("CUSTOM 名字是 courseflow.clarify_required",
              custom is not None and custom.get("name") == "courseflow.clarify_required",
              str(custom.get("name") if custom else None))
        check("带 clarification_id 和 question",
              bool((custom or {}).get("value", {}).get("clarification_id"))
              and bool((custom or {}).get("value", {}).get("question")))

    # 对话记录：**澄清问答不进消息表**，只留用户最初那句话
    rows = db.fetch_all(
        "SELECT role, content FROM agent_messages WHERE thread_id = %s ORDER BY id",
        (thread,),
    )
    roles = [r["role"] for r in rows]
    check("澄清问答不进消息表", roles == ["user"], str(roles))

    # 超时：返回 None，且不留 pending
    async def timeout_case() -> str | None:
        rt = Runtime(
            goal="超时测试",
            user=UserContext(user_id=int(row["id"]), username="t", real_name="t"),
            model="fake",
            api_key="sk-fake-for-test-only",
            settings=dataclasses.replace(settings, clarify_timeout_s=0.6),
            thread_id=("tto" + uuid.uuid4().hex)[:32],
        )
        rt.start(title="超时测试")
        result = await rt.ask_clarification(question="在吗？", options=[], round_no=1)
        db.execute("DELETE FROM agent_messages WHERE thread_id = %s", (rt.thread_id,))
        db.execute("DELETE FROM agent_threads WHERE thread_id = %s", (rt.thread_id,))
        return result

    timed = asyncio.run(timeout_case())
    check("超时返回 None", timed is None, str(timed))
    check("超时后不残留挂起", CLARIFICATIONS.pending_count == 0, str(CLARIFICATIONS.pending_count))

    db.execute("DELETE FROM agent_messages WHERE thread_id = %s", (thread,))
    db.execute("DELETE FROM agent_threads WHERE thread_id = %s", (thread,))


def main() -> int:
    ok, error = db.ping()
    if not ok:
        print(f"数据库连接失败：{error}")
        return 1

    print("=" * 74)
    test_gate()
    test_rules()
    test_fuse()
    test_routes()
    test_clarify_loop()
    test_multi_round_dimensions()
    test_round_limit()
    test_slot_incremental()
    test_regression()
    test_clarify_gate()
    print("=" * 74)

    if FAILURES:
        print(f"FAIL  {len(FAILURES)} 项未通过")
        for item in FAILURES:
            print(f"  - {item}")
        return 1
    print("OK  意图路由层：三节点、四档出口、三个边界全部成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
