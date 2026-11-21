"""编排层：supervisor。

流程：

    1. 意图路由层（识别 → 门控 → 澄清）：
         - 放行 → 继续
         - 澄清 → 生成确认句/选项，**终止本轮**等用户回复（支持多轮）
         - 拒绝 → 请用户换个说法
    2. meta（闲聊/元问题）→ 直接回答，不建图不调工具
    3. 产粗粒度任务图 → 下发 graph 事件
    4. 循环：取可执行节点 → 交给对应子 agent → 记录观察 → 更新图
    5. 图跑完后反思：finish / add（补节点）/ retry（重开失败节点）
    6. 汇总所有观察 → 流式产出最终回答 → final 事件

每一步都发事件，前端据此实时渲染步骤条与子 agent 卡片。
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..events import EventKind
from ..llm.prompts import FINAL_SYSTEM, PLANNER_SYSTEM, REFLECT_SYSTEM
from .agents import build_agents, get_agent
from .fastpath import try_write_graph
from .graph import NodeStatus, TaskGraph, build_graph_from_plan
from .router import CLARIFY, PASS, IntentRouter


class Supervisor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._router = IntentRouter(settings)
        build_agents(settings)

    # ------------------------------------------------------------------
    async def run(
        self,
        runtime: Any,
        *,
        message: str,
        history: list[dict[str, Any]] | None = None,
    ) -> None:
        history = history or []
        status = 3
        error = ""

        # **必须是 try/finally，且 finally 一定 close。**
        # 否则任何在 runtime.start() 里抛出的异常（比如写库失败）都会让
        # bus.closed 永远是 False，SSE 消费循环就会一直等下去 ——
        # 用户看到的就是"卡住了，一分钟都没动静"。
        try:
            runtime.start(title=message)

            # ---- 意图路由层：识别 → 门控 → 澄清（**不终止本轮**）----
            #
            # 澄清走的是"挂起等待"而不是"结束这一轮"：
            # 前端弹窗问一句，运行的协程在这里等着，用户答完同一轮继续。
            # 好处是用户不用把整句话重说一遍，已经抽到的槽位也不会丢。
            #
            # 外层 for 是**边界①**的兜底：即使在同一个 run 里反复澄清，
            # 也最多 `max_clarify_rounds` 轮，超了就放弃并请用户换说法。
            ask_history: list[dict[str, Any]] = []
            clarify_turns: list[tuple[str, str]] = []
            route = None
            current = message
            for _attempt in range(self._settings.max_clarify_rounds + 2):
                route = await self._router.route(
                    runtime=runtime,
                    message=current,
                    history=[*(history or []), *ask_history],
                )
                runtime.bus.emit(EventKind.INTENT, **route.to_payload())

                if route.action == PASS:
                    break

                # 拒绝（听不懂 / 回归 / 轮次耗尽）：给一条兜底回答并收尾
                if route.action != CLARIFY or route.question is None:
                    runtime.bus.emit(
                        EventKind.FINAL,
                        content=(route.question.text if route.question else "这句我没太听懂。"),
                        streamed=False,
                    )
                    status = 2
                    return

                answer = await runtime.ask_clarification(
                    question=route.question.text,
                    options=route.question.options,
                    round_no=route.clarify_round,
                    dimension=route.question.dimension,
                    kind=route.question.kind,
                )
                if answer is None:
                    # 等超时了：把澄清问题当本轮回答落下来。
                    # 下一轮用户再说话时，槽位状态还在，能接着走。
                    runtime.bus.emit(
                        EventKind.FINAL, content=route.question.text, streamed=False
                    )
                    status = 2
                    return

                clarify_turns.append((route.question.text, answer))
                ask_history.append({"role": "user", "content": current})
                ask_history.append({"role": "assistant", "content": route.question.text})
                ask_history.append({"role": "user", "content": answer})
                current = answer
            else:
                runtime.bus.emit(
                    EventKind.FINAL,
                    content=(
                        "我连着问了几轮还是没对上你的意思，先不猜了。\n\n"
                        "直接写清楚要做的事就行，比如「帮我选 CS109 和 CS110」。"
                    ),
                    streamed=False,
                )
                status = 2
                return

            intent = route.signals

            # ---- 把澄清得到的全部信息拼进**执行上下文** ----
            #
            # 这是"澄清完又忘了"的根因修复：
            # 澄清的问答和确认后的槽位原来只存在路由层的状态里，
            # 而规划器 / 子 agent / 最终回答拿到的还是用户最初那句话
            # （"那个啥"），自然什么都不知道。
            #
            # 现在把它们拼成一段附录，拼在原始请求后面，
            # 下游所有 LLM 调用都能看到"用户到底确认了什么"。
            effective_message = _compose_effective_message(
                message, intent.slots, clarify_turns
            )
            # 澄清问答也进上下文历史，子 agent 读得到
            context_history: list[dict[str, Any]] = [*(history or []), *ask_history]

            # ---- 元问题：直接回答 ----
            if intent.intent == "meta":
                runtime.set_stage("整理回答")
                await self._answer_meta(runtime, message, history)
                status = 2
                return

            # ---- 建图 ----
            runtime.set_stage("规划任务")
            graph = await self._plan(runtime, effective_message, intent.intent, context_history)
            runtime.bus.emit(EventKind.GRAPH, **graph.to_payload())

            await self._execute_graph(runtime, graph, effective_message, context_history)

            # ---- 最终回答 ----
            runtime.set_stage("整理回答")
            await self._final_answer(runtime, graph, effective_message)
            status = 2
        except Exception as exc:  # noqa: BLE001 —— 任何异常都要变成事件，不能让 SSE 静默断掉
            error = str(exc)[:500]
            runtime.bus.emit(EventKind.ERROR, message=error)
        finally:
            runtime.close(status=status, error=error)

    # ------------------------------------------------------------------
    async def _plan(
        self,
        runtime: Any,
        message: str,
        intent_kind: str,
        history: list[dict[str, Any]],
    ) -> TaskGraph:
        fast = try_write_graph(message, intent_kind)
        if fast is not None and fast.nodes:
            runtime.set_stage("已识别选课目标，跳过冗长规划")
            return fast

        messages = [
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": f"用户需求：{message}\n意图类别：{intent_kind}"},
        ]
        data = await runtime.chat(messages=messages, json_mode=True)
        graph = build_graph_from_plan(_as_plan(data.content))

        if not graph.nodes:
            # 规划失败也要能干活：给一个最小可用图
            agent = "execute" if intent_kind == "write" else "query"
            graph = build_graph_from_plan(
                {"nodes": [{"id": "n1", "agent": agent, "goal": message, "deps": []}]}
            )
        return graph

    # ------------------------------------------------------------------
    async def _execute_graph(
        self,
        runtime: Any,
        graph: TaskGraph,
        message: str,
        history: list[dict[str, Any]],
    ) -> None:
        """执行任务图，并在图跑完后**复盘**。

        循环结构（dsH 那种"能反思、能回退、最后一定收敛"）：

            取一个可执行节点 → 交给子 agent → 记录观察 → 更新图
                              ↓ 没有可执行节点了
                          反思：目标达成了吗？
                              ├ finish → 结束
                              ├ add    → 加一个补救节点，继续
                              └ retry  → 重开失败节点，继续

        收敛保证：轮次上限（max_supervisor_rounds）+ 反思次数上限
        + 同一个节点不允许无限重开。三者叠加，最坏情况也只是"少做一点"，
        绝不会转不完。
        """
        rounds = 0
        reflections = 0
        retried: set[str] = set()
        # 已经通过反思补过的 agent。**同一种 agent 只补一次。**
        #
        # 为什么要限制：日志里出现过"反思 → 加 execute → 失败 → 反思 → 再加 execute"
        # 连续三轮，用户等 40 秒还是没选上课。补节点应该解决"缺了某类工作"，
        # 而不是在同一个死路上重复下注。
        added_agents: set[str] = set()

        while rounds < self._settings.max_supervisor_rounds:
            rounds += 1

            if not runtime.governance.check_wall():
                runtime.bus.emit(
                    EventKind.ERROR, message="运行时间达到上限，已停止继续尝试"
                )
                break

            node = graph.next_ready()

            # ---- 图跑完了：反思 ----
            if node is None:
                if reflections >= 2:
                    break
                reflections += 1
                action = await self._reflect(runtime, graph, message)

                if action.get("action") == "add" and action.get("agent"):
                    agent_key = str(action["agent"])
                    if (
                        agent_key in ("query", "compute", "execute", "notify")
                        and agent_key not in added_agents
                    ):
                        added_agents.add(agent_key)
                        graph.add_node(
                            agent=agent_key,
                            goal=str(action.get("goal") or message)[:200],
                            deps=[],
                        )
                        runtime.bus.emit(EventKind.GRAPH, **graph.to_payload())
                        continue

                if action.get("action") == "retry":
                    reopened = graph.reopen_all_failed(exclude=retried)
                    if reopened:
                        # 记录重开过的节点，避免同一节点被反复重开
                        for failed_id in graph.failed_ids():
                            retried.add(failed_id)
                        runtime.bus.emit(EventKind.GRAPH, **graph.to_payload())
                        continue

                break

            graph.mark(node.id, NodeStatus.RUNNING)
            runtime.bus.emit(
                EventKind.NODE_STATUS, node_id=node.id, agent=node.agent, status="running"
            )
            runtime.bus.emit(
                EventKind.AGENT_STARTED, node_id=node.id, agent=node.agent, goal=node.goal
            )
            runtime.set_current_agent(node.agent)
            runtime.set_stage(f"执行：{node.goal[:24]}")

            agent = get_agent(node.agent)
            if agent is None:
                graph.mark(node.id, NodeStatus.FAILED, error="未知的子 agent")
                runtime.bus.emit(
                    EventKind.AGENT_FINISHED,
                    node_id=node.id,
                    agent=node.agent,
                    ok=False,
                    summary="未知的子 agent",
                )
                continue

            try:
                result = await agent.run(
                    runtime,
                    goal=node.goal,
                    observations=graph.observations(),
                    history=history,
                    # 请求原文带着澄清补充，保证每个子 agent 都知道用户确认了什么
                    request=message,
                )
                if result.ok:
                    graph.mark(node.id, NodeStatus.DONE, result=result.summary)
                    runtime.bus.emit(
                        EventKind.NODE_STATUS,
                        node_id=node.id,
                        agent=node.agent,
                        status="done",
                    )
                else:
                    graph.mark(node.id, NodeStatus.FAILED, error=result.error, result=result.summary)
                    runtime.bus.emit(
                        EventKind.NODE_STATUS,
                        node_id=node.id,
                        agent=node.agent,
                        status="failed",
                    )
                runtime.bus.emit(
                    EventKind.AGENT_FINISHED,
                    node_id=node.id,
                    agent=node.agent,
                    ok=result.ok,
                    summary=result.summary[:800],
                    tool_calls=result.tool_calls,
                )
            except Exception as exc:  # noqa: BLE001
                graph.mark(node.id, NodeStatus.FAILED, error=str(exc)[:300])
                runtime.bus.emit(
                    EventKind.NODE_STATUS,
                    node_id=node.id,
                    agent=node.agent,
                    status="failed",
                )
                runtime.bus.emit(
                    EventKind.AGENT_FINISHED,
                    node_id=node.id,
                    agent=node.agent,
                    ok=False,
                    summary=f"执行失败：{exc}",
                )

            # 运行时删节点：**只有失败才剪下游**。
            #
            # 这里踩过一个很隐蔽的坑：原来是无条件调用，
            # 结果 n1 成功之后，依赖它的 n2 因为还是 PENDING 就被一起删掉了 ——
            # compute / execute 节点根本没机会执行。表现就是"到计算那里就没了"。
            current = graph.get(node.id)
            if current is not None and current.status is NodeStatus.FAILED:
                self._prune_dependents(graph, node.id)

            runtime.bus.emit(EventKind.GRAPH, **graph.to_payload())

    # ------------------------------------------------------------------
    @staticmethod
    def _prune_dependents(graph: TaskGraph, failed_id: str) -> int:
        """把依赖失败节点的待执行节点从图上删掉。返回删除数量。

        **只在节点失败时调用。** 成功时剪下游会把后续所有节点删掉：
        n1 成功后 n2 还是 PENDING、又依赖 n1，于是被一起删了 ——
        compute / execute 根本没机会执行。这就是"到计算那里就没了"的根因。
        """
        return graph.prune_dependents(failed_id)

    # ------------------------------------------------------------------
    async def _reflect(
        self, runtime: Any, graph: TaskGraph, message: str
    ) -> dict[str, Any]:
        """复盘：目标达成了吗？要不要补节点 / 重开失败节点？

        这是"能反思、能回退"的入口。刻意做成**一次性决策**而不是自由对话：
        自由扩图等于把预算交给模型，收敛性就没保证了。
        """
        observations = graph.observations(limit_chars=2500)
        prompt = [
            {"role": "system", "content": REFLECT_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"用户的问题：{message}\n\n"
                    f"任务图现状：\n{graph.status_line()}\n\n"
                    f"已经拿到的结果：\n{observations or '（没有任何结果）'}"
                ),
            },
        ]

        try:
            runtime.set_stage("反思：目标达成了吗")
            data = await runtime.chat(messages=prompt, json_mode=True)
            parsed = _as_plan(data.content)
        except Exception:  # noqa: BLE001
            # 反思失败就当作"已达成"，直接进入收尾 —— 绝不让它拖住流程
            return {"action": "finish", "reason": "反思不可用"}

        action = str(parsed.get("action") or "finish").strip()
        if action not in ("finish", "add", "retry"):
            action = "finish"
        parsed["action"] = action
        return parsed

    # ------------------------------------------------------------------
    async def _answer_meta(
        self, runtime: Any, message: str, history: list[dict[str, Any]]
    ) -> None:
        """闲聊 / 元问题：不建图、不调工具，直接聊。"""
        messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": (
                    "你是 courseFlow 的选课助手。用户在问系统怎么用或闲聊时，"
                    "用简洁中文回答。你能做的事：查课程与余量、查课表与学分、"
                    "检测时间冲突、帮你选课与退课（需要你确认）、保存提醒。"
                    "回答控制在 120 字以内。"
                ),
            }
        ]
        for item in history[-6:]:
            if item.get("role") in ("user", "assistant"):
                messages.append(
                    {"role": item["role"], "content": str(item.get("content") or "")[:800]}
                )
        messages.append({"role": "user", "content": message})

        stream = await runtime.chat_stream(messages=messages)
        chunks: list[str] = []
        async for delta in stream:
            chunks.append(delta)
            runtime.bus.emit(EventKind.DELTA, text=delta)

        runtime.bus.emit(EventKind.FINAL, content="".join(chunks), streamed=True)

    # ------------------------------------------------------------------
    async def _final_answer(self, runtime: Any, graph: TaskGraph, message: str) -> None:
        observations = graph.observations(limit_chars=5000)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": FINAL_SYSTEM},
            {
                "role": "user",
                "content": (
                    f"用户的问题：{message}\n\n"
                    f"子 agent 完成的工作与观察结果：\n{observations or '（没有产生任何观察结果）'}"
                ),
            },
        ]

        try:
            stream = await runtime.chat_stream(messages=messages)
            chunks: list[str] = []
            async for delta in stream:
                chunks.append(delta)
                runtime.bus.emit(EventKind.DELTA, text=delta)

            content = "".join(chunks).strip()
            if not content:
                content = "抱歉，这次没能得出结论。可以换个问法试试。"
            runtime.bus.emit(EventKind.FINAL, content=content, streamed=True)
        except Exception as exc:  # noqa: BLE001
            # **降级兜底**：模型调不动了，也要让用户拿到点东西。
            # 直接把子 agent 的结论拼成一段文本，而不是抛个错误了事。
            runtime.bus.emit(
                EventKind.FINAL,
                content=self._degraded_answer(graph, str(exc)),
                streamed=False,
            )

    @staticmethod
    def _degraded_answer(graph: TaskGraph, reason: str) -> str:
        """模型不可用时的降级回答：用已有观察拼一段可读结论。

        宁可给一份"没有润色但真实"的结果，也不要让用户只看到一句报错 ——
        数据已经查到了，丢掉太可惜。
        """
        lines = ["（模型暂时不可用，下面是已查到的原始结果）"]
        for node in graph.nodes:
            if node.status is not NodeStatus.DONE or not node.result:
                continue
            lines.append(f"\n【{'查询' if node.agent == 'query' else node.agent}】{node.goal}")
            lines.append(node.result[:1200])
        if len(lines) == 1:
            lines.append("\n这次没有查到可用结果，请稍后重试。")
        lines.append(f"\n（原因：{reason[:120]}）")
        return "\n".join(lines)


def _compose_effective_message(
    message: str,
    slots: Any,
    clarify_turns: list[tuple[str, str]],
) -> str:
    """把"用户原话 + 澄清问答 + 已确认槽位"拼成下游真正该看到的请求。

    为什么必须拼：
    用户最初可能只说了一句"那个啥"，靠澄清才补全"帮我选上 CS109"。
    如果规划器和子 agent 只拿到原话，它们根本不知道要做什么 ——
    表现就是"澄清完了又忘了"。

    拼出来的是**一段自然语言附录**而不是结构化 JSON：
    下游全是 LLM 调用，人话比 JSON 更好用；而且这段也会进 trace，
    排查时"用户到底确认了什么"一眼可见。
    """
    parts: list[str] = []
    if clarify_turns:
        parts.append("（用户通过澄清补充了以下信息）")
        for question, answer in clarify_turns:
            parts.append(f"- 问：{question}")
            parts.append(f"  答：{answer}")

    description = slots.describe_full() if hasattr(slots, "describe_full") else ""
    if description:
        parts.append(f"（据此确认的槽位）{description}")

    if not parts:
        return message

    return f"{message}\n\n" + "\n".join(parts)


def _as_plan(content: str) -> dict[str, Any]:
    """把模型返回的 JSON 文本转成 plan。失败就返回空 plan，由调用方兜底。"""
    import json

    text = (content or "").strip()
    if not text:
        return {"nodes": []}
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"nodes": []}
    return parsed if isinstance(parsed, dict) else {"nodes": []}
