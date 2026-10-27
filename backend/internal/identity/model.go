// Package identity 是身份域：用户、角色、权限的持久化与登录注册用例。
//
// 表结构（见 backend/migrations/0001_rbac_schema.sql）：
//
//	users ──< user_roles >── roles ──< role_permissions >── permissions
//
// 这个包只负责「谁是谁、能干什么」以及签发凭据。
// JWT 的加解密原语在 platform/auth，不在这里重复实现。
package identity

import "time"

// 用户状态。
const (
	UserStatusNormal   int8 = 1 // 正常
	UserStatusDisabled int8 = 2 // 禁用
)

// 角色标识，与 roles.code 对应。
const (
	RoleStudent = "student"
	RoleTeacher = "teacher"
	RoleAdmin   = "admin"
)

// IsKnownRole 判断角色码是否是系统已知的三个角色之一。
//
// 放在 model 而不是 dao：它是对「角色枚举」的合法性判断，
// 与数据库访问无关。
func IsKnownRole(code string) bool {
	switch code {
	case RoleStudent, RoleTeacher, RoleAdmin:
		return true
	default:
		return false
	}
}

// User 对应 users 表。只映射表结构，不含业务逻辑。
type User struct {
	ID           int64     `gorm:"column:id;primaryKey"`
	Username     string    `gorm:"column:username"`
	PasswordHash string    `gorm:"column:password_hash"`
	RealName     string    `gorm:"column:real_name"`
	Email        string    `gorm:"column:email"`
	Status       int8      `gorm:"column:status"`
	CreatedAt    time.Time `gorm:"column:created_at"`
	UpdatedAt    time.Time `gorm:"column:updated_at"`
}

// TableName 显式指定表名，不依赖 GORM 的复数化推断。
func (User) TableName() string { return "users" }

// IsNormal 判断账号是否处于可用状态。
func (u *User) IsNormal() bool { return u.Status == UserStatusNormal }

// Role 对应 roles 表。
type Role struct {
	ID          int64  `gorm:"column:id;primaryKey"`
	Code        string `gorm:"column:code"`
	Name        string `gorm:"column:name"`
	Description string `gorm:"column:description"`
}

// TableName 显式指定表名。
func (Role) TableName() string { return "roles" }

// Permission 对应 permissions 表。
type Permission struct {
	ID     int64  `gorm:"column:id;primaryKey"`
	Code   string `gorm:"column:code"`
	Name   string `gorm:"column:name"`
	Action string `gorm:"column:action"`
}

// TableName 显式指定表名。
func (Permission) TableName() string { return "permissions" }

// UserRole 对应 user_roles 关联表（复合主键）。
type UserRole struct {
	UserID int64 `gorm:"column:user_id;primaryKey"`
	RoleID int64 `gorm:"column:role_id;primaryKey"`
}

// TableName 显式指定表名。
func (UserRole) TableName() string { return "user_roles" }
