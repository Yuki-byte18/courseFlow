package identity

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"gorm.io/gorm"

	"courseflow/internal/platform/mysql"
)

// DAO 是身份域的数据访问层。
//
// 硬约束：这里只做数据库读写，不含任何业务判断。
// 「密码对不对」「账号能不能登录」都属于 service 的职责。
type DAO struct {
	db *gorm.DB
}

// NewDAO 构造 DAO。
func NewDAO(db *gorm.DB) *DAO { return &DAO{db: db} }

// FindByUsername 按用户名查用户。查不到返回 (nil, nil)。
func (d *DAO) FindByUsername(ctx context.Context, username string) (*User, error) {
	var u User
	err := d.db.WithContext(ctx).Where("username = ?", username).First(&u).Error
	if err != nil {
		if errors.Is(err, gorm.ErrRecordNotFound) {
			return nil, nil
		}
		return nil, fmt.Errorf("按用户名查用户失败: %w", err)
	}
	return &u, nil
}

// CreateUserWithRoles 在一个事务里创建用户并绑定一个或多个角色。
//
// 用事务是因为「用户建好了但角色没绑上」会造出一个谁都登不进来的账号。
// 用户名唯一索引兜底并发创建：两个请求同时通过预检查时，
// 后一个会撞唯一键失败，由 service 翻译成 ErrUsernameTaken。
func (d *DAO) CreateUserWithRoles(
	ctx context.Context,
	username, passwordHash, realName, email string,
	roleCodes []string,
) (*User, error) {
	// 先去重，避免同一角色重复插入触发复合主键冲突
	seen := make(map[string]struct{}, len(roleCodes))
	codes := make([]string, 0, len(roleCodes))
	for _, c := range roleCodes {
		c = strings.TrimSpace(c)
		if c == "" {
			continue
		}
		if _, ok := seen[c]; ok {
			continue
		}
		seen[c] = struct{}{}
		codes = append(codes, c)
	}
	if len(codes) == 0 {
		return nil, errors.New("至少需要指定一个角色")
	}

	var created *User

	err := mysql.WithTx(ctx, d.db, func(tx *gorm.DB) error {
		u := &User{
			Username:     username,
			PasswordHash: passwordHash,
			RealName:     realName,
			Email:        email,
			Status:       UserStatusNormal,
		}
		if err := tx.Create(u).Error; err != nil {
			return fmt.Errorf("写入 users 失败: %w", err)
		}

		for _, code := range codes {
			var role Role
			if err := tx.Where("code = ?", code).First(&role).Error; err != nil {
				if errors.Is(err, gorm.ErrRecordNotFound) {
					return ErrRoleNotFound
				}
				return fmt.Errorf("查询角色 %q 失败: %w", code, err)
			}
			if err := tx.Create(&UserRole{UserID: u.ID, RoleID: role.ID}).Error; err != nil {
				return fmt.Errorf("写入 user_roles(%s) 失败: %w", code, err)
			}
		}

		created = u
		return nil
	})
	if err != nil {
		return nil, err
	}
	return created, nil
}

// RolesAndPerms 查出用户的角色码与权限码（已去重）。
//
// 这里刻意用原生 SQL：GORM 的 Pluck 会覆盖掉 Select 里的 DISTINCT，
// 多表 JOIN 下很容易悄悄返回重复行。SQL 写清楚比"看起来优雅"重要。
func (d *DAO) RolesAndPerms(ctx context.Context, userID int64) (roles []string, permCodes []string, err error) {
	err = d.db.WithContext(ctx).Raw(`
		SELECT r.code
		FROM roles r
		JOIN user_roles ur ON ur.role_id = r.id
		WHERE ur.user_id = ?
		ORDER BY r.code`, userID,
	).Scan(&roles).Error
	if err != nil {
		return nil, nil, fmt.Errorf("查询用户角色失败: %w", err)
	}

	err = d.db.WithContext(ctx).Raw(`
		SELECT DISTINCT p.code
		FROM permissions p
		JOIN role_permissions rp ON rp.permission_id = p.id
		JOIN user_roles ur ON ur.role_id = rp.role_id
		WHERE ur.user_id = ?
		ORDER BY p.code`, userID,
	).Scan(&permCodes).Error
	if err != nil {
		return nil, nil, fmt.Errorf("查询用户权限失败: %w", err)
	}

	return roles, permCodes, nil
}

// IsDuplicateKey 判断错误是否是唯一键冲突。
//
// 不依赖具体的驱动错误类型：GORM 会把 MySQL 的 1062 包成自己的错误，
// 这里用字符串匹配做兜底，够用且不引入额外依赖。
func IsDuplicateKey(err error) bool {
	if err == nil {
		return false
	}
	msg := strings.ToLower(err.Error())
	return strings.Contains(msg, "duplicate") || strings.Contains(msg, "1062")
}
