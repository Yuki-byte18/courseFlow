"""协议层。

内部事件（app/events.py）表达"业务上发生了什么"，
这一层把它翻译成前端能直接消费的标准协议（AG-UI）。

为什么要分开：协议是对外的契约，内部事件是内部实现。
混在一起的话，改一次内部事件粒度就会破坏前端。
"""

from .agui import AgUiEncoder

__all__ = ["AgUiEncoder"]
