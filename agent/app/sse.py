"""SSE 编码。

格式遵循 text/event-stream：
    data: {"kind": "...", ...}\\n\\n

刻意只用 `data:` 而不发 `event:` 字段：
事件类型已经在 JSON 的 kind 里了，多一套 SSE event 名只会让前端要维护两张映射表。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from .events import Event

SSE_HEADERS = {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # Nginx / 各种代理常见的缓冲问题：这一条能显著降低首字节延迟
    "X-Accel-Buffering": "no",
}


def encode(event: Event) -> str:
    return f"data: {json.dumps(event.to_dict(), ensure_ascii=False)}\n\n"


def encode_raw(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def heartbeat() -> str:
    """注释行，用于保持连接。SSE 规范里以 ':' 开头的行是注释。"""
    return ": ping\n\n"


async def event_stream(source: "AsyncIterator[Event]") -> AsyncIterator[str]:
    async for event in source:
        yield encode(event)
