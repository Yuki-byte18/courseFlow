"""业务工具集：查询 / 计算 / 执行 / 通知。

四个子 agent 各自拥有不同的工具集，归属由 ToolSpec.owner 决定：

    query   读：课程、班次、余量、课表、已选、轮次
    compute 算：学分核算、冲突检测、余量统计、组合推荐（多数 exposure=both，适合 CodeAct）
    execute 写：选课、退课（side_effect=True，永远走直接通道 + 审批）
    notify  通知：生成通知卡、保存提醒

import 这四个模块即完成注册（registry 是进程级单例）。
"""

from . import compute, execute, notify, query  # noqa: F401  —— 副作用式注册

__all__ = ["query", "compute", "execute", "notify"]
