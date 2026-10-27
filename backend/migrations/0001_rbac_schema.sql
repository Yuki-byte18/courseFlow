-- ============================================================================
-- 0001_rbac_schema.sql —— RBAC 五表
--
--   users ──< user_roles >── roles ──< role_permissions >── permissions
--           (M:N)                    (M:N)
-- ============================================================================

-- +goose Up

CREATE TABLE users (
    id            BIGINT UNSIGNED  NOT NULL AUTO_INCREMENT COMMENT '主键',
    username      VARCHAR(64)      NOT NULL COMMENT '登录账号：学号 / 工号',
    password_hash VARCHAR(255)     NOT NULL COMMENT 'bcrypt 哈希；salt 编码在串内',
    real_name     VARCHAR(64)      NOT NULL COMMENT '真实姓名',
    email         VARCHAR(128)     DEFAULT NULL COMMENT '邮箱，可空',
    status        TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '1=正常 2=禁用',
    created_at    DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    updated_at    DATETIME(3)      NOT NULL DEFAULT CURRENT_TIMESTAMP(3)
                                   ON UPDATE CURRENT_TIMESTAMP(3),
    PRIMARY KEY (id),
    UNIQUE KEY uk_users_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='用户（认证主体）';

CREATE TABLE roles (
    id          SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code        VARCHAR(32)       NOT NULL COMMENT 'student / teacher / admin',
    name        VARCHAR(64)       NOT NULL COMMENT '角色显示名',
    description VARCHAR(255)      NOT NULL DEFAULT '',
    PRIMARY KEY (id),
    UNIQUE KEY uk_roles_code (code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='角色';

CREATE TABLE permissions (
    id          SMALLINT UNSIGNED NOT NULL AUTO_INCREMENT,
    code        VARCHAR(64)       NOT NULL COMMENT '权限码，资源:操作，如 enrollment:create',
    name        VARCHAR(64)       NOT NULL COMMENT '权限显示名',
    resource    VARCHAR(32)       NOT NULL COMMENT '资源（从 code 冗余）',
    action      VARCHAR(32)       NOT NULL COMMENT '操作（从 code 冗余）',
    description VARCHAR(255)      NOT NULL DEFAULT '',
    PRIMARY KEY (id),
    UNIQUE KEY uk_permissions_code (code),
    KEY idx_permissions_resource (resource)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='权限点';

CREATE TABLE user_roles (
    user_id    BIGINT UNSIGNED   NOT NULL,
    role_id    SMALLINT UNSIGNED NOT NULL,
    granted_at DATETIME(3)       NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    PRIMARY KEY (user_id, role_id),
    KEY idx_user_roles_role (role_id),
    CONSTRAINT fk_user_roles_user FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_roles_role FOREIGN KEY (role_id) REFERENCES roles (id) ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='用户-角色（多对多）';

CREATE TABLE role_permissions (
    role_id       SMALLINT UNSIGNED NOT NULL,
    permission_id SMALLINT UNSIGNED NOT NULL,
    PRIMARY KEY (role_id, permission_id),
    KEY idx_role_permissions_perm (permission_id),
    CONSTRAINT fk_role_permissions_role FOREIGN KEY (role_id) REFERENCES roles (id) ON DELETE CASCADE,
    CONSTRAINT fk_role_permissions_perm FOREIGN KEY (permission_id) REFERENCES permissions (id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci COMMENT='角色-权限（多对多）';

INSERT INTO roles (code, name, description) VALUES
    ('student', '学生',       '抢课、退课、查课表、使用业务 Agent'),
    ('teacher', '教师',       '只读：查看自己开设的班次与选课名单'),
    ('admin',   '教务管理员', '排课、配置选课轮次、预热库存、补录、查看报表与大盘');

-- +goose Down

DROP TABLE IF EXISTS role_permissions;
DROP TABLE IF EXISTS user_roles;
DROP TABLE IF EXISTS permissions;
DROP TABLE IF EXISTS roles;
DROP TABLE IF EXISTS users;