-- ============================================================================
-- 0008_agent_conversation.sql —— Agent 对话记录
--
-- 起因：Agent 的对话必须持久化。刷新页面、换设备、隔天回来都要能看到
-- 之前聊了什么、当时返回了哪些课程卡片。
--
-- 两层结构：
--   agent_threads   一个会话（对应 AG-UI 的 threadId）
--   agent_messages  会话里的消息（user / assistant，assistant 可带卡片）
--
-- 谁写：Python Agent 服务（app/memory/conversation.py）
-- 谁读：Go 后端（trace 面板与对话历史接口）
-- 与 agent_runs/agent_steps 的关系：
--   run 是"一次运行"，thread 是"一段对话"。一次对话包含多次运行，
--   所以 agent_messages.run_id 指向产生这条回复的那次运行。
-- ============================================================================

-- +goose Up

CREATE TABLE IF NOT EXISTS agent_threads (
    id              BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    thread_id       VARCHAR(32)     NOT NULL COMMENT '会话 ID（AG-UI threadId）',
    user_id         BIGINT UNSIGNED NOT NULL,
    title           VARCHAR(200)    NOT NULL DEFAULT '' COMMENT '取首条用户消息（截断）',
    message_count   INT             NOT NULL DEFAULT 0,
    last_message_at DATETIME(3)     NULL,
    created_at      DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_agent_threads_thread_id (thread_id),
    KEY idx_agent_threads_user_time (user_id, last_message_at)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = 'Agent 会话';

CREATE TABLE IF NOT EXISTS agent_messages (
    id         BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    thread_id  VARCHAR(32)     NOT NULL,
    run_id     VARCHAR(32)     NULL COMMENT 'assistant 消息由哪次运行产生',
    role       VARCHAR(16)     NOT NULL COMMENT 'user / assistant',
    content    MEDIUMTEXT      NOT NULL,
    cards_json JSON            NULL COMMENT '这条回复附带的业务卡片',
    created_at DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_agent_messages_thread (thread_id, id)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COMMENT = 'Agent 对话消息';

-- +goose Down

DROP TABLE IF EXISTS agent_messages;
DROP TABLE IF EXISTS agent_threads;
