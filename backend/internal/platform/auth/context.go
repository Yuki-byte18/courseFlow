package auth

import "context"

type ctxKeyUser struct{}

// WithUser 把身份放进标准库上下文。
//
// 用私有空结构体当 key，而不是字符串：防止别的包用同名 key 撞车。
func WithUser(ctx context.Context, u *User) context.Context {
	return context.WithValue(ctx, ctxKeyUser{}, u)
}

// FromContext 取出当前用户。未认证时返回 (nil, false)。
//
// 业务层的 service 只要拿到 context.Context 就能取身份，
// 不需要 import gin，也不需要把 *gin.Context 透传到业务层。
func FromContext(ctx context.Context) (*User, bool) {
	u, ok := ctx.Value(ctxKeyUser{}).(*User)
	if !ok || u == nil {
		return nil, false
	}
	return u, true
}

// MustFromContext 取出当前用户，取不到就返回 nil。
// 只在「中间件已经保证登录」的 handler 里用。
func MustFromContext(ctx context.Context) *User {
	u, _ := FromContext(ctx)
	return u
}
