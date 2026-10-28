-- ============================================================================
-- 0005_offering_import_permission.sql —— 教师导入课表的权限点
--
-- 背景：教师端需要「一键导入课表」—— 把自己的授课安排批量建成班次。
-- 这是一个**受限写操作**：服务端强制把 teacher_id 设为当前登录用户，
-- 所以教师只能为自己开班，既可以批量录入，又不会破坏权限边界。
--
-- 按项目约定，**新增权限点必须单独一次迁移**：
-- admin 的全量授权是 0002 里用 CROSS JOIN 一次性给的，
-- 之后新加的权限点不会被自动授予，必须在这里补上。
-- ============================================================================

-- +goose Up

INSERT INTO permissions (code, name, resource, action, description) VALUES
    ('offering:import', '导入课表', 'offering', 'import', '教师批量导入自己的授课课表')
ON DUPLICATE KEY UPDATE
    name        = VALUES(name),
    resource    = VALUES(resource),
    action      = VALUES(action),
    description = VALUES(description);

-- 授予教师与管理员。学生不需要。
INSERT IGNORE INTO role_permissions (role_id, permission_id)
SELECT r.id, p.id
  FROM roles r
  CROSS JOIN permissions p
 WHERE r.code IN ('teacher', 'admin')
   AND p.code = 'offering:import';

-- +goose Down

DELETE FROM role_permissions
 WHERE permission_id IN (SELECT id FROM permissions WHERE code = 'offering:import');

DELETE FROM permissions WHERE code = 'offering:import';
