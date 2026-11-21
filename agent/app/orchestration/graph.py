"""任务图。

Supervisor 收到请求先产一张**粗粒度**任务图，然后每轮根据依赖和观察结果
决定调度哪个子 agent。图支持运行时增删改 —— 因为 Agent 的探索过程
本来就不该被一次性规划完全锁死：

    第 1 轮发现"周三下午的课都满了" → 运行时插入一个 compute 节点换方案

节点状态机很简单（pending → running → done/failed/skipped），
但它必须存在：没有状态就没法表达"这个节点被跳过是因为依赖失败了"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskNode:
    id: str
    agent: str
    goal: str
    deps: list[str] = field(default_factory=list)
    status: NodeStatus = NodeStatus.PENDING
    result: str = ""
    error: str = ""
    # 是否由运行时动态插入（前端会标一个「动态」角标）
    dynamic: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent": self.agent,
            "goal": self.goal,
            "deps": list(self.deps),
            "status": self.status.value,
            "dynamic": self.dynamic,
            "summary": self.result[:400],
            "error": self.error[:400],
        }


class TaskGraph:
    def __init__(self, nodes: list[TaskNode] | None = None) -> None:
        self.nodes: list[TaskNode] = nodes or []
        self._counter = len(self.nodes)

    # ------------------------------------------------------------------
    def new_id(self) -> str:
        self._counter += 1
        return f"n{self._counter}"

    def get(self, node_id: str) -> TaskNode | None:
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    # ------------------------------------------------------------------
    def next_ready(self) -> TaskNode | None:
        """下一个可执行的节点。

        规则：状态 pending，且所有依赖都已 done。
        依赖里有 failed 的，直接把本节点标成 skipped —— 不要让它无限期挂着。
        """
        for node in self.nodes:
            if node.status is not NodeStatus.PENDING:
                continue
            deps = [self.get(dep_id) for dep_id in node.deps]
            if any(d is None or d.status is NodeStatus.SKIPPED for d in deps):
                node.status = NodeStatus.SKIPPED
                node.error = "依赖节点被跳过"
                continue
            if any(d.status is NodeStatus.FAILED for d in deps):
                node.status = NodeStatus.SKIPPED
                node.error = "依赖节点失败"
                continue
            if all(d.status is NodeStatus.DONE for d in deps):
                return node
        return None

    def all_finished(self) -> bool:
        return all(
            node.status in (NodeStatus.DONE, NodeStatus.FAILED, NodeStatus.SKIPPED)
            for node in self.nodes
        )

    # ------------------------------------------------------------------
    def add_node(self, *, agent: str, goal: str, deps: list[str] | None = None) -> TaskNode:
        node = TaskNode(
            id=self.new_id(),
            agent=agent,
            goal=goal,
            deps=deps or [],
            dynamic=True,
        )
        self.nodes.append(node)
        return node

    def remove_node(self, node_id: str) -> bool:
        """运行时删节点。

        同时把依赖它的节点里的引用去掉，否则那些节点会永远等不到依赖。
        """
        before = len(self.nodes)
        self.nodes = [n for n in self.nodes if n.id != node_id]
        removed = len(self.nodes) < before
        if removed:
            for node in self.nodes:
                node.deps = [d for d in node.deps if d != node_id]
        return removed

    def mark(self, node_id: str, status: NodeStatus, *, result: str = "", error: str = "") -> None:
        node = self.get(node_id)
        if node is None:
            return
        node.status = status
        if result:
            node.result = result
        if error:
            node.error = error

    # ------------------------------------------------------------------
    def reopen(self, node_id: str) -> bool:
        """把一个失败/跳过的节点重新打开（反思后的"回退重试"）。

        只允许重开未执行完的节点；已经 done 的不动，否则会重复干活。
        """
        node = self.get(node_id)
        if node is None:
            return False
        if node.status not in (NodeStatus.FAILED, NodeStatus.SKIPPED):
            return False
        node.status = NodeStatus.PENDING
        node.error = ""
        return True

    def reopen_all_failed(self, *, exclude: set[str] | None = None) -> int:
        """把所有失败/跳过的节点重新打开。返回重开数量。

        为什么需要它：一个节点失败后，下游会被剪掉。如果反思认为
        "换个做法还能完成"，就得把图恢复到可执行状态，而不是让这次运行直接烂尾。
        """
        blocked = exclude or set()
        reopened = 0
        for node in self.nodes:
            if node.id in blocked:
                continue
            if self.reopen(node.id):
                reopened += 1
        return reopened

    def prune_dependents(self, failed_id: str) -> int:
        """把**直接或间接**依赖失败节点的待执行节点从图上删掉。返回删除数量。

        只在节点失败时调用 —— 成功时剪下游会把后续所有节点删掉。
        """
        removed = 0
        changed = True
        while changed:
            changed = False
            for node in list(self.nodes):
                if node.status is not NodeStatus.PENDING:
                    continue
                if failed_id in node.deps or any(d not in {n.id for n in self.nodes} for d in node.deps):
                    if self.remove_node(node.id):
                        removed += 1
                        changed = True
        return removed

    def failed_ids(self) -> list[str]:
        return [n.id for n in self.nodes if n.status is NodeStatus.FAILED]

    # ------------------------------------------------------------------
    def to_payload(self) -> dict[str, Any]:
        return {"nodes": [node.to_payload() for node in self.nodes]}

    def observations(self, limit_chars: int = 4000) -> str:
        """已完成的节点产出，拼成给后续节点看的观察结果。"""
        parts: list[str] = []
        for node in self.nodes:
            if node.status is not NodeStatus.DONE or not node.result:
                continue
            parts.append(f"[{node.agent}] {node.goal}\n{node.result}")
        text = "\n\n".join(parts)
        if len(text) > limit_chars:
            text = text[-limit_chars:]
        return text

    def status_line(self) -> str:
        """一行描述当前图的进度，给反思节点看。"""
        icon = {
            NodeStatus.PENDING: "待执行",
            NodeStatus.RUNNING: "进行中",
            NodeStatus.DONE: "已完成",
            NodeStatus.FAILED: "失败",
            NodeStatus.SKIPPED: "已跳过",
        }
        return "\n".join(
            f"- [{node.agent}] {node.goal} → {icon.get(node.status, node.status.value)}"
            + (f"（{node.error[:60]}）" if node.error else "")
            for node in self.nodes
        )


def build_graph_from_plan(plan: dict[str, Any]) -> TaskGraph:
    """把 LLM 产出的 plan JSON 转成 TaskGraph。

    容错优先：缺 id 就自动编一个，deps 指向不存在的节点就丢掉那个依赖。
    规划是模型给的，它偶尔会写错字段 —— 不值得为此让整次运行失败。
    """
    raw_nodes = plan.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        return TaskGraph()

    valid_agents = {"query", "compute", "execute", "notify"}
    nodes: list[TaskNode] = []
    seen_ids: set[str] = set()

    for index, raw in enumerate(raw_nodes[:5]):
        if not isinstance(raw, dict):
            continue
        agent = str(raw.get("agent") or "").strip()
        if agent not in valid_agents:
            continue
        node_id = str(raw.get("id") or f"n{index + 1}").strip() or f"n{index + 1}"
        if node_id in seen_ids:
            node_id = f"n{index + 1}"
        deps = raw.get("deps")
        dep_list = [str(d) for d in deps] if isinstance(deps, list) else []
        nodes.append(
            TaskNode(
                id=node_id,
                agent=agent,
                goal=str(raw.get("goal") or "").strip()[:200] or "完成用户需求",
                deps=dep_list,
            )
        )
        seen_ids.add(node_id)

    # 清掉悬空依赖
    for node in nodes:
        node.deps = [d for d in node.deps if d in seen_ids and d != node.id]

    graph = TaskGraph(nodes)
    graph._counter = len(nodes)
    return graph
