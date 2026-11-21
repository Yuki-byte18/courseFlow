-- ============================================================================
-- 0009_agent_intent_slots.sql —— 意图路由层的槽位状态
--
-- 起因：意图路由层现在支持**多轮澄清**。澄清会终止本轮、等用户回复，
-- 所以"已经抽到了什么槽位、澄清到第几轮、上一句问了什么"必须跨请求保存。
--
-- 为什么单独一张表而不是塞进 agent_threads：
--   1. 槽位是**路由层的运行状态**，不是对话内容。生命周期也不同 ——
--      一次话题结束就应该清空，而对话记录要长期保留。
--   2. 读写频率高（每轮一次），单独一张窄表比反复读写宽表更省。
--   3. 清空槽位不应该影响对话历史，反之亦然。
--
-- 关键字段：
--   slots_json          当前累积的槽位（增量合并的结果，不是重跑出来的）
--   clarify_round       已经澄清了几轮（用于上限判定）
--   confidence_history  最近几轮的置信度（用于判断"越澄清越差"）
--   pending_json        上一轮问了什么（确认句/选项），下一轮据此理解"是"/"第一个"
--
-- 谁写：Python Agent 服务
-- 谁读：Python Agent 服务（Go 只做透传，不关心这一层）
-- ============================================================================

-- +goose Up

CREATE TABLE IF NOT EXISTS agent_intent_slots (
    thread_id          VARCHAR(32)     NOT NULL COMMENT '会话 ID，一个会话一份槽位状态',
    user_id            BIGINT UNSIGNED NOT NULL COMMENT '归属用户，跨账号隔离的锚点',
    slots_json         JSON            NULL     COMMENT '累积槽位：action / course_codes / weekday / count',
    clarify_round      INT             NOT NULL DEFAULT 0 COMMENT '已澄清轮数',
    last_intent        VARCHAR(24)     NOT NULL DEFAULT '' COMMENT '最近一次识别出的意图',
    last_confidence    DECIMAL(4, 3)   NULL     COMMENT '最近一次融合置信度',
    confidence_history JSON            NULL     COMMENT '最近若干轮置信度，用于回归判定',
    pending_json       JSON            NULL     COMMENT '上一轮的澄清内容（确认句/选项/待确认意图）',
    created_at         DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at         DATETIME(3)     NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                                       ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (thread_id),
    KEY idx_agent_intent_slots_user (user_id, updated_at)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4 COMMENT = '意图路由层的跨轮槽位状态';

-- +goose Down

DROP TABLE IF EXISTS agent_intent_slots;
