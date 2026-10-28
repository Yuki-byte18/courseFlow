package identity

// 本文件是身份域的「账号管理」扩展：
// 列表、编辑（姓名 / 邮箱 / 年级 / 启用禁用）。
//
// 单独一个文件而不是往 controller.go / dao.go 里塞：
// 这是身份域里一块独立的用例，集中在一处更容易读。

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"

	"courseflow/internal/platform/middleware"
)

// 账号相关的业务错误。
var (
	ErrUserNotFound    = errors.New("账号不存在")
	ErrCannotDisableMe = errors.New("不能禁用自己的账号")
)

// UserAdminVO 是账号管理页的一行。
type UserAdminVO struct {
	UserID    int64     `json:"user_id"`
	Username  string    `json:"username"`
	RealName  string    `json:"real_name"`
	Email     string    `json:"email"`
	Status    int8      `json:"status"`
	Grade     int       `json:"grade"`
	Roles     []string  `json:"roles"`
	CreatedAt time.Time `json:"created_at"`
}

// ListUsersQuery 是账号列表的查询条件。
type ListUsersQuery struct {
	// Role 按角色码过滤，空串表示不过滤
	Role string
	// Keyword 匹配用户名或姓名
	Keyword  string
	Page     int
	PageSize int
}

// Normalize 把分页参数收敛到合法范围。
func (q *ListUsersQuery) Normalize() {
	if q.Page < 1 {
		q.Page = 1
	}
	if q.PageSize < 1 {
		q.PageSize = 20
	}
	if q.PageSize > 100 {
		q.PageSize = 100
	}
}

// ListUsersResult 是账号列表的分页结果。
type ListUsersResult struct {
	Items    []UserAdminVO `json:"items"`
	Total    int64         `json:"total"`
	Page     int           `json:"page"`
	PageSize int           `json:"page_size"`
}

// UpdateUserRequest 是编辑账号的入参。
//
// 字段全部用指针：只有传了的字段才会被更新。
// 用零值判断"要不要更新"是不行的 —— 0 和"没传"无法区分，
// 而把年级从 3 改成 0（未设置）是一个合法操作。
type UpdateUserRequest struct {
	RealName *string `json:"real_name" binding:"omitempty,max=64"`
	Email    *string `json:"email" binding:"omitempty,max=128"`
	Status   *int8   `json:"status" binding:"omitempty,oneof=1 2"`
	Grade    *int    `json:"grade" binding:"omitempty,min=0,max=5"`
}

// ListUsers 分页查询账号，附带各自的角色码。
func (d *DAO) ListUsers(ctx context.Context, q ListUsersQuery) ([]UserAdminVO, int64, error) {
	where := []string{"1 = 1"}
	args := []any{}

	if q.Role != "" {
		where = append(where, `EXISTS (
			SELECT 1 FROM user_roles ur2
			  JOIN roles r2 ON r2.id = ur2.role_id
			 WHERE ur2.user_id = u.id AND r2.code = ?
		)`)
		args = append(args, q.Role)
	}
	if q.Keyword != "" {
		where = append(where, "(u.username LIKE ? OR u.real_name LIKE ?)")
		like := "%" + q.Keyword + "%"
		args = append(args, like, like)
	}

	cond := strings.Join(where, " AND ")

	var total int64
	countSQL := `SELECT COUNT(*) FROM users u WHERE ` + cond
	if err := d.db.WithContext(ctx).Raw(countSQL, args...).Scan(&total).Error; err != nil {
		return nil, 0, fmt.Errorf("统计账号失败: %w", err)
	}
	if total == 0 {
		return []UserAdminVO{}, 0, nil
	}

	// email 列可空，用 COALESCE 兜一下，否则扫描 NULL 到 string 会报错
	listSQL := `
		SELECT u.id AS user_id, u.username, u.real_name,
		       COALESCE(u.email, '') AS email,
		       u.status, u.grade, u.created_at,
		       COALESCE(GROUP_CONCAT(r.code ORDER BY r.code SEPARATOR ','), '') AS roles_csv
		  FROM users u
		  LEFT JOIN user_roles ur ON ur.user_id = u.id
		  LEFT JOIN roles r      ON r.id = ur.role_id
		 WHERE ` + cond + `
		 GROUP BY u.id
		 ORDER BY u.id DESC
		 LIMIT ? OFFSET ?`

	args = append(args, q.PageSize, (q.Page-1)*q.PageSize)

	var rows []struct {
		UserID    int64     `gorm:"column:user_id"`
		Username  string    `gorm:"column:username"`
		RealName  string    `gorm:"column:real_name"`
		Email     string    `gorm:"column:email"`
		Status    int8      `gorm:"column:status"`
		Grade     int       `gorm:"column:grade"`
		CreatedAt time.Time `gorm:"column:created_at"`
		RolesCSV  string    `gorm:"column:roles_csv"`
	}
	if err := d.db.WithContext(ctx).Raw(listSQL, args...).Scan(&rows).Error; err != nil {
		return nil, 0, fmt.Errorf("查询账号失败: %w", err)
	}

	items := make([]UserAdminVO, 0, len(rows))
	for _, r := range rows {
		roles := []string{}
		if r.RolesCSV != "" {
			roles = strings.Split(r.RolesCSV, ",")
		}
		items = append(items, UserAdminVO{
			UserID:    r.UserID,
			Username:  r.Username,
			RealName:  r.RealName,
			Email:     r.Email,
			Status:    r.Status,
			Grade:     r.Grade,
			Roles:     roles,
			CreatedAt: r.CreatedAt,
		})
	}
	return items, total, nil
}

