"""LangSmith 可选接入。

没有 API Key、或显式关闭时**完全不导入 langsmith** ——
不能让一个观测依赖变成启动依赖。

接入方式：每次运行起一个 root run，每个子 agent / 工具调用挂成子 run。
这样在 LangSmith 里能直接看到"哪一层慢、哪一步失败了"。
"""

from __future__ import annotations

from typing import Any

from ..config import Settings


class LangSmithHook:
    """极薄的封装。失败一律吞掉：观测不能影响主流程。"""

    def __init__(self, settings: Settings) -> None:
        self.enabled = False
        self._client: Any = None
        self._root: Any = None
        self._error = ""

        if not settings.langsmith_enabled:
            return
        if not settings.langsmith_api_key:
            self._error = "缺少 LANGSMITH_API_KEY"
            return

        try:  # pragma: no cover —— 依赖外部服务
            from langsmith import Client  # type: ignore
            from langsmith.run_trees import RunTree  # type: ignore

            self._client = Client(api_key=settings.langsmith_api_key)
            self._run_tree_cls = RunTree
            self.enabled = True
        except Exception as exc:  # noqa: BLE001
            self._error = f"LangSmith 初始化失败：{exc}"

    # ------------------------------------------------------------------
    def start_run(self, *, name: str, inputs: dict[str, Any], project: str) -> None:
        if not self.enabled:
            return
        try:  # pragma: no cover
            self._root = self._run_tree_cls(
                name=name,
                run_type="chain",
                inputs=inputs,
                project_name=project,
            )
            self._root.post()
        except Exception:  # noqa: BLE001
            self.enabled = False

    def end_run(self, *, outputs: dict[str, Any], error: str = "") -> None:
        if not self.enabled or self._root is None:
            return
        try:  # pragma: no cover
            self._root.end(outputs=outputs, error=error or None)
            self._root.patch()
        except Exception:  # noqa: BLE001
            pass

    @property
    def status(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "error": self._error}
