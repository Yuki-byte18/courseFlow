"""一次工具执行的上下文。

这是工具层与上层的唯一耦合点，刻意做成鸭子类型的运行时对象：
工具层不 import execenv / governance，也不 import 事件总线 —— 
它只要求传进来的对象有这几个方法。

    bus.emit(kind, **payload)              发一个结构化事件
    governance.spend_tool(name) -> bool    预算扣减，false 表示超预算
    governance.audit(record)               记一条审计
    approve(tool, args) -> bool            写操作的审批闸门（异步）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable


@runtime_checkable
class EventSink(Protocol):
    def emit(self, kind: Any, **payload: Any) -> Any: ...


@runtime_checkable
class Governor(Protocol):
    def spend_tool(self, name: str) -> bool: ...

    def audit(self, record: dict[str, Any]) -> None: ...


ApprovalFn = Callable[[str, dict[str, Any], str], Awaitable[bool]]


@dataclass
class ToolContext:
    """一次工具调用所依赖的运行时信息。"""

    run_id: str
    user_id: int
    username: str
    scopes: tuple[str, ...] = ()

    # 用户自己的 JWT。写操作要回调 Go 后端，就用它 —— 身份不放大。
    user_token: str = ""

    # 当前是哪个子 agent 在调工具
    agent: str = ""

    # 事件与治理（由 execenv 注入）
    bus: EventSink | None = None
    governance: Governor | None = None

    # 写操作审批闸门。返回 False 表示用户拒绝或超时。
    approve: ApprovalFn | None = None

    # 本次运行允许在沙箱里 call_tool 的工具名（策略层注入）
    sandbox_tool_names: frozenset[str] = field(default_factory=frozenset)

    # 本 agent 这一步**被允许调用**的工具名 —— 就是发给模型的那份 tools 数组。
    #
    # 为什么还要单独记一份：模型可能点名一个没给它的工具
    # （比如从系统提示里见过 execute_code，就在 execute 节点里点它）。
    # _dispatch 用这份白名单挡住这种情况，否则等于绕过策略层。
    allowed_tools: frozenset[str] = field(default_factory=frozenset)

    def emit(self, kind: Any, **payload: Any) -> None:
        if self.bus is not None:
            self.bus.emit(kind, **payload)

    def spend(self, name: str) -> bool:
        if self.governance is None:
            return True
        return self.governance.spend_tool(name)

    def audit(self, record: dict[str, Any]) -> None:
        if self.governance is not None:
            self.governance.audit(record)
