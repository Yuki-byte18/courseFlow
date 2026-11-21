"""courseFlow Agent 服务。

六层架构（自底向上）：

    inference      推理层       DeepSeek 客户端，BYOK
    tools          工具层       注册表 / 策略 / 双通道执行 / 治理
    memory         记忆层       上下文窗口（架构待定，先给接口）
    orchestration  编排层       意图守卫 + 任务图 + supervisor + 四个子 agent
    execenv        执行环境层   单次运行的上下文、工具宿主、审批闸门
    evaluation     评测层       trace 存储 + LangSmith 可选接入

依赖方向严格单向：上层可以 import 下层，下层绝不 import 上层。
"""
