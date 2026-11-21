"""工具层：把"能力描述"和"怎么调"彻底分开。

    spec.py      能力描述（ToolSpec + 两条安全不变量）
    registry.py  全局注册表
    context.py   一次工具执行的上下文（谁、什么权限、能不能审批）
    policy.py    L2 策略层：决定模型这次能看到什么
    direct.py    L3 直接通道：模型点名调用，宿主执行
    codeact.py   L3 代码通道：模型写代码，沙箱执行，桥接回宿主
    astguard.py  代码通道的静态闸门
    sandbox.py   代码通道的进程隔离
    business/    四个子 agent 的业务工具
"""
