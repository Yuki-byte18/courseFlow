package enrollment

import (
	"errors"
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"

	"courseflow/internal/platform/middleware"
	"courseflow/internal/platform/perms"
)

// Controller 是选课域的 HTTP 层。
type Controller struct {
	svc *Service
}

// NewController 构造 Controller。
func NewController(svc *Service) *Controller { return &Controller{svc: svc} }

// Register 挂载选课域路由。
//
// 学生端接口统一挂 /my 前缀：身份只从 token 推导，
// 路径里不出现任何 userId，从根上杜绝「改个参数就能操作别人数据」。
func (c *Controller) Register(authed, admin *gin.RouterGroup) {
	authed.GET("/my/round", middleware.RequirePerm(perms.EnrollmentCreate), c.CurrentRound)
	authed.POST("/my/enrollments", middleware.RequirePerm(perms.EnrollmentCreate), c.Enroll)
	authed.DELETE("/my/enrollments/:offering_id", middleware.RequirePerm(perms.EnrollmentDrop), c.Drop)
	authed.GET("/my/enrollments", middleware.RequirePerm(perms.EnrollmentRead), c.MyEnrollments)
	authed.GET("/my/schedule", middleware.RequirePerm(perms.ScheduleRead), c.MySchedule)

	admin.GET("/rounds", middleware.RequirePerm(perms.RoundRead), c.ListRounds)
	admin.POST("/rounds", middleware.RequirePerm(perms.RoundWrite), c.CreateRound)
	admin.PATCH("/rounds/:id", middleware.RequirePerm(perms.RoundWrite), c.UpdateRound)

	// 教务应急：强制退课 / 补录
	admin.POST("/enrollments/force-drop", middleware.RequirePerm(perms.EnrollmentForceDrop), c.ForceDrop)
	admin.POST("/enrollments/supplement", middleware.RequirePerm(perms.EnrollmentSupplement), c.Supplement)
}

// CurrentRound 处理 GET /api/v1/my/round。
func (c *Controller) CurrentRound(ctx *gin.Context) {
	u, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	vo, err := c.svc.CurrentRound(ctx.Request.Context(), u.ID)
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusOK, vo)
}

// Enroll 处理 POST /api/v1/my/enrollments —— 抢课。
func (c *Controller) Enroll(ctx *gin.Context) {
	u, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	var req EnrollRequest
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	vo, err := c.svc.Enroll(ctx.Request.Context(), u, req.OfferingID)
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusCreated, vo)
}

// Drop 处理 DELETE /api/v1/my/enrollments/:offering_id —— 退课。
func (c *Controller) Drop(ctx *gin.Context) {
	u, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	offeringID := parseInt64(ctx.Param("offering_id"))
	if offeringID <= 0 {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "班次 ID 不合法")
		return
	}

	if err := c.svc.Drop(ctx.Request.Context(), u, offeringID); err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.Status(http.StatusNoContent)
}

// MyEnrollments 处理 GET /api/v1/my/enrollments。
func (c *Controller) MyEnrollments(ctx *gin.Context) {
	u, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	query := MyEnrollmentQuery{
		TermID:         parseInt64(ctx.Query("term_id")),
		IncludeDropped: ctx.Query("include_dropped") == "1",
	}

	items, err := c.svc.MyEnrollments(ctx.Request.Context(), u.ID, query)
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"items": items})
}

// MySchedule 处理 GET /api/v1/my/schedule。
func (c *Controller) MySchedule(ctx *gin.Context) {
	u, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	vo, err := c.svc.MySchedule(ctx.Request.Context(), u.ID, parseInt64(ctx.Query("term_id")))
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusOK, vo)
}

// ListRounds 处理 GET /api/v1/admin/rounds。
func (c *Controller) ListRounds(ctx *gin.Context) {
	items, err := c.svc.ListRounds(ctx.Request.Context(), parseInt64(ctx.Query("term_id")))
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"items": items})
}

// CreateRound 处理 POST /api/v1/admin/rounds。
func (c *Controller) CreateRound(ctx *gin.Context) {
	var req CreateRoundRequest
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	vo, err := c.svc.CreateRound(ctx.Request.Context(), req)
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusCreated, vo)
}

// writeServiceError 把业务错误翻译成 HTTP 状态码。
func (c *Controller) writeServiceError(ctx *gin.Context, err error) {
	switch {
	case errors.Is(err, ErrOfferingNotFound):
		respondError(ctx, http.StatusNotFound, "offering_not_found", "班次不存在")
	case errors.Is(err, ErrOfferingNotOpen):
		respondError(ctx, http.StatusConflict, "offering_not_open", "该班次已停开")
	case errors.Is(err, ErrOfferingFull):
		// 409 而不是 400：名额已满是一种"资源冲突"，不是参数错误
		respondError(ctx, http.StatusConflict, "offering_full", "名额已满")
	case errors.Is(err, ErrAlreadyEnrolled):
		respondError(ctx, http.StatusConflict, "already_enrolled", "你已经选过这门课了")
	case errors.Is(err, ErrNotEnrolled):
		respondError(ctx, http.StatusNotFound, "not_enrolled", "你没有选这门课")
	case errors.Is(err, ErrCreditLimit):
		respondError(ctx, http.StatusConflict, "credit_limit", "超出本轮学分上限")
	case errors.Is(err, ErrRoundClosed):
		respondError(ctx, http.StatusForbidden, "round_closed", "当前没有开放中的选课轮次")
	case errors.Is(err, ErrRoundNotStarted):
		respondError(ctx, http.StatusForbidden, "round_not_started", err.Error())
	case errors.Is(err, ErrTimeConflict):
		// 409 而不是 400：这是一次"资源冲突"，不是参数写错
		respondError(ctx, http.StatusConflict, "time_conflict", err.Error())
	case errors.Is(err, ErrGradeRestricted):
		respondError(ctx, http.StatusForbidden, "grade_restricted", err.Error())
	case errors.Is(err, ErrTermNotFound):
		respondError(ctx, http.StatusBadRequest, "term_not_found", "学期不存在")
	case errors.Is(err, ErrInvalidTime):
		respondError(ctx, http.StatusBadRequest, "invalid_time", err.Error())
	default:
		zap.L().Error("选课域未预期错误", zap.Error(err))
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

// parseInt64 宽松解析参数：非法值当作 0，由调用方判空。
func parseInt64(s string) int64 {
	v, err := strconv.ParseInt(s, 10, 64)
	if err != nil {
		return 0
	}
	return v
}
