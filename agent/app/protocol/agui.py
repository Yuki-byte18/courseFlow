"""AG-UI 协议适配层。

内部事件（`app/events.py`）是**领域语义**的：intent / graph / agent_started / tool_call …
前端要的是**可渲染的交互协议**。这层负责把前者翻译成 AG-UI 的标准事件。

为什么要有这层，而不是让前端直接吃内部事件：
  1. 协议标准化：AG-UI 是"Agent ↔ 前端"的通用事件流，换前端框架不用改协议
  2. 关注点分离：内部想改事件粒度时，只改这一层的映射，前端不动
  3. 状态同步有规范：AG-UI 用 STATE_SNAPSHOT / STATE_DELTA（JSON Patch）传状态，
     前端只需要一个 reducer 消费补丁，不用理解业务事件的语义

AG-UI 事件（本实现覆盖的子集）：

    RUN_STARTED / RUN_FINISHED / RUN_ERROR
    STEP_STARTED / STEP_FINISHED          子 agent 与代码执行都是"步骤"
    TEXT_MESSAGE_START / _CONTENT / _END   流式文本
    TOOL_CALL_START / _ARGS / _END         工具调用（参数也流式）
    STATE_SNAPSHOT / STATE_DELTA           整棵 Agent 状态树
    CUSTOM                                 领域自定义事件（卡片、审批、预算）

State 树（前端 reducer 直接维护这个结构）：

    {
      "intent":   {"intent": str, "confidence": float, "reason": str} | null,
      "nodes":    [{id, agent, goal, deps, status, dynamic, summary, error}],
      "agents":   {node_id: {agent, goal, status, summary, items: [...]}},
      "approval": {approval_id, agent, tool, summary, args, timeout_s} | null,
      "cards":    [{card, payload}],
      "budget":   {...} | null
    }
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from ..events import EventKind


def _now_ms() -> int:
    return int(time.time() * 1000)


class AgUiEncoder:
    """把内部事件翻译成 AG-UI 事件。

    有状态：它同时维护一份"前端应该看到的状态树"，
    每收到一个内部事件就产出对应的 STATE_DELTA（JSON Patch）。

    为什么 STATE_DELTA 直接 replace 整个子树而不是算最小 diff：
    节点数最多 5 个、agent 卡片最多 4 个，整棵状态树几 KB。
    算精确 diff 的代码复杂度远高于收益，而 JSON Patch 允许 replace 任意子树。
    """

    def __init__(self, *, thread_id: str, run_id: str) -> None:
        self.thread_id = thread_id
        self.run_id = run_id
        self.state: dict[str, Any] = {
            "intent": None,
            "nodes": [],
            "agents": {},
            "approval": None,
            # 意图澄清：挂起等待用户补充信息时，这里放当前的澄清请求
            "clarify": None,
            "cards": [],
            "budget": None,
        }
        self._message_id = f"msg_{uuid.uuid4().hex[:12]}"
        self._message_open = False
        self._tool_calls_opened: set[str] = set()
        self._step_open: str | None = None
        self._nodes_by_id: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # 顶层事件
    # ------------------------------------------------------------------
    def start(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "RUN_STARTED",
                "threadId": self.thread_id,
                "runId": self.run_id,
                "timestamp": _now_ms(),
            },
            {"type": "STATE_SNAPSHOT", "snapshot": self.state},
        ]

    def finish(self, *, status: str, usage: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if self._message_open:
            out.append({"type": "TEXT_MESSAGE_END", "messageId": self._message_id})
            self._message_open = False
        out.append(
            {
                "type": "RUN_FINISHED",
                "threadId": self.thread_id,
                "runId": self.run_id,
                "result": {"status": status, "usage": usage or {}},
                "timestamp": _now_ms(),
            }
        )
        return out

    # ------------------------------------------------------------------
    # 主翻译入口
    # ------------------------------------------------------------------
    def encode(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            kind = EventKind(event.get("kind"))
        except ValueError:
            return []

        handler = getattr(self, f"_on_{kind.value}", None)
        if handler is None:
            return [{"type": "CUSTOM", "name": f"courseflow.{kind.value}", "value": event}]
        return handler(event)  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # 状态补丁
    # ------------------------------------------------------------------
    def _patch(self, path: str, value: Any) -> dict[str, Any]:
        return {"type": "STATE_DELTA", "delta": [{"op": "replace", "path": path, "value": value}]}

    def _sync_nodes(self) -> dict[str, Any]:
        self.state["nodes"] = list(self._nodes_by_id.values())
        return self._patch("/nodes", self.state["nodes"])

    # ------------------------------------------------------------------
    # 各事件
    # ------------------------------------------------------------------
    def _on_run_started(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "type": "CUSTOM",
                "name": "courseflow.run_started",
                "value": {"model": event.get("model"), "runId": self.run_id},
            }
        ]

    def _on_stage(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """阶段标记：告诉界面"现在在等什么"。

        单独处理而不是走兜底分支：兜底会把整个内部事件塞进 value，
        嵌套成 {"value": {"name": ..., "value": {...}}}，前端不好用。

        两种形状都兼容：`{"stage": "..."}` 和 `{"value": {"stage": "..."}}`。
        前者是现在的写法；后者是历史写法，兼容一下免得又踩一次。
        """
        stage = event.get("stage")
        if not stage:
            nested = event.get("value")
            if isinstance(nested, dict):
                stage = nested.get("stage")
        return [
            {
                "type": "CUSTOM",
                "name": "courseflow.stage",
                "value": {"stage": str(stage or "")},
            }
        ]

    def _on_intent(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """意图路由层的识别结果 + 门控档位。

        比原来多带了 slots / band / route / clarify_round ——
        排查"为什么这次又澄清了"时，光看一个置信度是看不出来的。
        """
        self.state["intent"] = {
            "intent": event.get("intent"),
            "confidence": event.get("confidence"),
            "reason": event.get("reason"),
            "needs_clarify": event.get("route") == "clarify",
            "slots": event.get("slots") or {},
            "band": event.get("band") or "",
            "route": event.get("route") or "",
            "clarify_round": event.get("clarify_round") or 0,
        }
        return [self._patch("/intent", self.state["intent"])]

    def _on_clarify(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """旧的"直接反问"事件：**只发自定义帧，不发文本**。

        以前这里会产出 TEXT_MESSAGE_*，而上层又发一条 FINAL，
        于是前端同一个问题渲染成两个气泡。现在澄清走 clarify_required
        的弹窗通道，这里保留只是为了兼容旧数据。
        """
        question = event.get("question") or ""
        return [
            {
                "type": "CUSTOM",
                "name": "courseflow.clarify",
                "value": {
                    "kind": event.get("clarify_kind") or "",
                    "round": event.get("round") or 0,
                    "question": question,
                    "options": event.get("options") or [],
                    "confidence": event.get("confidence"),
                },
            }
        ]

    def _on_clarify_required(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """澄清闸门打开：运行挂起，前端弹窗等用户回答。

        刻意**不产出任何文本帧** —— 澄清是弹窗，不是聊天消息。
        """
        value = {
            "clarification_id": event.get("clarification_id"),
            "question": event.get("question") or "",
            "options": event.get("options") or [],
            "round": event.get("round") or 0,
            "timeout_s": event.get("timeout_s") or 0,
            # 这一轮澄清的是哪个维度（意图/动作/课程/整体确认）
            "dimension": event.get("dimension") or "",
            "kind": event.get("clarify_kind") or "",
        }
        self.state["clarify"] = value
        return [
            self._patch("/clarify", value),
            {
                "type": "CUSTOM",
                "name": "courseflow.clarify_required",
                "value": value,
            },
        ]

    def _on_clarify_answer(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """澄清被回答（或超时）：关掉弹窗，运行继续往下跑。"""
        self.state["clarify"] = None
        return [
            self._patch("/clarify", None),
            {
                "type": "CUSTOM",
                "name": "courseflow.clarify_answer",
                "value": {
                    "clarification_id": event.get("clarification_id"),
                    "answered": bool(event.get("answered")),
                    "timed_out": bool(event.get("timed_out")),
                    "text": event.get("text") or "",
                },
            },
        ]

    def _on_intent_regression(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        """澄清回归：越澄清越差，回流治理层。前端用它在过程区标一条警告。"""
        return [
            {
                "type": "CUSTOM",
                "name": "courseflow.intent_regression",
                "value": {
                    "reason": event.get("reason") or "",
                    "round": event.get("clarify_round") or 0,
                    "history": event.get("confidence_history") or [],
                    "intent": event.get("intent") or "",
                },
            }
        ]

    def _on_graph(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        nodes = event.get("nodes") or []
        for node in nodes:
            self._nodes_by_id[node.get("id", "")] = node
        return [self._sync_nodes()]

    def _on_node_status(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        node_id = event.get("node_id", "")
        node = self._nodes_by_id.get(node_id)
        if node is not None:
            node = {**node, "status": event.get("status")}
            self._nodes_by_id[node_id] = node
        return [self._sync_nodes()]

    def _on_agent_started(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        node_id = event.get("node_id", "")
        agent = event.get("agent", "")
        self.state["agents"][node_id] = {
            "agent": agent,
            "goal": event.get("goal", ""),
            "status": "running",
            "summary": "",
            "items": [],
        }
        self._step_open = node_id
        return [
            {"type": "STEP_STARTED", "stepName": f"{agent}:{node_id}"},
            self._patch("/agents", self.state["agents"]),
        ]

    def _on_agent_finished(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        node_id = event.get("node_id", "")
        agent = event.get("agent", "")
        card = self.state["agents"].get(node_id)
        if card is not None:
            card["status"] = "done" if event.get("ok") else "failed"
            card["summary"] = event.get("summary") or ""
            self.state["agents"][node_id] = card
        self._step_open = None
        return [
            {"type": "STEP_FINISHED", "stepName": f"{agent}:{node_id}"},
            self._patch("/agents", self.state["agents"]),
        ]

    def _on_tool_call(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        tool = event.get("tool", "")
        channel = event.get("channel", "direct")
        call_id = f"call_{uuid.uuid4().hex[:12]}"

        out: list[dict[str, Any]] = [
            {
                "type": "TOOL_CALL_START",
                "toolCallId": call_id,
                "toolCallName": tool,
                "parentMessageId": self._message_id,
            },
            {
                "type": "TOOL_CALL_ARGS",
                "toolCallId": call_id,
                # AG-UI 的参数也是流式的，这里一次性给完整 JSON
                "delta": json.dumps(event.get("args") or {}, ensure_ascii=False),
            },
            {"type": "TOOL_CALL_END", "toolCallId": call_id},
        ]
        self._tool_calls_opened.add(tool)

        agent = event.get("agent", "")
        node_id = self._running_node(agent)
        if node_id:
            card = self.state["agents"].setdefault(
                node_id,
                {"agent": agent, "goal": "", "status": "running", "summary": "", "items": []},
            )
            card["items"] = [
                *card["items"],
                {
                    "id": call_id,
                    "tool": tool,
                    "channel": channel,
                    "status": "running",
                    "detail": "",
                },
            ]
            out.append(self._patch("/agents", self.state["agents"]))

        return out

    def _on_tool_result(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        tool = event.get("tool", "")
        agent = event.get("agent", "")
        node_id = self._running_node(agent)
        out: list[dict[str, Any]] = []

        if node_id:
            card = self.state["agents"].get(node_id)
            if card is not None:
                items = [dict(item) for item in card["items"]]
                for item in reversed(items):
                    if item["tool"] == tool and item["status"] == "running":
                        item["status"] = "ok" if event.get("ok") else "failed"
                        item["detail"] = str(event.get("preview") or "")[:1200]
                        item["durationMs"] = event.get("duration_ms")
                        break
                card["items"] = items
                self.state["agents"][node_id] = card
                out.append(self._patch("/agents", self.state["agents"]))

        return out

    def _on_code_run(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        agent = event.get("agent", "")
        node_id = self._running_node(agent)
        blocked = bool(event.get("blocked"))
        call_id = f"code_{uuid.uuid4().hex[:12]}"

        out: list[dict[str, Any]] = [
            {"type": "STEP_STARTED", "stepName": "codeact"},
            {
                "type": "TOOL_CALL_START",
                "toolCallId": call_id,
                "toolCallName": "execute_code",
                "parentMessageId": self._message_id,
            },
            {
                "type": "TOOL_CALL_ARGS",
                "toolCallId": call_id,
                "delta": json.dumps(
                    {
                        "purpose": event.get("purpose") or "",
                        "code": event.get("code") or "",
                    },
                    ensure_ascii=False,
                ),
            },
            {"type": "TOOL_CALL_END", "toolCallId": call_id},
        ]

        if node_id:
            card = self.state["agents"].setdefault(
                node_id,
                {"agent": agent, "goal": "", "status": "running", "summary": "", "items": []},
            )
            card["items"] = [
                *card["items"],
                {
                    "id": call_id,
                    "tool": "execute_code",
                    "channel": "code",
                    "status": "failed" if blocked else "running",
                    "detail": event.get("reason") if blocked else (event.get("code") or ""),
                },
            ]
            out.append(self._patch("/agents", self.state["agents"]))

        return out

    def _on_code_output(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        agent = event.get("agent", "")
        node_id = self._running_node(agent)
        out: list[dict[str, Any]] = [{"type": "STEP_FINISHED", "stepName": "codeact"}]

        if node_id:
            card = self.state["agents"].get(node_id)
            if card is not None:
                items = [dict(item) for item in card["items"]]
                for item in reversed(items):
                    if item["tool"] == "execute_code" and item["status"] == "running":
                        item["status"] = "ok" if event.get("ok") else "failed"
                        item["detail"] = str(
                            event.get("output") if event.get("ok") else event.get("error")
                        )[:2000]
                        item["durationMs"] = event.get("duration_ms")
                        break
                card["items"] = items
                self.state["agents"][node_id] = card
                out.append(self._patch("/agents", self.state["agents"]))

        return out

    def _on_approval_required(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        self.state["approval"] = {
            "approval_id": event.get("approval_id"),
            "agent": event.get("agent"),
            "tool": event.get("tool"),
            "summary": event.get("summary"),
            "args": event.get("args"),
            "timeout_s": event.get("timeout_s"),
        }
        return [
            self._patch("/approval", self.state["approval"]),
            {
                "type": "CUSTOM",
                "name": "courseflow.approval_required",
                "value": self.state["approval"],
            },
        ]

    def _on_approval_result(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        previous = self.state["approval"] or {}
        self.state["approval"] = None
        return [
            self._patch("/approval", None),
            {
                "type": "CUSTOM",
                "name": "courseflow.approval_result",
                "value": {
                    "approval_id": event.get("approval_id") or previous.get("approval_id"),
                    "approved": event.get("approved"),
                    "reason": event.get("reason"),
                },
            },
        ]

    def _on_card(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        card = {"card": event.get("card"), "payload": event.get("payload")}
        self.state["cards"] = [*self.state["cards"], card]
        return [
            self._patch("/cards", self.state["cards"]),
            {"type": "CUSTOM", "name": "courseflow.card", "value": card},
        ]

    def _on_budget(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        self.state["budget"] = {
            "dimension": event.get("dimension"),
            "snapshot": event.get("snapshot"),
        }
        return [
            self._patch("/budget", self.state["budget"]),
            {
                "type": "CUSTOM",
                "name": "courseflow.budget",
                "value": self.state["budget"],
            },
        ]

    def _on_delta(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        return self._text(event.get("text") or "", complete=False, streamed=True)

    def _on_final(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        content = event.get("content") or ""
        if event.get("streamed") and self._message_open:
            # 已经流式发过了，只需要收尾
            self._message_open = False
            return [{"type": "TEXT_MESSAGE_END", "messageId": self._message_id}]
        return self._text(content, complete=True, streamed=False)

    def _on_error(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "type": "RUN_ERROR",
                "message": event.get("message") or "运行出错",
                "code": "agent_error",
            }
        ]

    def _on_run_finished(self, event: dict[str, Any]) -> list[dict[str, Any]]:
        return self.finish(
            status=str(event.get("status") or "ok"),
            usage=event.get("governance") or {},
        )

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def _text(self, content: str, *, complete: bool, streamed: bool) -> list[dict[str, Any]]:
        """流式文本消息。

        AG-UI 要求 START → CONTENT* → END 三段式。
        已经 open 就直接发 CONTENT；complete=True 时补一个 END。
        """
        out: list[dict[str, Any]] = []
        if not self._message_open:
            out.append({"type": "TEXT_MESSAGE_START", "messageId": self._message_id})
            self._message_open = True

        if content:
            out.append(
                {
                    "type": "TEXT_MESSAGE_CONTENT",
                    "messageId": self._message_id,
                    "delta": content,
                }
            )

        if complete:
            out.append({"type": "TEXT_MESSAGE_END", "messageId": self._message_id})
            self._message_open = False
        elif not streamed:
            pass

        return out

    def _running_node(self, agent: str) -> str:
        for node_id, card in self.state["agents"].items():
            if card.get("agent") == agent and card.get("status") == "running":
                return node_id
        return ""
