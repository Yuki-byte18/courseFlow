-- ============================================================================
-- 0010_agent_intent_dimensions.sql —— 澄清维度的去重记录
--
-- 起因：意图澄清改成"**每次只澄清一个维度**"。
-- 一轮只问一个维度（意图类别 / 动作 / 哪门课），答完回识别节点，
-- 还缺别的维度就继续问下一个。
--
-- 所以需要记住"这个会话已经澄清过哪些维度"：
--   1. 同一维度不重复问 —— 否则用户答了"选课"，下一轮又问"选还是退"，
--      纯属原地打转，这也是"澄清回归"的一种表现
--   2. 也给治理层留证据：哪几个维度总在反复澄清，说明识别侧有问题
--
-- 为什么是"维度集合"而不是一个计数器：
--   维度之间是有序推进的（intent → action → course），
--   只知道"问了几轮"没法判断下一步该问哪个。
-- ============================================================================

-- +goose Up

ALTER TABLE agent_intent_slots
    ADD COLUMN clarified_dimensions JSON NULL
        COMMENT '已经澄清过的维度（intent/action/course/confirm），JSON 数组';

-- +goose Down

ALTER TABLE agent_intent_slots
    DROP COLUMN clarified_dimensions;