// UserExists 判断账号是否存在。
func (d *DAO) UserExists(ctx context.Context, userID int64) (bool, error) {
	var ok int
	if err := d.db.WithContext(ctx).Raw(
		`SELECT EXISTS(SELECT 1 FROM users WHERE id = ?)`, userID).Scan(&ok).Error; err != nil {
		return false, fmt.Errorf("校验账号失败: %w", err)
	}
	return ok == 1, nil
}

// UpdateUserFields 局部更新账号。
//
// 先校验存在再更新：UPDATE 的 RowsAffected 返回"实际改变的行数"，
// 提交与原值相同的字段会得到 0，从而被误判成"账号不存在"。
func (d *DAO) UpdateUserFields(ctx context.Context, userID int64, fields map[string]any) error {
	if len(fields) == 0 {
		return nil
	}
	if err := d.db.WithContext(ctx).Model(&User{}).
		Where("id = ?", userID).
		Updates(fields).Error; err != nil {
		return fmt.Errorf("更新账号失败: %w", err)
	}
	return nil
}

// GetUserAdmin 按 ID 查一个账号的管理视图。
func (d *DAO) GetUserAdmin(ctx context.Context, userID int64) (*UserAdminVO, error) {
	var row struct {
		UserID   int64     `gorm:"column:user_id"`
		Username string    `gorm:"column:username"`
		RealName string    `gorm:"column:real_name"`
		Email    string    `gorm:"column:email"`
		Status   int8      `gorm:"column:status"`
		Grade    int       `gorm:"column:grade"`
		RolesCSV string    `gorm:"column:roles_csv"`
		Created  time.Time `gorm:"column:created_at"`
	}

	err := d.db.WithContext(ctx).Raw(`
		SELECT u.id AS user_id, u.username, u.real_name,
		       COALESCE(u.email, '') AS email,
		       u.status, u.grade, u.created_at,
		       COALESCE(GROUP_CONCAT(r.code ORDER BY r.code SEPARATOR ','), '') AS roles_csv
		  FROM users u
		  LEFT JOIN user_roles ur ON ur.user_id = u.id
		  LEFT JOIN roles r      ON r.id = ur.role_id
		 WHERE u.id = ?
		 GROUP BY u.id`, userID).Scan(&row).Error
	if err != nil {
		return nil, fmt.Errorf("查询账号失败: %w", err)
	}
	if row.UserID == 0 {
		return nil, nil
	}

	roles := []string{}
	if row.RolesCSV != "" {
		roles = strings.Split(row.RolesCSV, ",")
	}
	return &UserAdminVO{
		UserID:    row.UserID,
		Username:  row.Username,
		RealName:  row.RealName,
		Email:     row.Email,
		Status:    row.Status,
		Grade:     row.Grade,
		Roles:     roles,
		CreatedAt: row.Created,
	}, nil
}

