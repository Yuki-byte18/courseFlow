package identity

import "time"

// UserInfo 是「当前用户是谁、能干什么」的对外视图。
//
// 它同时也是前端做路由守卫和按钮显隐的依据：前端拿 roles 和 perms
// 判断该渲染哪些菜单。但真正的安全边界在后端，前端隐藏只是体验。
type UserInfo struct {
	ID       int64    `json:"user_id"`
	Username string   `json:"username"`
	RealName string   `json:"real_name"`
	Roles    []string `json:"roles"`
	Perms    []string `json:"perms"`
}

// AuthResponse 是登录 / 注册成功后的响应。
type AuthResponse struct {
	Token     string    `json:"token"`
	ExpiresAt time.Time `json:"expires_at"`
	User      UserInfo  `json:"user"`
}
