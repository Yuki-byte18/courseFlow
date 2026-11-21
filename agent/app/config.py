"""运行期配置。

全部来自环境变量，且都有默认值 —— 本机开发不配任何东西也能起来。
刻意不用 pydantic-settings：少一个依赖，且这里的配置项都是扁平的标量。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def load_dotenv() -> None:
    """把 agent/.env 载入环境变量。

    自己写 15 行而不是装 python-dotenv：
    格式就是 KEY=VALUE 加注释，多一个依赖不划算。

    规则：**已存在的环境变量不覆盖** —— 命令行 / 容器里显式传的值优先级更高。
    """
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.is_file():
        return

    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# 模块导入时就加载，保证后面 Settings 的 default_factory 能读到
load_dotenv()


def _str(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value not in (None, "") else default


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_str(name, str(default)))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    raw = _str(name, "true" if default else "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class MySQLConfig:
    host: str
    port: int
    user: str
    password: str
    database: str

    def connect_kwargs(self) -> dict:
        return {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "database": self.database,
            "charset": "utf8mb4",
            "autocommit": True,
            "cursorclass": None,  # 由 db.py 填成 DictCursor
        }


@dataclass(frozen=True)
class Settings:
    # HTTP
    host: str = field(default_factory=lambda: _str("AGENT_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("AGENT_PORT", 8090))
    internal_token: str = field(
        default_factory=lambda: _str("AGENT_INTERNAL_TOKEN", "dev-internal-token-change-me")
    )

    # 推理层
    deepseek_base_url: str = field(
        default_factory=lambda: _str("AGENT_DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    )
    # 单次模型请求的超时。
    # 120s 太宽了：一次超时 + 重试就是 240s 静默，用户只会以为卡死。
    # 压到 45s，配合看门狗（60s）保证"最坏情况一分钟内一定有反馈"。
    request_timeout_s: float = field(
        default_factory=lambda: _float("AGENT_REQUEST_TIMEOUT_S", 45.0)
    )
    # 校验与推理用同一份列表，模型合法性以 DeepSeek /models 为准
    default_model: str = field(default_factory=lambda: _str("AGENT_DEFAULT_MODEL", "deepseek-v4.1-flash"))

    # Go 业务后端。
    # 写操作（选课/退课）由 Agent 带**用户自己的 token** 回调这里，
    # 这样容量、学分、冲突、防超卖的规则只有一份实现。
    go_base_url: str = field(
        default_factory=lambda: _str("AGENT_GO_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
    )

    # 数据库
    mysql: MySQLConfig = field(
        default_factory=lambda: MySQLConfig(
            host=_str("AGENT_MYSQL_HOST", "127.0.0.1"),
            port=_int("AGENT_MYSQL_PORT", 3306),
            user=_str("AGENT_MYSQL_USER", "root"),
            password=_str("AGENT_MYSQL_PASSWORD", ""),
            database=_str("AGENT_MYSQL_DATABASE", "courseflow"),
        )
    )

    # 治理层
    # ---- 代码通道（CodeAct）总开关 ----
    #
    # **默认开启** —— 这是产品要求的双通道能力（FC + CodeAct 混合）。
    # 它依赖"起一个子进程"，而子进程在某些宿主环境里会让事件循环停住。
    # 排查这类问题时可以临时设 AGENT_ENABLE_CODE_CHANNEL=0 切到纯直连模式，
    # 用来确认"是不是代码通道的问题"。
    enable_code_channel: bool = field(
        default_factory=lambda: _bool("AGENT_ENABLE_CODE_CHANNEL", True)
    )
    max_tool_calls: int = field(default_factory=lambda: _int("AGENT_MAX_TOOL_CALLS", 24))
    # 代码执行次数。compute 节点 3 次足够；给多了只会让模型反复试错。
    max_code_runs: int = field(default_factory=lambda: _int("AGENT_MAX_CODE_RUNS", 4))
    # 模型调用总次数。
    #
    # 要按整条链路算账：意图 1 + 建图 1 + 每个节点最多 4 + 反思 2 + 收尾 1 ≈ 15。
    # 设成 12 的话，节点一多就会在"执行"或"收尾"阶段被掐断 ——
    # 那看起来就是"卡住然后失败"。给到 20 留足余量，真正的边界交给墙钟。
    max_llm_calls: int = field(default_factory=lambda: _int("AGENT_MAX_LLM_CALLS", 20))
    # 墙钟只算**系统真正在工作**的时间，审批等待会被扣掉（见 Budget.pause）
    max_wall_seconds: float = field(default_factory=lambda: _float("AGENT_MAX_WALL_SECONDS", 120.0))
    tool_timeout_s: float = field(default_factory=lambda: _float("AGENT_TOOL_TIMEOUT_S", 15.0))
    sandbox_timeout_s: float = field(default_factory=lambda: _float("AGENT_SANDBOX_TIMEOUT_S", 10.0))
    sandbox_max_output_chars: int = field(
        default_factory=lambda: _int("AGENT_SANDBOX_MAX_OUTPUT_CHARS", 8000)
    )
    # 审批等待上限。**这个值直接决定"卡住"最长有多久**，所以刻意压到 90s：
    # 240s 意味着用户走开一会儿回来才发现整件事已经失败，体验很差。
    approval_timeout_s: float = field(default_factory=lambda: _float("AGENT_APPROVAL_TIMEOUT_S", 90.0))
    # 流式读取的空闲超时。整条请求的超时是 request_timeout_s，
    # 但流式一旦对端不发数据，会一直挂到总超时才失败 —— 单独设一个更短的。
    stream_idle_timeout_s: float = field(
        default_factory=lambda: _float("AGENT_STREAM_IDLE_TIMEOUT_S", 45.0)
    )

    # 编排层
    #
    # ---- 意图路由层：三档阈值（用户明确要求）----
    #   >= 0.95        直接放行，进编排层执行
    #   0.75 ~ 0.95    假设性确认（"您是想……对吗？"），终止本轮等回复
    #   0.50 ~ 0.75    选项式/追问式澄清，终止本轮等回复
    #   <  0.50        拒绝，请用户换个说法
    intent_direct_threshold: float = field(
        default_factory=lambda: _float("AGENT_INTENT_DIRECT_THRESHOLD", 0.95)
    )
    intent_confirm_threshold: float = field(
        default_factory=lambda: _float("AGENT_INTENT_CONFIRM_THRESHOLD", 0.75)
    )
    intent_reject_threshold: float = field(
        default_factory=lambda: _float("AGENT_INTENT_REJECT_THRESHOLD", 0.50)
    )
    # 最多澄清几轮。取 3 的理由：澄清的边际收益在第 3 轮之后基本归零，
    # 而每一轮都要用户多打一句话。超过就降级为选项式，再超就拒绝。
    max_clarify_rounds: int = field(
        default_factory=lambda: _int("AGENT_MAX_CLARIFY_ROUNDS", 3)
    )
    # 澄清回归判定窗口：连续这么多轮置信度不升反降 → 判定"越澄清越差"
    intent_regress_patience: int = field(
        default_factory=lambda: _int("AGENT_INTENT_REGRESS_PATIENCE", 2)
    )
    # 澄清等待用户回答的上限（秒）。
    #
    # 澄清**不终止本轮**：运行会挂起等前端把回答送回来，然后接着往下跑。
    # 所以必须有个上限，否则用户关掉页面就会挂住整条流。
    # 超时后降级为"把澄清问题作为本轮回答"，下一轮用户再说也能接着走。
    clarify_timeout_s: float = field(
        default_factory=lambda: _float("AGENT_CLARIFY_TIMEOUT_S", 180.0)
    )
    # 旧的单一阈值保留（老配置兼容），新逻辑不再用它做主要判断
    intent_threshold: float = field(default_factory=lambda: _float("AGENT_INTENT_THRESHOLD", 0.75))
    max_supervisor_rounds: int = field(default_factory=lambda: _int("AGENT_MAX_SUPERVISOR_ROUNDS", 8))
    # 单个子 agent 的工具轮次上限。
    #
    # 实测：设成 4 时，一个"帮我选两门课"的请求里查询助手会跑满 4 轮、
    # 每次一轮 2-3 秒，整条链路能到 70 秒以上 —— 用户体感就是"卡住"。
    # 3 轮足够覆盖"查 → 补查 → 收尾"，把最坏情况压到 50 秒以内。
    max_agent_steps: int = field(default_factory=lambda: _int("AGENT_MAX_AGENT_STEPS", 3))

    # 评测层（可选）
    langsmith_enabled: bool = field(default_factory=lambda: _bool("AGENT_LANGSMITH_ENABLED", False))
    langsmith_project: str = field(
        default_factory=lambda: _str("AGENT_LANGSMITH_PROJECT", "courseflow-agent")
    )
    langsmith_api_key: str = field(default_factory=lambda: _str("LANGSMITH_API_KEY", ""))


_settings: Settings | None = None


def get_settings() -> Settings:
    """单例。配置在进程生命周期内不变。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
