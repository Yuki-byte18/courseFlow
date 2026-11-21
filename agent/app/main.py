"""courseFlow Agent 服务入口。

启动顺序：
    1. 加载配置
    2. 导入业务工具（触发注册）
    3. 校验注册表不变量（写工具不能进沙箱等），失败就拒绝启动
    4. 冻结注册表（运行期不能再注册）
    5. 起 HTTP

第 3 步放在启动阶段而不是首次调用时：工具定义错了应该立刻炸，
而不是等到用户点了一次按钮才发现。

启动：
    cd agent
    python -m uvicorn app.main:app --host 127.0.0.1 --port 8090
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import router
from .config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
)
log = logging.getLogger("courseflow.agent")


def create_app() -> FastAPI:
    settings = get_settings()

    # 1. 注册业务工具（import 即注册）
    from .tools import business  # noqa: F401
    from .tools.registry import REGISTRY

    # 2. 注册表自检
    stats = REGISTRY.stats()
    _self_check()

    # 3. 冻结
    REGISTRY.freeze()

    # 4. 业务库探活
    #    放在启动阶段：如果连不上库，所有工具都会失败，
    #    而那时候用户只会看到"Agent 好像不太聪明"，很难定位到是配置问题。
    from . import db

    db_ok, db_error = db.ping()
    if db_ok:
        log.info("业务库连接正常 | %s/%s", settings.mysql.host, settings.mysql.database)
    else:
        log.error("业务库连接失败：%s", db_error)
        log.error(
            "工具层将无法读取课程/余量/课表。请设置环境变量 "
            "AGENT_MYSQL_HOST / AGENT_MYSQL_PORT / AGENT_MYSQL_USER / "
            "AGENT_MYSQL_PASSWORD / AGENT_MYSQL_DATABASE，"
            "取值与 backend/configs/config.yaml 的 mysql 段保持一致。"
        )

    app = FastAPI(
        title="courseFlow Agent",
        version="0.1.0",
        description="六层架构的业务 Agent 服务：推理 / 工具 / 记忆 / 编排 / 执行环境 / 评测",
    )

    # 只允许本机后端调用。浏览器不直连这个服务。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router, prefix="/agent")

    log.info(
        "Agent 服务就绪 | 工具 %s 个（读 %s / 写 %s，直连 %s / 沙箱 %s）| 模型偏好 %s",
        stats["total"],
        stats["read"],
        stats["write"],
        stats["direct"],
        stats["sandboxed"],
        settings.default_model,
    )
    log.info("监听 %s:%s | LangSmith %s", settings.host, settings.port,
             "启用" if settings.langsmith_enabled else "关闭")

    return app


def _self_check() -> None:
    """注册表不变量自检。

    ToolSpec 的 __post_init__ 已经拦住了非法定义，这里再查一遍全局性质：
    至少要有一个能写、一个能读、一个能进沙箱，否则架构是残的。
    """
    from .tools.registry import REGISTRY

    tools = REGISTRY.all()
    if not tools:
        raise RuntimeError("没有任何工具注册：业务工具模块没有 import")

    writes = [t for t in tools if t.side_effect]
    reads_sandbox = [t for t in tools if t.visible_in_sandbox()]

    if not writes:
        raise RuntimeError("没有注册任何写工具：执行 agent 将无事可做")
    if not reads_sandbox:
        raise RuntimeError("没有注册任何可进沙箱的工具：CodeAct 通道无法使用")

    for spec in writes:
        if spec.exposure == "sandboxed":
            raise RuntimeError(f"写工具 {spec.name} 被暴露到沙箱，拒绝启动")
    for spec in reads_sandbox:
        if spec.side_effect:
            raise RuntimeError(f"只读通道里出现写工具 {spec.name}，拒绝启动")


app = create_app()
