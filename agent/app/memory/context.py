"""记忆 / 上下文 / 知识层。

用户明确说这一层"先不设计"，所以这里**只给接口，不做实现**。

但有一件事现在就必须做对：**上下文窗口的裁剪**。
因为推理层的模型有上下文上限，而 Agent 是循环的 —— 一轮对话可能产生
几十条工具结果，不做裁剪会在第 3 轮就爆掉。

所以本文件提供：
  - ContextWindow：把消息按 token 预算裁进窗口（当前唯一实现）
  - MemoryStore：长期记忆的接口占位（未实现，抛出明确异常）

这样上层可以按最终形态写代码，等记忆层设计定了再换实现，不用改调用方。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class MemoryStore(Protocol):
    """长期记忆接口占位。

    设计定下来之后要实现：
      - 写入：把一轮对话里的稳定事实（偏好、已选课、常用筛选条件）落库
      - 检索：按语义/关键词把相关记忆召回进上下文
      - 遗忘：按时间或重要性淘汰
    """

    def remember(self, key: str, value: Any) -> None: ...

    def recall(self, query: str, limit: int = 5) -> list[dict[str, Any]]: ...


class NotImplementedMemory:
    """默认实现：什么都不做。

    刻意不静默返回空列表 —— 那样上层会以为"记忆里没有东西"，
    而不是"记忆层还没实现"。抛异常更容易发现配置漏了。
    """

    def remember(self, key: str, value: Any) -> None:  # noqa: D401
        raise NotImplementedError("记忆层尚未实现：等待架构设计定稿")

    def recall(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        raise NotImplementedError("记忆层尚未实现：等待架构设计定稿")


@dataclass
class ContextWindow:
    """上下文窗口裁剪。

    策略（保守、可解释）：
      1. system 消息永远保留
      2. 最近 N 条消息永远保留（保证模型知道刚刚发生了什么）
      3. 中间的旧消息按"从旧到新"丢弃，直到估算 token 低于预算
      4. 被丢弃的那一段用一条摘要占位消息代替，避免模型以为对话断层

    估算方式：字符数 / 2.5 近似 token 数。
    中文大约 1 字 ≈ 1 token，英文 1 token ≈ 4 字符，取中间偏保守的值即可 ——
    这里的目标是"别爆窗口"，不是精确计费。
    """

    max_tokens: int = 24_000
    keep_recent: int = 8
    _CHARS_PER_TOKEN: float = 2.5

    def estimate_tokens(self, messages: list[dict[str, Any]]) -> int:
        total_chars = 0
        for message in messages:
            content = message.get("content")
            if isinstance(content, str):
                total_chars += len(content)
            elif content is not None:
                total_chars += len(str(content))
            # tool_calls 的参数也算进去
            for call in message.get("tool_calls") or []:
                fn = (call or {}).get("function") or {}
                total_chars += len(str(fn.get("arguments") or ""))
        return int(total_chars / self._CHARS_PER_TOKEN)

    def fit(
        self, messages: list[dict[str, Any]], *, extra_budget_tokens: int = 0
    ) -> list[dict[str, Any]]:
        """把消息裁进窗口。返回新的列表，不修改入参。"""
        budget = max(1_000, self.max_tokens - extra_budget_tokens)
        if self.estimate_tokens(messages) <= budget:
            return list(messages)

        system_msgs = [m for m in messages if m.get("role") == "system"]
        rest = [m for m in messages if m.get("role") != "system"]

        # 尾部保留
        tail_count = min(self.keep_recent, len(rest))
        tail = rest[-tail_count:] if tail_count else []
        head = rest[: len(rest) - tail_count]

        dropped = 0
        while head and self.estimate_tokens(system_msgs + head + tail) > budget:
            head.pop(0)
            dropped += 1

        summary = {
            "role": "system",
            "content": f"（为控制上下文长度，已省略较早的 {dropped} 条消息）",
        }

        result = list(system_msgs)
        if dropped:
            result.append(summary)
        result.extend(head)
        result.extend(tail)
        return result


MEMORY: MemoryStore = NotImplementedMemory()
