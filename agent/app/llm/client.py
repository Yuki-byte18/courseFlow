"""推理层：DeepSeek 客户端。

只做一件事：把 messages / tools 发给 DeepSeek，拿回结构化结果。
不掺任何编排、工具、记忆的逻辑 —— 那些都在上层。

Key 是**每次请求传进来的**（BYOK），这个类不持有 Key、不缓存 Key、不记日志。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from ..config import get_settings

_logger = logging.getLogger("courseflow.agent")


async def _once() -> AsyncIterator[int]:
    """产出 0、1 两次尝试序号。抽出来只是让重试逻辑读起来像"最多两次"。"""
    yield 0
    yield 1


class LLMError(RuntimeError):
    """推理层错误。带 status 方便上层做熔断与提示。"""

    def __init__(self, message: str, status: int = 0, retryable: bool = True) -> None:
        super().__init__(message)
        self.status = status
        # 超时**不重试**：重试一次等于让用户再等一个超时周期，
        # 而"卡了两百秒没动静"正是这么来的（120s × 2 次）。
        self.retryable = retryable


@dataclass
class ToolCall:
    """模型要求调用的一个工具。"""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""


@dataclass
class Completion:
    """一次补全的结果。"""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # usage 用于治理层的预算统计
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class DeepSeekClient:
    """Async DeepSeek 客户端。

    刻意不重试 4xx：Key 错、余额不足这类错误重试只会更慢、更贵。
    只对网络层错误做一次重试，因为那是瞬时的。
    """

    def __init__(self, api_key: str, timeout_s: float | None = None) -> None:
        settings = get_settings()
        if not api_key:
            raise LLMError("缺少 API Key", status=401)
        self._api_key = api_key
        self._base_url = settings.deepseek_base_url
        self._timeout = timeout_s or settings.request_timeout_s

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        """发请求，**瞬时故障重试一次**。

        重试策略刻意保守：
          - 4xx 一律不重试（Key 错、余额不足、参数错，重试只会更慢更贵）
          - 5xx / 网络 / 超时 重试一次，间隔 0.6s
        再多的重试会把延迟叠上去，而 Agent 本来就要循环调用很多次。
        """
        async for attempt in _once():
            try:
                return await self._post_once(payload)
            except LLMError as exc:
                transient = not (exc.status and 400 <= exc.status < 500)
                if not transient or not exc.retryable or attempt == 1:
                    raise
                await asyncio.sleep(0.6)
        raise LLMError("请求失败")

    async def _post_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/chat/completions"

        # 发请求前先落一行日志。
        #
        # 为什么：排查"卡在某个子 agent"时，需要知道到底停在哪一层。
        #   - 有 "HTTP 请求发出" 但没有 httpx 的响应行 → 卡在网络/服务端
        #   - 连 "HTTP 请求发出" 都没有        → 卡在序列化或更上层
        # 这几行日志就是二分点。
        try:
            body_size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise LLMError(f"请求体无法序列化：{exc}", retryable=False) from exc

        _logger.info(
            "HTTP 请求发出 | model=%s | bytes=%d | messages=%d | tools=%d",
            payload.get("model"),
            body_size,
            len(payload.get("messages") or []),
            len(payload.get("tools") or []),
        )

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(url, headers=self._headers(), json=payload)
            except httpx.TimeoutException as exc:
                # 超时不重试：重试只会让用户多等一个周期
                raise LLMError(
                    f"DeepSeek 响应超时（{type(exc).__name__}）", retryable=False
                ) from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"无法连接 DeepSeek：{exc.__class__.__name__}") from exc

        if resp.status_code == 401 or resp.status_code == 403:
            raise LLMError("Key 无效或没有权限", status=resp.status_code)
        if resp.status_code == 402:
            raise LLMError("账户余额不足", status=402)
        if resp.status_code == 429:
            raise LLMError("请求过于频繁", status=429)
        if resp.status_code >= 400:
            # 不把响应体原样抛给用户，它可能很长；但截断一部分便于排查
            raise LLMError(
                f"DeepSeek 返回 {resp.status_code}：{resp.text[:300]}",
                status=resp.status_code,
            )

        return resp.json()

    # ------------------------------------------------------------------
    # 对外
    # ------------------------------------------------------------------
    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
        tool_choice: str | None = None,
    ) -> Completion:
        """一次非流式补全。子 agent 的工具循环用它。"""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        if json_mode:
            # DeepSeek 兼容 OpenAI 的 JSON 模式；提示词里也必须出现 json 字样
            payload["response_format"] = {"type": "json_object"}

        data = await self._post(payload)
        return self._parse_completion(data)

    async def chat_stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.3,
        max_tokens: int | None = None,
        idle_timeout_s: float | None = None,
    ) -> AsyncIterator[str]:
        """流式补全，只产出文本增量。

        只有"最终回答"走流式：中间的工具轮次不需要流式，
        而且流式 + 工具调用混在一起解析复杂度高、收益低。

        `idle_timeout_s` 是**单次读取**的超时。为什么单独设：
        总超时（120s）意味着对端不吐数据时要干等两分钟，用户看到的就是"卡住了"。
        给它一个更短的读超时，能快速失败并让前端拿到错误，而不是一直转圈。
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        timeout = httpx.Timeout(
            connect=10.0,
            read=idle_timeout_s or 45.0,
            write=30.0,
            pool=10.0,
        )

        url = f"{self._base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with client.stream(
                    "POST", url, headers=self._headers(), json=payload
                ) as resp:
                    if resp.status_code >= 400:
                        body = await resp.aread()
                        raise LLMError(
                            f"DeepSeek 返回 {resp.status_code}：{body[:300].decode('utf-8', 'ignore')}",
                            status=resp.status_code,
                        )
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == "[DONE]":
                            break
                        try:
                            parsed = json.loads(chunk)
                        except json.JSONDecodeError:
                            continue
                        for choice in parsed.get("choices", []):
                            delta = choice.get("delta") or {}
                            text = delta.get("content")
                            if text:
                                yield text
            except httpx.ReadTimeout as exc:
                # 明确区分"对端不吐数据"和"网络断了"：前者重试可能有用，后者没用
                raise LLMError("DeepSeek 长时间没有返回数据，已中断") from exc
            except httpx.HTTPError as exc:
                raise LLMError(f"无法连接 DeepSeek：{exc.__class__.__name__}") from exc

    async def chat_json(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int | None = 800,
    ) -> dict[str, Any]:
        """要求模型输出 JSON 并解析。

        编排层的意图识别、任务图生成用它。
        解析失败时不重试第二次（那会让延迟翻倍），直接把解析失败交给上层决定：
        对意图识别来说，解析失败 == 低置信度，正好触发澄清。
        """
        completion = await self.chat(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
        text = (completion.content or "").strip()
        if not text:
            return {}
        # 有些实现会在 JSON 外包一层 ```json
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _parse_completion(data: dict[str, Any]) -> Completion:
        choices = data.get("choices") or []
        if not choices:
            return Completion()

        message = choices[0].get("message") or {}
        usage = data.get("usage") or {}

        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            fn = raw.get("function") or {}
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {}
            calls.append(
                ToolCall(
                    id=raw.get("id") or "",
                    name=fn.get("name") or "",
                    arguments=args,
                    raw_arguments=raw_args if isinstance(raw_args, str) else "",
                )
            )

        return Completion(
            content=message.get("content") or "",
            tool_calls=calls,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )
