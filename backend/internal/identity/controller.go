package identity

import (
	"errors"
	"net/http"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"

	"courseflow/internal/platform/middleware"
	"courseflow/internal/platform/perms"
)

// Controller 是身份域的 HTTP 层：只负责解析请求、调 service、组装响应。
type Controller struct {
	svc *Service
}

// NewController 构造 Controller。
func NewController(svc *Service) *Controller { return &Controller{svc: svc} }

// RegisterPublic 挂载不需要登录的路由。
func (c *Controller) RegisterPublic(api *gin.RouterGroup) {
	authGroup := api.Group("/auth")
	authGroup.POST("/register", c.Register)
	authGroup.POST("/login", c.Login)
}

// RegisterProtected 挂载需要登录的路由（调用方必须已套上 RequireAuth）。
func (c *Controller) RegisterProtected(authed *gin.RouterGroup) {
	authed.GET("/me", c.Me)
	authed.POST("/auth/logout", c.Logout)
}

// RegisterAdmin 挂载管理员路由。
// 调用方负责套上 RequireRole(admin)；这里只声明每个接口需要什么权限码。
func (c *Controller) RegisterAdmin(admin *gin.RouterGroup) {
	admin.POST("/users", middleware.RequirePerm(perms.UserWrite), c.AdminCreateUser)
}

// Register 处理 POST /api/v1/auth/register。
func (c *Controller) Register(ctx *gin.Context) {
	var req RegisterRequest
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	resp, err := c.svc.Register(ctx.Request.Context(), RegisterInput{
		Username: req.Username,
		Password: req.Password,
		RealName: req.RealName,
		Email:    req.Email,
	})
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}

	ctx.JSON(http.StatusCreated, resp)
}

// Login 处理 POST /api/v1/auth/login。
func (c *Controller) Login(ctx *gin.Context) {
	var req LoginRequest
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	resp, err := c.svc.Login(ctx.Request.Context(), LoginInput{
		Username: req.Username,
		Password: req.Password,
	})
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}

	ctx.JSON(http.StatusOK, resp)
}

// Logout 处理 POST /api/v1/auth/logout。
//
// 返回 204：JWT 是无状态的，服务端不做会话失效，
// 客户端只需丢弃本地 token。这里记录一条审计日志。
func (c *Controller) Logout(ctx *gin.Context) {
	u, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}
	_ = c.svc.Logout(ctx.Request.Context(), u)
	ctx.Status(http.StatusNoContent)
}

// Me 处理 GET /api/v1/me：返回当前登录用户，信息全部来自 token。
func (c *Controller) Me(ctx *gin.Context) {
	u, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}
	ctx.JSON(http.StatusOK, CurrentUserFromToken(u))
}

// AdminCreateUser 处理 POST /api/v1/admin/users：管理员建号，可指定角色。
func (c *Controller) AdminCreateUser(ctx *gin.Context) {
	var req CreateUserRequest
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	info, err := c.svc.CreateUser(ctx.Request.Context(), CreateUserInput{
		Username: req.Username,
		Password: req.Password,
		RealName: req.RealName,
		Email:    req.Email,
		Roles:    req.Roles,
	})
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}

	ctx.JSON(http.StatusCreated, info)
}

// writeServiceError 把业务错误翻译成 HTTP 状态码。
//
// 对外文案刻意保守：不告诉调用方「用户名不存在」还是「密码错了」，
// 两种情况都返回同一句，避免被用来枚举账号。
func (c *Controller) writeServiceError(ctx *gin.Context, err error) {
	switch {
	case errors.Is(err, ErrUsernameTaken):
		respondError(ctx, http.StatusConflict, "username_taken", "用户名已被占用")
	case errors.Is(err, ErrInvalidCredential):
		respondError(ctx, http.StatusUnauthorized, "invalid_credential", "用户名或密码错误")
	case errors.Is(err, ErrUserDisabled):
		respondError(ctx, http.StatusForbidden, "user_disabled", "账号已被禁用，请联系管理员")
	case errors.Is(err, ErrRoleNotFound):
		respondError(ctx, http.StatusInternalServerError, "role_missing", "系统角色未初始化，请联系管理员")
	default:
		// 未预期的错误：日志里留全量，对外只说「服务器内部错误」
		zap.L().Error("身份域未预期错误", zap.Error(err))
		respondError(ctx, http.StatusInternalServerError, "internal_error", "服务器内部错误")
	}
}

// respondError 统一错误响应结构，与鉴权中间件保持一致。
func respondError(ctx *gin.Context, status int, code, message string) {
	ctx.AbortWithStatusJSON(status, gin.H{
		"error": gin.H{
			"code":       code,
			"message":    message,
			"request_id": middleware.RequestIDFrom(ctx),
		},
	})
}
