-- ============================================================================
-- 0007_agent_trace.sql —— Agent 运行轨迹与提醒
--
-- 起因：Agent 的每一步（意图识别 / 任务图 / 子 agent / 工具调用 / 代码执行 /
-- 审批 / 结果卡片）都要能在 trace 面板里回看。前端靠事件流实时渲染，
-- 但刷新页面之后必须还能查到，所以必须落库。
--
-- 谁写这两张表：**Python Agent 服务**（app/evaluation/tracer.py）。
-- 为什么不由 Go 写：轨迹是 Agent 域的私有数据，写它的代码就在产生它的进程里，
-- 跨进程传一遍只会多一个可能失败的点。
--
-- Go 只负责**读**（给 trace 面板用），并做权限控制。
-- ============================================================================

-- +goose Up

CREATE TABLE IF NOT EXISTS agent_runs (
    id                BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id            VARCHAR(32)     NOT NULL COMMENT '运行 ID，时间前缀 + 随机后缀，按时间有序',
    user_id           BIGINT UNSIGNED NOT NULL COMMENT '发起人',
    title             VARCHAR(200)    NOT NULL DEFAULT '' COMMENT '用户原始输入（截断）',
    intent            VARCHAR(32)     NOT NULL DEFAULT '' COMMENT 'query_compute / write / meta',
    confidence        DECIMAL(3,2)    NOT NULL DEFAULT 0.00 COMMENT '意图置信度',
    status            TINYINT         NOT NULL DEFAULT 1 COMMENT '1=运行中 2=完成 3=失败 4=已取消',
    model             VARCHAR(64)     NOT NULL DEFAULT '',
    prompt_tokens     INT             NOT NULL DEFAULT 0,
    completion_tokens INT             NOT NULL DEFAULT 0,
    error             VARCHAR(2000)   NULL,
    started_at        DATETIME(3)     NOT NULL,
    finished_at       DATETIME(3)     NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_agent_runs_run_id (run_id),
    KEY idx_agent_runs_user_time (user_id, started_at),
    KEY idx_agent_runs_status (status, started_at)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = 'Agent 运行';

CREATE TABLE IF NOT EXISTS agent_steps (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id      VARCHAR(32)     NOT NULL,
    seq         INT             NOT NULL COMMENT '运行内严格自增，前端按它排序',
    kind        VARCHAR(32)     NOT NULL COMMENT 'intent/graph/node_status/agent_started/tool_call/...',
    agent       VARCHAR(32)     NULL COMMENT 'query / compute / execute / notify',
    name        VARCHAR(128)    NULL COMMENT '工具名 / 节点 id / 卡片类型',
    status      TINYINT         NOT NULL DEFAULT 1 COMMENT '1=成功 2=进行中 3=失败',
    input_json  JSON            NULL,
    output_json JSON            NULL,
    error       TEXT            NULL,
    latency_ms  INT             NULL,
    created_at  DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_agent_steps_run_seq (run_id, seq),
    KEY idx_agent_steps_kind (run_id, kind)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = 'Agent 单步轨迹';

CREATE TABLE IF NOT EXISTS agent_reminders (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id    BIGINT UNSIGNED NOT NULL,
    title      VARCHAR(120)    NOT NULL,
    note       VARCHAR(500)    NOT NULL DEFAULT '',
    remind_at  DATETIME        NOT NULL,
    status     TINYINT         NOT NULL DEFAULT 1 COMMENT '1=待提醒 2=已完成 3=已取消',
    created_at DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_agent_reminders_user_time (user_id, remind_at),
    KEY idx_agent_reminders_status (status, remind_at)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = 'Agent 保存的提醒';

-- +goose Down

DROP TABLE IF EXISTS agent_reminders;
DROP TABLE IF EXISTS agent_steps;
DROP TABLE IF EXISTS agent_runs;
