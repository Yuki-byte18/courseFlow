"""评测层：观测与评估。

分两块：
  tracer.py         自建 trace 存储（MySQL）—— trace 观测面板的数据源
  langsmith_hook.py LangSmith 可选接入 —— 没有 API Key 时自动跳过

为什么两者都要：
  - 自建面板**不依赖第三方**：答辩现场没网也能看每一步发生了什么
  - LangSmith 提供业界标准的可视化与评估数据集，是加分项而不是必需项
"""

from .tracer import Tracer, TracerEvent

__all__ = ["Tracer", "TracerEvent"]
