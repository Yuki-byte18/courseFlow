-- ============================================================================
-- 0002_permissions_seed.sql —— 权限点清单 + 角色权限关联
--
-- 权限码格式：资源:操作
--   资源 = 业务对象（offering / enrollment / roster …）
--   操作 = 动作（read / write / create / drop / preheat …）
--
-- 关联规则：
--   student  只读自己的数据 + 抢课退课 + 用 Agent
--   teacher  只读（自己的班次与名单），没有任何写权限
--   admin    全部权限；数据范围另行在后端 service 层按身份过滤
--
-- 注意：以后新增权限点时，除了插 permissions，还要在 000N 迁移里
--      把它授给 admin（本次用 CROSS JOIN 一次性授全量，之后需显式补）。
-- ============================================================================

-- +goose Up

-- ---------------------------------------------------------------------------
-- 1. 权限点
-- ---------------------------------------------------------------------------
INSERT INTO permissions (code, name, resource, action, description) VALUES
    -- 课程与班次
    ('offering:read',        '查看课程与班次',   'offering',   'read',        '学生端筛课、管理端查看排课结果'),
    ('offering:write',       '维护开课班次',     'offering',   'write',       '排课：课程 + 教师 + 时间 + 地点 + 容量'),
    ('course:read',          '查看课程库',       'course',     'read',        '管理端课程库列表'),
    ('course:write',         '维护课程库',       'course',     'write',       '新建、修改课程主数据'),

    -- 学生端
    ('schedule:read',        '查看我的课表',     'schedule',   'read',        '学生端周视图课表'),
    ('enrollment:read',      '查看我的选课',     'enrollment', 'read',        '学生端选课状态与结果轮询'),
    ('enrollment:create',    '发起选课',         'enrollment', 'create',      '抢课：占用名额'),
    ('enrollment:drop',      '退课',             'enrollment', 'drop',        '学生退掉自己的课'),

    -- 管理员操作
    ('enrollment:force_drop',  '强制退课',       'enrollment', 'force_drop',  '管理员强制退掉某学生的课'),
    ('enrollment:supplement',  '补录选课',       'enrollment', 'supplement',  '管理员代学生补录'),
    ('round:read',             '查看选课轮次',   'round',      'read',        '查看轮次开放时间与规则'),
    ('round:write',            '配置选课轮次',   'round',      'write',       '开放时间、每生学分上限、开放年级'),
    ('stock:preheat',          '预热库存',       'stock',      'preheat',     '把班次容量灌进 Redis，开抢前必做'),
    ('user:write',             '管理账号与角色', 'user',       'write',       '创建教师/管理员账号、分配角色'),

    -- 教师端（只读）
    ('roster:read',            '查看选课名单',   'roster',     'read',        '教师查看自己班次的名单'),
    ('roster:export',          '导出选课名单',   'roster',     'export',      '导出点名册 CSV'),

    -- 数据与 Agent
    ('dashboard:read',         '查看大盘与报表', 'dashboard',  'read',        '实时大盘 + 离线报表'),
    ('etl:read',               '查看 ETL 状态',  'etl',        'read',        '离线任务运行情况'),
    ('agent:chat',             '使用业务 Agent', 'agent',      'chat',        '自然语言查课、给建议');


-- ---------------------------------------------------------------------------
-- 2. 角色 → 权限
-- ---------------------------------------------------------------------------

-- 学生：看课、选课、退课、看课表、用 Agent
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE code = 'student'), id
FROM permissions
WHERE code IN (
    'offering:read',
    'schedule:read',
    'enrollment:read',
    'enrollment:create',
    'enrollment:drop',
    'agent:chat'
);

-- 教师：完全只读。注意不含任何 enrollment:create/drop——
-- 教师不能替学生选课，这是业务约束，不是权限漏配。
INSERT INTO role_permissions (role_id, permission_id)
SELECT (SELECT id FROM roles WHERE code = 'teacher'), id
FROM permissions
WHERE code IN (
    'offering:read',
    'roster:read',
    'roster:export'
);

-- 管理员：授予当下已存在的全部权限。
-- 用 CROSS JOIN 而不是逐个列举，是为了让这份迁移无需随权限点增加而改动；
-- 但以后新增权限点时，仍需单独写迁移把它授给 admin。
INSERT INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
FROM roles r
CROSS JOIN permissions p
WHERE r.code = 'admin';


-- +goose Down

-- 删除权限点即可：role_permissions 上的外键是 ON DELETE CASCADE，
-- 关联行会跟着一起消失，不需要单独删。
DELETE FROM permissions
WHERE code IN (
    'offering:read', 'offering:write', 'course:read', 'course:write',
    'schedule:read', 'enrollment:read', 'enrollment:create', 'enrollment:drop',
    'enrollment:force_drop', 'enrollment:supplement',
    'round:read', 'round:write', 'stock:preheat', 'user:write',
    'roster:read', 'roster:export',
    'dashboard:read', 'etl:read', 'agent:chat'
);
