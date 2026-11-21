"""执行环境层。

定义"一次运行"拥有哪些资源：
  - 事件总线、轨迹录制器、治理器
  - 推理客户端（带用户自己的 Key）
  - 工具上下文工厂（每个子 agent 一个）
  - 审批闸门（写操作在这里阻塞等用户点击）

审批为什么要在这里：它既是"执行环境"的一部分（运行需要人的介入），
也横跨工具层（由工具调用触发）与 API 层（由 HTTP 请求决议）。
放在 execenv 是因为**一次运行 = 一个 Runtime**，审批天然以 run 为粒度。
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..evaluation.langsmith_hook import LangSmithHook
from ..evaluation.tracer import Tracer, new_run_id
from ..events import EventBus, EventKind
from ..governance.core import Governance
from ..llm.client import Completion, DeepSeekClient
from ..tools.context import ToolContext

logger = logging.getLogger("courseflow.agent")


@dataclass
class UserContext:
    """从 Go 后端转发过来的已认证身份。"""

    user_id: int
    username: str
    real_name: str
    scopes: tuple[str, ...] = ()
    # 用户自己的 JWT —— 写操作回调用它，身份不放大
    token: str = ""


class ApprovalRegistry:
    """等待审批的请求。

    进程级单例：审批决议来自另一个 HTTP 请求（前端点确认），
    必须能从 /approvals/{id}/decide 找到当初挂起的那个 Future。
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[bool]] = {}

    def create(self, loop: asyncio.AbstractEventLoop) -> tuple[str, asyncio.Future[bool]]:
        approval_id = uuid.uuid4().hex[:24]
        future: asyncio.Future[bool] = loop.create_future()
        self._pending[approval_id] = future
        return approval_id, future

    def decide(self, approval_id: str, approved: bool) -> bool:
        future = self._pending.pop(approval_id, None)
        if future is None or future.done():
            return False
        loop = future.get_loop()
        loop.call_soon_threadsafe(_resolve, future, approved)
        return True

    def discard(self, approval_id: str) -> None:
        self._pending.pop(approval_id, None)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def snapshot(self) -> list[str]:
        """当前还没被处理的审批 id。

        暴露出来是为了**手动兜底**：万一前端面板没弹（或者用户直接关了页面），
        还能在 /agent/health 里查到 id，用 curl 提交决议，不至于整次运行白等 90 秒。
        """
        return list(self._pending.keys())


def _resolve(future: asyncio.Future[bool], approved: bool) -> None:
    if not future.done():
        future.set_result(approved)


APPROVALS = ApprovalRegistry()

# 澄清维度的中文名：给阶段文案用，让人一眼知道"现在在确认什么"
_DIMENSION_LABELS = {
    "intent": "意图",
    "action": "动作",
    "course": "课程",
    "confirm": "整体确认",
}


