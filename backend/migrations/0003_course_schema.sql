-- ============================================================================
-- 0003_course_schema.sql —— 学期、课程、开课班次、上课时间
--
--   terms ──< offerings >── courses
--               │  └──< offering_slots
--               └── users（教师）
--
-- 核心表是 offerings：它同时持有 capacity（容量）和 taken（已选人数）。
-- 这两个字段决定了整个系统的高并发形态 —— M2 会把它们镜像进 Redis。
-- ============================================================================

-- +goose Up

CREATE TABLE terms (
    id         BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
    code       VARCHAR(16)      NOT NULL COMMENT '学期码：2026S / 2026F',
    name       VARCHAR(64)      NOT NULL COMMENT '2026 春季学期',
    starts_on  DATETIME(3)      NOT NULL COMMENT '学期开始；时间部分恒为 00:00:00',
    ends_on    DATETIME(3)      NOT NULL,
    is_current TINYINT UNSIGNED NOT NULL DEFAULT 0 COMMENT '1=当前学期',
    created_at DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_terms_code (code),
    KEY idx_terms_current (is_current)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='学期';

CREATE TABLE courses (
    id          BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
    code        VARCHAR(32)      NOT NULL COMMENT '课程号',
    name        VARCHAR(128)     NOT NULL COMMENT '课程名',
    credits     DECIMAL(3,1)     NOT NULL COMMENT '学分；支持 0.5 递增',
    department  VARCHAR(64)      NOT NULL DEFAULT '' COMMENT '开课院系',
    description VARCHAR(512)     NOT NULL DEFAULT '',
    status      TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1=启用 2=停用',
    created_at  DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at  DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                                 ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_courses_code (code),
    KEY idx_courses_department (department)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='课程主数据';

CREATE TABLE offerings (
    id         BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
    term_id    BIGINT UNSIGNED  NOT NULL,
    course_id  BIGINT UNSIGNED  NOT NULL,
    teacher_id BIGINT UNSIGNED  NOT NULL COMMENT '授课教师（users.id）',
    section_no VARCHAR(16)      NOT NULL DEFAULT '01' COMMENT '班号',
    capacity   INT UNSIGNED     NOT NULL COMMENT '容量',
    taken      INT UNSIGNED     NOT NULL DEFAULT 0 COMMENT '已选人数；抢课热点字段',
    location   VARCHAR(64)      NOT NULL DEFAULT '' COMMENT '上课地点',
    status     TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1=开放 2=停开',
    created_at DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                                ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_offerings_section (term_id, course_id, section_no),
    KEY idx_offerings_term (term_id, status),
    KEY idx_offerings_course (course_id),
    KEY idx_offerings_teacher (teacher_id),
    CONSTRAINT fk_offerings_term FOREIGN KEY (term_id) REFERENCES terms (id) ON DELETE RESTRICT,
    CONSTRAINT fk_offerings_course FOREIGN KEY (course_id) REFERENCES courses (id) ON DELETE RESTRICT,
    CONSTRAINT fk_offerings_teacher FOREIGN KEY (teacher_id) REFERENCES users (id) ON DELETE RESTRICT,
    -- 宪法第 2 条：名额不可为负。就算应用层写错了，数据库这里也会拒绝。
    CONSTRAINT ck_offerings_stock CHECK (taken <= capacity)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='开课班次（名额载体）';

CREATE TABLE offering_slots (
    id           BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT,
    offering_id  BIGINT UNSIGNED  NOT NULL,
    weekday      TINYINT UNSIGNED NOT NULL COMMENT '1=周一 … 7=周日',
    start_period TINYINT UNSIGNED NOT NULL COMMENT '起始节次',
    end_period   TINYINT UNSIGNED NOT NULL COMMENT '结束节次',
    PRIMARY KEY (id),
    KEY idx_slots_offering (offering_id),
    CONSTRAINT fk_slots_offering FOREIGN KEY (offering_id) REFERENCES offerings (id) ON DELETE CASCADE,
    CONSTRAINT ck_slots_range CHECK (
        weekday BETWEEN 1 AND 7
        AND start_period >= 1
        AND end_period >= start_period
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='上课时间段';

-- +goose Down

DROP TABLE IF EXISTS offering_slots;
DROP TABLE IF EXISTS offerings;
DROP TABLE IF EXISTS courses;
DROP TABLE IF EXISTS terms;
