package course

import (
	"errors"
	"net/http"
	"strconv"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"

	"courseflow/internal/platform/middleware"
	"courseflow/internal/platform/perms"
)

// Controller 是课程域的 HTTP 层：只负责解析请求、调 service、组装响应。
type Controller struct {
	svc *Service
}

// NewController 构造 Controller。
func NewController(svc *Service) *Controller { return &Controller{svc: svc} }

// Register 挂载课程域路由。
//
// authed 是"已登录"分组，admin 是"已登录 + 管理员角色"分组（由 main 装配）。
// 每个接口在这里声明自己需要的权限码，装配层不再重复。
func (c *Controller) Register(authed, admin *gin.RouterGroup) {
	// 读：所有登录用户（学生选课台要用）
	authed.GET("/terms", middleware.RequirePerm(perms.OfferingRead), c.ListTerms)
	authed.GET("/offerings", middleware.RequirePerm(perms.OfferingRead), c.ListOfferings)
	authed.GET("/offerings/:id", middleware.RequirePerm(perms.OfferingRead), c.OfferingDetail)
	authed.GET("/offerings/:id/roster", middleware.RequirePerm(perms.RosterRead), c.Roster)

	// 课程主数据只有管理员能看
	authed.GET("/courses", middleware.RequirePerm(perms.CourseRead), c.ListCourses)

	// 教师端：我的班次 + 一键导入课表
	authed.GET("/my/offerings", middleware.RequirePerm(perms.OfferingRead), c.MyOfferings)
	authed.POST("/my/offerings/import", middleware.RequirePerm(perms.OfferingImport), c.ImportOfferings)

	// 名单导出：教师与管理员都需要
	authed.GET("/offerings/:id/roster/export", middleware.RequirePerm(perms.RosterExport), c.RosterExport)

	// 写：管理员
	admin.POST("/terms", middleware.RequirePerm(perms.OfferingWrite), c.CreateTerm)
	admin.POST("/terms/:id/current", middleware.RequirePerm(perms.OfferingWrite), c.SetCurrentTerm)
	admin.POST("/courses", middleware.RequirePerm(perms.CourseWrite), c.CreateCourse)
	admin.PATCH("/courses/:id", middleware.RequirePerm(perms.CourseWrite), c.UpdateCourse)
	admin.POST("/offerings", middleware.RequirePerm(perms.OfferingWrite), c.CreateOffering)
	admin.PATCH("/offerings/:id", middleware.RequirePerm(perms.OfferingWrite), c.UpdateOffering)
}

// ListTerms 处理 GET /api/v1/terms。
func (c *Controller) ListTerms(ctx *gin.Context) {
	items, err := c.svc.ListTerms(ctx.Request.Context())
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"items": items})
}

// ListCourses 处理 GET /api/v1/courses。
func (c *Controller) ListCourses(ctx *gin.Context) {
	items, err := c.svc.ListCourses(ctx.Request.Context(), ctx.Query("keyword"), 0)
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusOK, gin.H{"items": items})
}

// ListOfferings 处理 GET /api/v1/offerings。
func (c *Controller) ListOfferings(ctx *gin.Context) {
	query := OfferingQuery{
		TermID:     parseInt64(ctx.Query("term_id")),
		Department: ctx.Query("department"),
		Keyword:    ctx.Query("keyword"),
		Page:       int(parseInt64(ctx.Query("page"))),
		PageSize:   int(parseInt64(ctx.Query("page_size"))),
	}

	page, err := c.svc.ListOfferings(ctx.Request.Context(), query)
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusOK, page)
}

// OfferingDetail 处理 GET /api/v1/offerings/:id。
func (c *Controller) OfferingDetail(ctx *gin.Context) {
	id := parseInt64(ctx.Param("id"))
	if id <= 0 {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "班次 ID 不合法")
		return
	}

	vo, err := c.svc.OfferingDetail(ctx.Request.Context(), id)
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusOK, vo)
}