class ClarifyRegistry:
    """澄清闸门的挂起登记表。和审批同构，只是等的是**文本**而不是布尔。

    为什么要跨对象登记：`ask_clarification` 挂在某个 Runtime 的协程里等，
    而回答是从另一次 HTTP 请求进来的。两边必须能找到同一个 Future。
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[str]] = {}

    def create(self, loop: asyncio.AbstractEventLoop) -> tuple[str, asyncio.Future[str]]:
        clarification_id = "c" + uuid.uuid4().hex[:12]
        future: asyncio.Future[str] = loop.create_future()
        self._pending[clarification_id] = future
        return clarification_id, future

    def answer(self, clarification_id: str, text: str) -> bool:
        future = self._pending.pop(clarification_id, None)
        if future is None or future.done():
            return False
        loop = future.get_loop()
        loop.call_soon_threadsafe(_resolve_text, future, text)
        return True

    def discard(self, clarification_id: str) -> None:
        self._pending.pop(clarification_id, None)

    @property
    def pending_count(self) -> int:
        return len(self._pending)

    def snapshot(self) -> list[str]:
        return list(self._pending.keys())


CLARIFICATIONS = ClarifyRegistry()


def _resolve_text(future: asyncio.Future[str], text: str) -> None:
    if not future.done():
        future.set_result(text)


class Runtime:
    """一次运行的执行环境。"""

    def __init__(
        self,
        *,
        goal: str,
        user: UserContext,
        model: str,
        api_key: str,
        settings: Settings,
        thread_id: str = "",
    ) -> None:
        self.run_id = new_run_id()
        self.thread_id = thread_id or f"t{self.run_id[:20]}"
        self.goal = goal
        self.user = user
        self.model = model
        self.settings = settings

        self.bus = EventBus()
        self.tracer = Tracer(run_id=self.run_id, user_id=user.user_id, model=model)
        self.governance = Governance(run_id=self.run_id, settings=settings, bus=self.bus)
        self.langsmith = LangSmithHook(settings)
        self.client = DeepSeekClient(api_key, timeout_s=settings.request_timeout_s)
        self._closed = False
        # 当前阶段。SSE 心跳和界面都读它，用来回答"现在到底在等什么"。
        self.stage = "启动中"

    # ------------------------------------------------------------------
    @property
    def scopes(self) -> tuple[str, ...]:
        return self.user.scopes

    def set_stage(self, stage: str) -> None:
        """标记"现在在做什么"，并发一条自定义事件。

        为什么这件事重要：Agent 一次运行里大部分时间花在等模型上，
        这段时间没有任何工具事件。没有阶段标记，界面上就是"停住了"，
        用户无法区分"在思考"和"已经死了"。

        注意字段名必须是 `stage`：AG-UI 编码器读的就是它。
        之前这里发的是 `name=` / `value={"stage": ...}`，而编码器读
        `event["stage"]` —— 结果前端收到的 26 个阶段帧全是空字符串，
        活动条永远显示"正在理解你的问题…"，等于没有阶段信息。
        """
        self.stage = stage
        self.bus.emit(EventKind.CUSTOM_STAGE, stage=stage)

    def tool_context(self, agent: str) -> ToolContext:
        from ..tools.policy import POLICY

        decision = POLICY.decide(agent=agent, scopes=self.scopes)
        return ToolContext(
            run_id=self.run_id,
            user_id=self.user.user_id,
            username=self.user.username,
            scopes=self.scopes,
            user_token=self.user.token,
            agent=agent,
            bus=self.bus,
            governance=self.governance,
            approve=self.approve,
            sandbox_tool_names=frozenset(t.name for t in decision.sandbox_tools),
        )

    # ------------------------------------------------------------------
    async def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> Completion:
        """包一层：统一做熔断检查、模型调用预算与 token 记账。"""
        if not self.governance.breaker.allow():
            raise RuntimeError(
                f"推理服务连续失败，已熔断：{self.governance.breaker.last_error or '未知错误'}"
            )
        # 模型调用预算：超了必须立刻停下，否则就是"一直转圈"
        if not self.governance.spend_llm():
            raise RuntimeError("本次运行的模型调用次数已达上限，已停止继续尝试")

        # 单次模型调用的硬上限。
        # httpx 的超时是"单次读写"的，理论上够用；但它是库层的，
        # 一旦哪天连接建立后对端一直不结束，业务层就完全看不到超时。
        # 这里再包一层 asyncio.wait_for，保证**任何一次模型调用都有上界**。
        timeout = min(self.settings.request_timeout_s, 45.0)
        started = time.monotonic()
        # 日志里带上阶段和规模：卡住时看服务端控制台就知道是哪一次调用、多大上下文
        logger.info(
            "LLM 调用开始 | stage=%s | messages=%d | tools=%d | timeout=%ss",
            self.stage,
            len(messages),
            len(tools or []),
            timeout,
        )
        try:
            completion = await asyncio.wait_for(
                self.client.chat(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    temperature=temperature,
                    json_mode=json_mode,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError as exc:
            self.governance.breaker.record_failure("模型响应超时")
            logger.warning("LLM 调用超时 | stage=%s | %.1fs", self.stage, time.monotonic() - started)
            raise RuntimeError(f"模型在 {timeout:.0f} 秒内没有响应，已放弃这一步") from exc
        except Exception as exc:
            self.governance.breaker.record_failure(str(exc))
            logger.warning(
                "LLM 调用失败 | stage=%s | %.1fs | %s",
                self.stage,
                time.monotonic() - started,
                str(exc)[:160],
            )
            raise
        logger.info(
            "LLM 调用完成 | stage=%s | %.1fs | content=%d | tool_calls=%d",
            self.stage,
            time.monotonic() - started,
            len(completion.content or ""),
            len(completion.tool_calls),
        )
        self.governance.breaker.record_success()
        self.track_tokens(completion)
        return completion

    async def chat_stream(self, *, messages: list[dict[str, Any]], temperature: float = 0.3) -> Any:
        if not self.governance.breaker.allow():
            raise RuntimeError("推理服务已熔断")
        if not self.governance.spend_llm():
            raise RuntimeError("本次运行的模型调用次数已达上限，已停止继续尝试")
        return self.client.chat_stream(
            model=self.model,
            messages=messages,
            temperature=temperature,
            idle_timeout_s=self.settings.stream_idle_timeout_s,
        )

    def track_tokens(self, completion: Completion) -> None:
        self.governance.spend_tokens(completion.total_tokens)
        self.tracer.add_tokens(completion.prompt_tokens, completion.completion_tokens)

    # ------------------------------------------------------------------
    async def approve(self, tool: str, args: dict[str, Any], description: str) -> bool:
        """写操作审批闸门。

        流程：发 approval_required 事件 → 挂起等前端 POST 决议 → 超时自动拒绝。
        超时自动拒绝而不是自动通过，因为写操作的风险不对称。

        注意：**这里会阻塞整个运行，最长 approval_timeout_s 秒。**
        所以必须让"在等审批"这件事在界面上明确可见（阶段 + 倒计时 + 队列长度），
        否则用户看到的就是"卡在执行这一步"。
        """
        loop = asyncio.get_running_loop()
        approval_id, future = APPROVALS.create(loop)

        summary = _summarize_action(tool, args, description)

        # 一次选两门课会有两次审批。告诉前端"后面还有几个"，
        # 用户就不会只点一次然后以为结束了。
        pending = APPROVALS.pending_count
        self.set_stage(f"等待你确认：{summary[:20]}")
        self.bus.emit(
            EventKind.APPROVAL_REQUIRED,
            approval_id=approval_id,
            agent=self._current_agent,
            tool=tool,
            args=args,
            summary=summary,
            timeout_s=int(self.settings.approval_timeout_s),
            pending=pending,
        )

        wait_started = time.monotonic()
        self._awaiting_approval = True
        try:
            approved = await asyncio.wait_for(future, timeout=self.settings.approval_timeout_s)
        except asyncio.TimeoutError:
            APPROVALS.discard(approval_id)
            # 审批等待不计入墙钟，超时这条路径也要扣
            self.governance.pause_clock(time.monotonic() - wait_started)
            approved = False
            self.bus.emit(
                EventKind.APPROVAL_RESULT,
                approval_id=approval_id,
                approved=False,
                reason="timeout",
            )
            return False
        finally:
            self._awaiting_approval = False

        # **关键**：用户思考的时间不算 Agent 慢。
        # 不扣掉的话，用户等 60 秒再点确认，下一条语句就会因为
        # "运行时间超出预算" 直接失败 —— 表现就是"卡住然后失败"。
        self.governance.pause_clock(time.monotonic() - wait_started)

        self.bus.emit(
            EventKind.APPROVAL_RESULT,
            approval_id=approval_id,
            approved=approved,
            reason="user",
        )
        self.set_stage("继续执行")
        return approved

    _current_agent: str = ""

    # 是否正在等用户审批。等审批属于"人慢"，不算运行停滞。
    _awaiting_approval: bool = False

    # 是否正在等用户澄清意图。同理，等用户不算停滞。
    _awaiting_clarify: bool = False

    @property
    def awaiting_approval(self) -> bool:
        return self._awaiting_approval

    @property
    def awaiting_clarify(self) -> bool:
        return self._awaiting_clarify

    def set_current_agent(self, agent: str) -> None:
        self._current_agent = agent

    # ------------------------------------------------------------------
    async def ask_clarification(
        self,
        *,
        question: str,
        options: list[dict[str, Any]] | None,
        round_no: int,
        dimension: str = "",
        kind: str = "",
    ) -> str | None:
        """澄清闸门：**挂起当前运行**，等用户补充信息，然后同一轮继续。

        为什么不"结束这一轮再让用户重新提问"：
        那样用户得把整句话重说一遍，而且上一轮已经抽到的槽位容易丢。
        挂起等待的体验是"弹个窗问一句，答完继续跑"，和审批一致。

        返回 None 表示等超时了（用户可能关了页面），由上层降级收尾。
        """
        loop = asyncio.get_running_loop()
        clarification_id, future = CLARIFICATIONS.create(loop)
        timeout_s = self.settings.clarify_timeout_s

        self.set_stage(f"等待你补充信息（{_DIMENSION_LABELS.get(dimension, '澄清')}）")
        self.bus.emit(
            EventKind.CLARIFY_REQUIRED,
            clarification_id=clarification_id,
            question=question,
            options=options or [],
            round=round_no,
            timeout_s=int(timeout_s),
            # 这一轮在澄清哪个维度：前端可以显示成"正在确认：意图/动作/课程"
            dimension=dimension,
            clarify_kind=kind,
        )

        wait_started = time.monotonic()
        self._awaiting_clarify = True
        try:
            answer = await asyncio.wait_for(future, timeout=timeout_s)
        except asyncio.TimeoutError:
            CLARIFICATIONS.discard(clarification_id)
            self.governance.pause_clock(time.monotonic() - wait_started)
            self.bus.emit(
                EventKind.CLARIFY_ANSWER,
                clarification_id=clarification_id,
                answered=False,
                timed_out=True,
            )
            return None
        finally:
            self._awaiting_clarify = False

        # 用户思考的时间不算 Agent 慢（和审批同理）
        self.governance.pause_clock(time.monotonic() - wait_started)
        self.bus.emit(
            EventKind.CLARIFY_ANSWER,
            clarification_id=clarification_id,
            answered=True,
            timed_out=False,
            text=answer[:200],
        )

        # **澄清问答不写进对话记录。**
        #
        # 澄清是弹窗里的交互，不是聊天内容 —— 用户不希望自己的回答
        # 变成会话里的一条消息气泡。这一问一答只存在于槽位状态里：
        # 答案已经并进槽位（下一轮识别直接可用），问题本身不需要留档。
        # 真正要进对话记录的只有用户最初那句话和最终的助手回答。
        return answer

    # ------------------------------------------------------------------
    async def enforce_stall(self, task: asyncio.Task[Any], stall_s: float = 60.0) -> None:
        """看门狗：运行停滞超过 stall_s 秒就强制收尾。

        这是**最后一道兜底**。前面已经有各种超时（工具 15s、沙箱 10s、
        审批 90s、模型 60s、墙钟 120s），但它们都要求"能走到检查点"。
        如果某个 await 真的卡死了（网络半开、库锁、第三方 SDK 内部挂住），
        前面那些检查一个都不会执行 —— 用户看到的就是"几百秒没动静"。

        这个看门狗独立于主流程，只看两件事：
          - 还有没有新事件（bus.idle_seconds）
          - 是不是在等用户（等审批不算停滞）

        一旦判定停滞：发错误 + 发一条兜底回答 + 关闭运行 + 取消任务。
        用户最多等 stall_s 秒，而且一定会看到"卡在哪一步"。
        """
        while not task.done():
            await asyncio.sleep(2.0)
            if task.done():
                return
            if self.bus.closed:
                return
            if self._awaiting_approval:
                continue
            # 等用户澄清也不算停滞：这时候停掉运行等于把用户正在输入的东西扔掉
            if self._awaiting_clarify:
                continue
            idle = self.bus.idle_seconds()
            if idle <= stall_s:
                continue

            stage = self.stage or "未知阶段"
            message = f"运行停滞超过 {stall_s:.0f} 秒（卡在「{stage}」），已中止"
            self.bus.emit(EventKind.ERROR, message=message)
            self.bus.emit(
                EventKind.FINAL,
                content=(
                    f"这次没能完成：卡在「{stage}」超过 {stall_s:.0f} 秒，已经帮你中止了。\n\n"
                    "可以重试一次，或者把问题拆小一点（比如只说课程号）。"
                ),
                streamed=False,
            )
            self.close(status=3, error="stalled")
            task.cancel()
            return

    # ------------------------------------------------------------------
    def start(self, *, title: str) -> None:
        from ..memory import conversation

        # ensure_thread 会校验会话归属：如果传进来的 thread_id 属于别人，
        # 它会换成一个新会话并返回。必须用返回值覆盖 self.thread_id，
        # 否则后面的消息仍会写到别人的会话里。
        self.thread_id = conversation.ensure_thread(
            thread_id=self.thread_id, user_id=self.user.user_id, title=title
        )
        conversation.append_message(
            thread_id=self.thread_id,
            user_id=self.user.user_id,
            role="user",
            content=title,
            run_id=self.run_id,
        )

        self.bus.emit(
            EventKind.RUN_STARTED,
            run_id=self.run_id,
            thread_id=self.thread_id,
            model=self.model,
            title=title[:120],
        )
        self.tracer.start(title=title)
        self.langsmith.start_run(
            name="courseflow-agent",
            inputs={"goal": title, "user": self.user.username, "model": self.model},
            project=self.settings.langsmith_project,
        )

    def close(self, *, status: int, error: str = "") -> None:
        """收尾：落库 + 关流。

        顺序很重要：先把剩余事件录进 trace，再存助手消息，再标 finished，
        最后 close。反过来做的话最后一条 agent_finished 会丢。
        """
        if self._closed:
            return
        self._closed = True

        try:
            for event in self.bus.history_snapshot():
                self.tracer.record(event)
        finally:
            self.tracer.finish(status=status, error=error)
            self._persist_assistant_message()
            self.bus.emit(
                EventKind.RUN_FINISHED,
                run_id=self.run_id,
                thread_id=self.thread_id,
                status="ok" if status == 2 else "failed",
                governance=self.governance.snapshot(),
            )
            self.langsmith.end_run(
                outputs={"status": status, "audit": len(self.governance.records)}, error=error
            )
            self.bus.close()

    def _persist_assistant_message(self) -> None:
        """把最终回答与卡片存进对话记录。

        从事件流里取，而不是让 supervisor 回传：
        supervisor 已经把这些信息变成事件了，再回传一遍就是两套真相。
        """
        from ..memory import conversation

        content = ""
        cards: list[dict[str, Any]] = []
        for event in self.bus.history_snapshot():
            if event.get("kind") == EventKind.FINAL.value:
                content = str(event.get("content") or "")
            elif event.get("kind") == EventKind.CARD.value:
                cards.append({"card": event.get("card"), "payload": event.get("payload")})

        if content.strip():
            conversation.append_message(
                thread_id=self.thread_id,
                user_id=self.user.user_id,
                role="assistant",
                content=content,
                run_id=self.run_id,
                cards=cards,
            )


def _summarize_action(tool: str, args: dict[str, Any], description: str) -> str:
    """给审批面板准备一句人话。

    故意不用模型生成：审批文案必须**确定性的**，
    让模型来写等于让待批准的操作自己描述自己，这是安全设计上的反模式。
    """
    from ..tools.business.common import offering_label

    if tool == "enroll_offering":
        try:
            offering_id = int(args.get("offering_id"))
        except (TypeError, ValueError):
            offering_id = 0
        return f"为你选课：{offering_label(offering_id)}" if offering_id else "为你选课"
    if tool == "enroll_offerings":
        raw = args.get("offering_ids") or []
        ids: list[int] = []
        if isinstance(raw, list):
            for item in raw:
                try:
                    ids.append(int(item))
                except (TypeError, ValueError):
                    continue
        labels = [offering_label(item) for item in ids[:8]]
        if not labels:
            return "为你选多门课"
        return "为你选课：" + "、".join(labels)
    if tool == "drop_offering":
        try:
            offering_id = int(args.get("offering_id"))
        except (TypeError, ValueError):
            offering_id = 0
        return f"为你退课：{offering_label(offering_id)}" if offering_id else "为你退课"
    if tool == "save_reminder":
        return f"保存提醒「{args.get('title')}」，时间 {args.get('remind_at')}"
    return description or tool