// ListUsers 是 service 层的账号列表用例。
func (s *Service) ListUsers(ctx context.Context, q ListUsersQuery) (*ListUsersResult, error) {
	q.Normalize()
	q.Role = strings.TrimSpace(q.Role)
	q.Keyword = strings.TrimSpace(q.Keyword)

	items, total, err := s.dao.ListUsers(ctx, q)
	if err != nil {
		return nil, err
	}
	return &ListUsersResult{
		Items:    items,
		Total:    total,
		Page:     q.Page,
		PageSize: q.PageSize,
	}, nil
}

// UpdateUser 局部更新账号（管理员）。
//
// operatorID 用于拦住"把自己禁用"这种自伤操作 ——
// 管理员一禁用自己就再也登不进来，只能改库恢复。
func (s *Service) UpdateUser(
	ctx context.Context,
	userID, operatorID int64,
	req UpdateUserRequest,
) (*UserAdminVO, error) {
	exists, err := s.dao.UserExists(ctx, userID)
	if err != nil {
		return nil, err
	}
	if !exists {
		return nil, ErrUserNotFound
	}

	fields := make(map[string]any, 4)
	if req.RealName != nil {
		fields["real_name"] = strings.TrimSpace(*req.RealName)
	}
	if req.Email != nil {
		fields["email"] = strings.TrimSpace(*req.Email)
	}
	if req.Grade != nil {
		fields["grade"] = *req.Grade
	}
	if req.Status != nil {
		if *req.Status == UserStatusDisabled && userID == operatorID {
			return nil, ErrCannotDisableMe
		}
		fields["status"] = *req.Status
	}

	if err := s.dao.UpdateUserFields(ctx, userID, fields); err != nil {
		return nil, err
	}

	s.log.Info("更新账号",
		zap.Int64("user_id", userID),
		zap.Int64("operator_id", operatorID),
		zap.Int("field_count", len(fields)),
	)

	return s.dao.GetUserAdmin(ctx, userID)
}

// AdminListUsers 处理 GET /api/v1/admin/users。
func (c *Controller) AdminListUsers(ctx *gin.Context) {
	result, err := c.svc.ListUsers(ctx.Request.Context(), ListUsersQuery{
		Role:     ctx.Query("role"),
		Keyword:  ctx.Query("keyword"),
		Page:     atoiQuery(ctx.Query("page")),
		PageSize: atoiQuery(ctx.Query("page_size")),
	})
	if err != nil {
		zap.L().Error("查询账号列表失败", zap.Error(err))
		respondError(ctx, http.StatusInternalServerError, "internal_error", "服务器内部错误")
		return
	}
	ctx.JSON(http.StatusOK, result)
}

// AdminUpdateUser 处理 PATCH /api/v1/admin/users/:id。
func (c *Controller) AdminUpdateUser(ctx *gin.Context) {
	operator, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	userID := parseID(ctx.Param("id"))
	if userID <= 0 {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "账号 ID 不合法")
		return
	}

	var req UpdateUserRequest
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	vo, err := c.svc.UpdateUser(ctx.Request.Context(), userID, operator.ID, req)
	if err != nil {
		switch {
		case errors.Is(err, ErrUserNotFound):
			respondError(ctx, http.StatusNotFound, "user_not_found", "账号不存在")
		case errors.Is(err, ErrCannotDisableMe):
			respondError(ctx, http.StatusConflict, "cannot_disable_self", "不能禁用自己的账号")
		default:
			zap.L().Error("更新账号失败", zap.Error(err))
			respondError(ctx, http.StatusInternalServerError, "internal_error", "服务器内部错误")
		}
		return
	}

	ctx.JSON(http.StatusOK, vo)
}

// atoiQuery 宽松解析查询参数：非法值当作 0，由 Normalize 收敛默认值。
func atoiQuery(s string) int {
	n := 0
	for _, ch := range s {
		if ch < '0' || ch > '9' {
			return 0
		}
		n = n*10 + int(ch-'0')
		if n > 1_000_000 {
			return 0
		}
	}
	return n
}

// parseID 解析路径里的 ID：非法或非正数一律当作 0，由调用方拒绝。
func parseID(s string) int64 {
	n := int64(0)
	for _, ch := range s {
		if ch < '0' || ch > '9' {
			return 0
		}
		n = n*10 + int64(ch-'0')
		if n > 1<<62 {
			return 0
		}
	}
	return n
}