// Roster 处理 GET /api/v1/offerings/:id/roster。
func (c *Controller) Roster(ctx *gin.Context) {
	id := parseInt64(ctx.Param("id"))
	if id <= 0 {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "班次 ID 不合法")
		return
	}

	viewer, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	vo, err := c.svc.Roster(ctx.Request.Context(), id, viewer)
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusOK, vo)
}

// MyOfferings 处理 GET /api/v1/my/offerings —— 教师自己的班次。
func (c *Controller) MyOfferings(ctx *gin.Context) {
	u, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	query := OfferingQuery{
		TermID:   parseInt64(ctx.Query("term_id")),
		Keyword:  ctx.Query("keyword"),
		Page:     int(parseInt64(ctx.Query("page"))),
		PageSize: int(parseInt64(ctx.Query("page_size"))),
	}

	page, err := c.svc.MyOfferings(ctx.Request.Context(), u.ID, query)
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusOK, page)
}

// ImportOfferings 处理 POST /api/v1/my/offerings/import —— 一键导入课表。
func (c *Controller) ImportOfferings(ctx *gin.Context) {
	u, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	var req ImportOfferingsRequest
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	result, err := c.svc.ImportOfferings(ctx.Request.Context(), u.ID, req)
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusOK, result)
}

// CreateTerm 处理 POST /api/v1/admin/terms。
func (c *Controller) CreateTerm(ctx *gin.Context) {
	var req CreateTermRequest
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	vo, err := c.svc.CreateTerm(ctx.Request.Context(), req)
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusCreated, vo)
}

// SetCurrentTerm 处理 POST /api/v1/admin/terms/:id/current。
func (c *Controller) SetCurrentTerm(ctx *gin.Context) {
	id := parseInt64(ctx.Param("id"))
	if id <= 0 {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "学期 ID 不合法")
		return
	}

	vo, err := c.svc.SetCurrentTerm(ctx.Request.Context(), id)
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusOK, vo)
}

// CreateCourse 处理 POST /api/v1/admin/courses。
func (c *Controller) CreateCourse(ctx *gin.Context) {
	var req CreateCourseRequest
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	vo, err := c.svc.CreateCourse(ctx.Request.Context(), req)
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusCreated, vo)
}

// CreateOffering 处理 POST /api/v1/admin/offerings。
func (c *Controller) CreateOffering(ctx *gin.Context) {
	var req CreateOfferingRequest
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	vo, err := c.svc.CreateOffering(ctx.Request.Context(), req)
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
		// 越权访问班次也走这里：不告诉调用方"存在但你没权限"
		respondError(ctx, http.StatusNotFound, "offering_not_found", "班次不存在")
	case errors.Is(err, ErrTermOrCourseMissing):
		respondError(ctx, http.StatusBadRequest, "term_or_course_missing", "学期或课程不存在")
	case errors.Is(err, ErrTermNotFound):
		respondError(ctx, http.StatusBadRequest, "term_not_found", "学期不存在")
	case errors.Is(err, ErrTeacherMissing):
		respondError(ctx, http.StatusBadRequest, "teacher_missing", "该用户不存在或未被授予教师角色")
	case errors.Is(err, ErrSectionTaken):
		respondError(ctx, http.StatusConflict, "section_taken", "该学期下这个班号已存在")
	case errors.Is(err, ErrTermCodeTaken):
		respondError(ctx, http.StatusConflict, "term_code_taken", "学期码已存在")
	case errors.Is(err, ErrCourseCodeTaken):
		respondError(ctx, http.StatusConflict, "course_code_taken", "课程号已存在")
	case errors.Is(err, ErrInvalidDate):
		respondError(ctx, http.StatusBadRequest, "invalid_date", err.Error())
	case errors.Is(err, ErrInvalidSlot):
		respondError(ctx, http.StatusBadRequest, "invalid_slot", err.Error())
	default:
		zap.L().Error("课程域未预期错误", zap.Error(err))
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

// parseInt64 宽松解析路径/查询参数：非法值当作 0 处理，由调用方判空。
func parseInt64(s string) int64 {
	v, err := strconv.ParseInt(s, 10, 64)
	if err != nil {
		return 0
	}
	return v
}
