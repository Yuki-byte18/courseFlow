-- ============================================================================
-- 0004_enrollment_schema.sql —— 选课轮次与选课记录
--
--   terms ──< rounds
--   enrollments >── users（学生）
--               >── offerings
--               >── rounds
--
-- 两条不能违反的不变量都在这一张表上：
--   1. UNIQUE (student_id, offering_id)  —— 同一学生同一班次只能有一条记录
--   2. offerings.taken 与有效记录数必须一致 —— 靠事务保证
--
-- 注意唯一键【不含 status】：退课是 UPDATE status=2，不是 DELETE。
-- 因此「退课后重选」走的是把 status 改回 1，而不是再插入一行。
-- ============================================================================

-- +goose Up

CREATE TABLE rounds (
    id          BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
    term_id     BIGINT UNSIGNED  NOT NULL,
    name        VARCHAR(64)      NOT NULL COMMENT '第一轮 / 正选 / 补选',
    opens_at    DATETIME(3)      NOT NULL COMMENT '开放时间',
    closes_at   DATETIME(3)      NOT NULL COMMENT '关闭时间',
    max_credits DECIMAL(4,1)     NOT NULL DEFAULT 30.0 COMMENT '本轮学分上限',
    allow_grade TINYINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '开放年级；0=不限',
    status      TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1=启用 2=关闭',
    created_at  DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    KEY idx_rounds_term (term_id, status),
    KEY idx_rounds_window (opens_at, closes_at),
    CONSTRAINT fk_rounds_term FOREIGN KEY (term_id) REFERENCES terms (id) ON DELETE CASCADE,
    CONSTRAINT ck_rounds_window CHECK (closes_at > opens_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='选课轮次';

CREATE TABLE enrollments (
    id          BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
    student_id  BIGINT UNSIGNED  NOT NULL,
    offering_id BIGINT UNSIGNED  NOT NULL,
    round_id    BIGINT UNSIGNED  NOT NULL COMMENT '在哪一轮选上的',
    credits     DECIMAL(3,1)     NOT NULL COMMENT '学分快照：课程学分之后改了也不影响历史统计',
    status      TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1=有效 2=已退',
    created_at  DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    dropped_at  DATETIME(3)      DEFAULT NULL,
    PRIMARY KEY (id),
    -- 防重复选的最后一道防线。并发下两个请求同时通过应用层预检查时，这里会拦住一个。
    UNIQUE KEY uk_enrollments_student_offering (student_id, offering_id),
    KEY idx_enrollments_offering (offering_id, status),
    KEY idx_enrollments_student (student_id, status),
    CONSTRAINT fk_enrollments_student FOREIGN KEY (student_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_enrollments_offering FOREIGN KEY (offering_id) REFERENCES offerings (id) ON DELETE RESTRICT,
    CONSTRAINT fk_enrollments_round FOREIGN KEY (round_id) REFERENCES rounds (id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='选课记录';

-- +goose Down

DROP TABLE IF EXISTS enrollments;
DROP TABLE IF EXISTS rounds;
