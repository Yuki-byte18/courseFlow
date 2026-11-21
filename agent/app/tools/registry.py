"""工具层 L1：全局注册表。

进程级单例。业务模块 import 时通过 `@tool(...)` 装饰器把自己注册进来，
`tools/business/__init__.py` 负责触发这些 import。

为什么不搞自动扫描：显式 import 让"有哪些工具"在代码里是可 grep 的，
新人看 __init__.py 就知道系统具备什么能力，比魔法扫描好维护。
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from .spec import Exposure, ToolSpec, ToolSpecError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._frozen = False

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------
    def register(self, spec: ToolSpec) -> ToolSpec:
        if self._frozen:
            raise ToolSpecError("注册表已冻结，不能再注册工具")
        if spec.name in self._tools:
            raise ToolSpecError(f"工具 {spec.name} 重复注册")
        self._tools[spec.name] = spec
        return spec

    def freeze(self) -> None:
        """启动完成后冻结，防止运行期被动态注入。

        Agent 能注册工具 = Agent 能给自己加能力，这是必须堵死的口子。
        """
        self._frozen = True

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def require(self, name: str) -> ToolSpec:
        spec = self._tools.get(name)
        if spec is None:
            raise ToolSpecError(f"未知工具：{name}")
        return spec

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def by_owner(self, owner: str) -> list[ToolSpec]:
        """某个子 agent 能用的工具。

        owner="*" 的工具对所有 agent 可见（例如通用查询）。
        """
        return [t for t in self._tools.values() if t.owner in (owner, "*")]

    def by_owner_and_scopes(self, owner: str, scopes: Iterable[str]) -> list[ToolSpec]:
        scope_list = list(scopes)
        return [t for t in self.by_owner(owner) if t.allows(scope_list)]

    def names(self) -> list[str]:
        return sorted(self._tools)

    def stats(self) -> dict[str, Any]:
        tools = self.all()
        return {
            "total": len(tools),
            "write": sum(1 for t in tools if t.side_effect),
            "read": sum(1 for t in tools if not t.side_effect),
            "direct": sum(1 for t in tools if t.visible_directly()),
            "sandboxed": sum(1 for t in tools if t.visible_in_sandbox()),
        }


# 进程级单例
REGISTRY = ToolRegistry()


def tool(
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
    side_effect: bool,
    exposure: Exposure,
    scopes: tuple[str, ...] = (),
    owner: str = "*",
    timeout_s: float | None = None,
    card: str | None = None,
    # 与 ToolSpec.preview_chars 保持一致。默认 6000 而不是 2000：
    # 一个"我选了哪些课"的清单，9 门课就要 3.5KB，卡 2000 会让模型
    # 只看得到 6 门，然后老实报告"还有 3 门查不出来"。
    preview_chars: int = 6000,
    tags: tuple[str, ...] = (),
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """注册一个工具。

    装饰器用法让定义与实现紧挨着 —— 工具的描述和它的代码分离是最容易出错的地方。
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        REGISTRY.register(
            ToolSpec(
                name=name,
                description=description,
                parameters=parameters,
                handler=fn,
                side_effect=side_effect,
                exposure=exposure,
                scopes=scopes,
                owner=owner,
                timeout_s=timeout_s,
                card=card,
                preview_chars=preview_chars,
                tags=tags,
            )
        )
        return fn

    return decorator
