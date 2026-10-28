package middleware

import (
	"errors"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"

	"courseflow/internal/platform/auth"
)

// authorizationHeader 是携带 token 的请求头。
const authorizationHeader = "Authorization"

// bearerPrefix 是 token 的前缀，大小写不敏感地匹配。
const bearerPrefix = "bearer "

// unauthenticatedMessage 统一对外文案，避免泄露「token 为什么无效」。
// 具体的失败原因只写进日志，不返回给客户端。
const unauthenticatedMessage = "认证失败，请重新登录"

// RequireAuth 校验 Authorization 头里的 JWT，并把身份注入请求上下文。
//
// 校验通过后，后续 handler 和 service 都可以用 auth.FromContext(c.Request.Context())
// 或者 middleware.CurrentUser(c) 拿到当前用户。
func RequireAuth(tm *auth.TokenManager, log *zap.Logger) gin.HandlerFunc {
	return func(c *gin.Context) {
		raw := c.GetHeader(authorizationHeader)
		if raw == "" {
			logAuthFailure(log, c, "缺少 Authorization 请求头")
			abortUnauthorized(c, "missing_token")
			return
		}

		if len(raw) <= len(bearerPrefix) || !strings.EqualFold(raw[:len(bearerPrefix)], bearerPrefix) {
			logAuthFailure(log, c, "Authorization 头格式不是 Bearer <token>")
			abortUnauthorized(c, "malformed_token")
			return
		}

		token := strings.TrimSpace(raw[len(bearerPrefix):])
		u, err := tm.Parse(token)
		if err != nil {
			switch {
			case errors.Is(err, auth.ErrTokenExpired):
				logAuthFailure(log, c, "token 已过期")
				abortWithCode(c, http.StatusUnauthorized, "token_expired", "登录已过期，请重新登录")
			default:
				logAuthFailure(log, c, "token 校验失败: "+err.Error())
				abortUnauthorized(c, "invalid_token")
			}
			return
		}

		// 把身份挂到标准库 context 上，业务层不必接触 gin
		c.Request = c.Request.WithContext(auth.WithUser(c.Request.Context(), u))
		c.Next()
	}
}

// RequireRole 要求当前用户拥有任意一个给定角色。
//
// 必须挂在 RequireAuth 之后，否则上下文里没有用户，一律 401。
func RequireRole(codes ...string) gin.HandlerFunc {
	return func(c *gin.Context) {
		u, ok := CurrentUser(c)
		if !ok {
			abortUnauthorized(c, "missing_token")
			return
		}
		if !u.HasRole(codes...) {
			// 把要求的角色写进文案：否则调用方看到「无权访问」只能靠猜
			abortForbidden(c, "需要以下角色之一："+strings.Join(codes, " / "))
			return
		}
		c.Next()
	}
}

// RequirePerm 要求当前用户拥有任意一个给定权限码。
//
// 权限码来自 JWT 里的 perms，所以这里不查库 ——
// 这也意味着**权限变更后必须重新登录才会生效**。
// 必须挂在 RequireAuth 之后。
func RequirePerm(codes ...string) gin.HandlerFunc {
	return func(c *gin.Context) {
		u, ok := CurrentUser(c)
		if !ok {
			abortUnauthorized(c, "missing_token")
			return
		}
		if !u.HasPerm(codes...) {
			// 文案里带上具体权限码：这是排查「为什么 403」的唯一线索。
			// 对已登录用户暴露"你需要什么权限"不构成信息泄露，反而省掉很多来回。
			abortWithCode(c, http.StatusForbidden, "missing_permission",
				"缺少所需权限："+strings.Join(codes, " / ")+
					"（如刚被授予权限，请重新登录使其生效）")
			return
		}
		c.Next()
	}
}

// CurrentUser 从请求上下文取出当前用户。
func CurrentUser(c *gin.Context) (*auth.User, bool) {
	return auth.FromContext(c.Request.Context())
}

// MustCurrentUser 取出当前用户，取不到返回 nil。
// 只在确定挂了 RequireAuth 的 handler 里用。
func MustCurrentUser(c *gin.Context) *auth.User {
	u, _ := CurrentUser(c)
	return u
}

// logAuthFailure 记录认证失败。
//
// 只记失败原因和低基数信息，绝不记录 token 原文——
// 日志一旦被采集，token 泄露等于账号泄露。
func logAuthFailure(log *zap.Logger, c *gin.Context, reason string) {
	log.Warn("auth failed",
		zap.String("reason", reason),
		zap.String("path", c.Request.URL.Path),
		zap.String("method", c.Request.Method),
		zap.String("client_ip", c.ClientIP()),
		zap.String("request_id", RequestIDFrom(c)),
	)
}

// abortUnauthorized 统一的 401 响应。
func abortUnauthorized(c *gin.Context, code string) {
	abortWithCode(c, http.StatusUnauthorized, code, unauthenticatedMessage)
}

// abortForbidden 统一的 403 响应。
func abortForbidden(c *gin.Context, message string) {
	abortWithCode(c, http.StatusForbidden, "forbidden", message)
}

// abortWithCode 写统一错误结构并终止后续 handler。
func abortWithCode(c *gin.Context, status int, code, message string) {
	c.AbortWithStatusJSON(status, gin.H{
		"error": gin.H{
			"code":       code,
			"message":    message,
			"request_id": RequestIDFrom(c),
		},
	})
}
