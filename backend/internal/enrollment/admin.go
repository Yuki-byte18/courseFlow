package enrollment

// 本文件是选课域的「教务应急」与「轮次编辑」用例。
//
// 三块内容：
//   - 轮次局部编辑（改时间窗、学分上限、年级、开关）
//   - 强制退课（学生自己退不了时的兜底）
//   - 补录（学生错过了选课窗口，教务代为选上）
//
// 三者的共同点：都是**特权操作**，需要独立权限码，
// 而且都要在日志里留下"谁替谁做了什么"。

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"

	"courseflow/internal/platform/auth"
	"courseflow/internal/platform/middleware"
)

// 编辑 / 应急操作的业务错误。
var (
	ErrTimeConflict    = errors.New("与已选课程时间冲突")
	ErrGradeRestricted = errors.New("本轮选课不面向你的年级")
	ErrRoundNotFound   = errors.New("轮次不存在")
	ErrStudentNotFound = errors.New("学生账号不存在")
	ErrNoRoundForTerm  = errors.New("该学期还没有任何选课轮次，无法补录")
)

// UpdateRoundRequest 是编辑轮次的入参。字段用指针表示"传了才改"。
type UpdateRoundRequest struct {
	Name       *string  `json:"name" binding:"omitempty,min=1,max=64"`
	OpensAt    *string  `json:"opens_at" binding:"omitempty"`
	ClosesAt   *string  `json:"closes_at" binding:"omitempty"`
	MaxCredits *float64 `json:"max_credits" binding:"omitempty,gt=0,lte=60"`
	AllowGrade *int     `json:"allow_grade" binding:"omitempty,min=0,max=5"`
	Status     *int8    `json:"status" binding:"omitempty,oneof=1 2"`
}

// StudentOfferingRequest 是「针对某个学生的某个班次」这类操作的入参。
//
// 用**学号（账号名）**而不是 user_id：教务手里有学号，没有数据库主键。
// 让使用者去查 user_id 是把内部实现泄漏到了界面上。
type StudentOfferingRequest struct {
	StudentNo  string `json:"student_no" binding:"required,min=1,max=64"`
	OfferingID int64  `json:"offering_id" binding:"required,gt=0"`
}

// ---------------------------------------------------------------------------
// DAO
// ---------------------------------------------------------------------------

// RoundExists 判断轮次是否存在。
func (d *DAO) RoundExists(ctx context.Context, roundID int64) (bool, error) {
	var ok int
	if err := d.db.WithContext(ctx).Raw(
		`SELECT EXISTS(SELECT 1 FROM rounds WHERE id = ?)`, roundID).Scan(&ok).Error; err != nil {
		return false, fmt.Errorf("校验轮次失败: %w", err)
	}
	return ok == 1, nil
}

// RoundByID 按 ID 查轮次。查不到返回 (nil, nil)。
func (d *DAO) RoundByID(ctx context.Context, roundID int64) (*Round, error) {
	var rows []Round
	if err := d.db.WithContext(ctx).Raw(`
		SELECT id, term_id, name, opens_at, closes_at, max_credits, allow_grade, status, created_at
		  FROM rounds WHERE id = ?`, roundID).Scan(&rows).Error; err != nil {
		return nil, fmt.Errorf("查询轮次失败: %w", err)
	}
	if len(rows) == 0 {
		return nil, nil
	}
	return &rows[0], nil
}

// LatestRound 返回某学期最近的一个轮次（不要求此刻开放）。
//
// 补录用它：补录往往发生在窗口之外，所以不能要求"当前开放中"。
func (d *DAO) LatestRound(ctx context.Context, termID int64) (*Round, error) {
	var rows []Round
	if err := d.db.WithContext(ctx).Raw(`
		SELECT id, term_id, name, opens_at, closes_at, max_credits, allow_grade, status, created_at
		  FROM rounds
		 WHERE term_id = ?
		 ORDER BY opens_at DESC
		 LIMIT 1`, termID).Scan(&rows).Error; err != nil {
		return nil, fmt.Errorf("查询轮次失败: %w", err)
	}
	if len(rows) == 0 {
		return nil, nil
	}
	return &rows[0], nil
}

// UpdateRoundFields 局部更新轮次。
func (d *DAO) UpdateRoundFields(ctx context.Context, roundID int64, fields map[string]any) error {
	if len(fields) == 0 {
		return nil
	}
	if err := d.db.WithContext(ctx).Model(&Round{}).
		Where("id = ?", roundID).
		Updates(fields).Error; err != nil {
		return fmt.Errorf("更新轮次失败: %w", err)
	}
	return nil
}

// StudentExists 判断学生账号是否存在。
func (d *DAO) StudentExists(ctx context.Context, studentID int64) (bool, error) {
	var ok int
	if err := d.db.WithContext(ctx).Raw(
		`SELECT EXISTS(SELECT 1 FROM users WHERE id = ?)`, studentID).Scan(&ok).Error; err != nil {
		return false, fmt.Errorf("校验学生账号失败: %w", err)
	}
	return ok == 1, nil
}

// UserIDByUsername 按账号名查用户 ID。查不到返回 0。
func (d *DAO) UserIDByUsername(ctx context.Context, username string) (int64, error) {
	var id int64
	if err := d.db.WithContext(ctx).Raw(
		`SELECT id FROM users WHERE username = ?`, username).Scan(&id).Error; err != nil {
		return 0, fmt.Errorf("查询账号失败: %w", err)
	}
	return id, nil
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

// UpdateRound 局部更新轮次（管理员）。
//
// 时间窗改完之后仍必须满足 closes_at > opens_at ——
// 单独改一头很容易造出"结束早于开始"的轮次，数据库的 CHECK 会拒绝，
// 但那时报错信息对使用者没有意义，所以在这里先算一遍再给结论。
func (s *Service) UpdateRound(
	ctx context.Context,
	roundID int64,
	req UpdateRoundRequest,
) (*RoundVO, error) {
	current, err := s.dao.RoundByID(ctx, roundID)
	if err != nil {
		return nil, err
	}
	if current == nil {
		return nil, ErrRoundNotFound
	}

	opensAt := current.OpensAt
	closesAt := current.ClosesAt

	if req.OpensAt != nil {
		parsed, err := parseDateTime(*req.OpensAt)
		if err != nil {
			return nil, err
		}
		opensAt = parsed
	}
	if req.ClosesAt != nil {
		parsed, err := parseDateTime(*req.ClosesAt)
		if err != nil {
			return nil, err
		}
		closesAt = parsed
	}
	if !closesAt.After(opensAt) {
		return nil, fmt.Errorf("%w: 结束时间必须晚于开始时间", ErrInvalidTime)
	}

	fields := make(map[string]any, 6)
	if req.Name != nil {
		fields["name"] = strings.TrimSpace(*req.Name)
	}
	if req.OpensAt != nil {
		fields["opens_at"] = opensAt
	}
	if req.ClosesAt != nil {
		fields["closes_at"] = closesAt
	}
	if req.MaxCredits != nil {
		fields["max_credits"] = *req.MaxCredits
	}
	if req.AllowGrade != nil {
		fields["allow_grade"] = *req.AllowGrade
	}
	if req.Status != nil {
		fields["status"] = *req.Status
	}

	if err := s.dao.UpdateRoundFields(ctx, roundID, fields); err != nil {
		return nil, err
	}

	s.log.Info("更新选课轮次", zap.Int64("round_id", roundID), zap.Int("field_count", len(fields)))

	updated, err := s.dao.RoundByID(ctx, roundID)
	if err != nil {
		return nil, err
	}
	if updated == nil {
		return nil, ErrRoundNotFound
	}

	return &RoundVO{
		RoundID:    updated.ID,
		TermID:     updated.TermID,
		Name:       updated.Name,
		OpensAt:    updated.OpensAt,
		ClosesAt:   updated.ClosesAt,
		MaxCredits: updated.MaxCredits,
		Open:       updated.IsOpenAt(updated.OpensAt),
	}, nil
}

// ForceDrop 强制退课（管理员）。
//
// 与学生的自助退课走完全相同的 DAO：把记录置为已退、把名额还回去。
// 区别只在权限码和审计日志 —— 这里记 Warn 级别，因为它是一次"人的干预"。
func (s *Service) ForceDrop(ctx context.Context, operator *auth.User, studentNo string, offeringID int64) error {
	studentID, err := s.resolveStudent(ctx, studentNo)
	if err != nil {
		return err
	}

	if err := s.dao.Drop(ctx, studentID, offeringID); err != nil {
		return err
	}
	s.log.Warn("管理员强制退课",
		zap.Int64("operator_id", operator.ID),
		zap.String("student_no", studentNo),
		zap.Int64("student_id", studentID),
		zap.Int64("offering_id", offeringID),
	)
	return nil
}

// Supplement 补录（管理员）。
//
// 绕过三件事：轮次时间窗、学分上限、年级限制 —— 补录本身就是给特殊情况开例外。
// **绝不绕过**：容量（不能超卖）、时间冲突（不能造出上不了的课表）、唯一性（不能重复选）。
func (s *Service) Supplement(
	ctx context.Context,
	operator *auth.User,
	studentNo string,
	offeringID int64,
) (*EnrollVO, error) {
	studentID, err := s.resolveStudent(ctx, studentNo)
	if err != nil {
		return nil, err
	}

	termID, err := s.dao.TermOfOffering(ctx, offeringID)
	if err != nil {
		return nil, err
	}
	if termID == 0 {
		return nil, ErrOfferingNotFound
	}

	round, err := s.dao.LatestRound(ctx, termID)
	if err != nil {
		return nil, err
	}
	if round == nil {
		return nil, ErrNoRoundForTerm
	}

	res, err := s.dao.Enroll(ctx, EnrollParams{
		StudentID:    studentID,
		OfferingID:   offeringID,
		RoundID:      round.ID,
		MaxCredits:   round.MaxCredits,
		AllowGrade:   round.AllowGrade,
		BypassLimits: true,
	})
	if err != nil {
		return nil, err
	}

	remaining := res.Capacity - res.Taken
	if remaining < 0 {
		remaining = 0
	}

	s.log.Warn("管理员补录",
		zap.Int64("operator_id", operator.ID),
		zap.String("student_no", studentNo),
		zap.Int64("student_id", studentID),
		zap.Int64("offering_id", offeringID),
		zap.Int64("round_id", round.ID),
	)

	return &EnrollVO{
		EnrollmentID: res.EnrollmentID,
		OfferingID:   res.OfferingID,
		CourseName:   res.CourseName,
		Credits:      res.Credits,
		Capacity:     res.Capacity,
		Taken:        res.Taken,
		Remaining:    remaining,
	}, nil
}

// resolveStudent 把学号解析成 user_id。
//
// 两个用途：强制退课、补录。抽出来是因为两处都要报同一个错。
func (s *Service) resolveStudent(ctx context.Context, studentNo string) (int64, error) {
	studentID, err := s.dao.UserIDByUsername(ctx, strings.TrimSpace(studentNo))
	if err != nil {
		return 0, err
	}
	if studentID == 0 {
		return 0, ErrStudentNotFound
	}
	return studentID, nil
}

// ---------------------------------------------------------------------------
// Controller
// ---------------------------------------------------------------------------

// UpdateRound 处理 PATCH /api/v1/admin/rounds/:id。
func (c *Controller) UpdateRound(ctx *gin.Context) {
	id := parseInt64(ctx.Param("id"))
	if id <= 0 {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "轮次 ID 不合法")
		return
	}

	var req UpdateRoundRequest
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	vo, err := c.svc.UpdateRound(ctx.Request.Context(), id, req)
	if err != nil {
		if errors.Is(err, ErrRoundNotFound) {
			respondError(ctx, http.StatusNotFound, "round_not_found", "轮次不存在")
			return
		}
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusOK, vo)
}

// ForceDrop 处理 POST /api/v1/admin/enrollments/force-drop。
func (c *Controller) ForceDrop(ctx *gin.Context) {
	operator, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	var req StudentOfferingRequest
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	if err := c.svc.ForceDrop(ctx.Request.Context(), operator, req.StudentNo, req.OfferingID); err != nil {
		c.writeServiceError(ctx, err)
		return
	}
	ctx.Status(http.StatusNoContent)
}

// Supplement 处理 POST /api/v1/admin/enrollments/supplement。
func (c *Controller) Supplement(ctx *gin.Context) {
	operator, ok := middleware.CurrentUser(ctx)
	if !ok {
		respondError(ctx, http.StatusUnauthorized, "unauthorized", "认证失败，请重新登录")
		return
	}

	var req StudentOfferingRequest
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	vo, err := c.svc.Supplement(ctx.Request.Context(), operator, req.StudentNo, req.OfferingID)
	if err != nil {
		if errors.Is(err, ErrStudentNotFound) {
			respondError(ctx, http.StatusNotFound, "student_not_found", "学生账号不存在")
			return
		}
		if errors.Is(err, ErrNoRoundForTerm) {
			respondError(ctx, http.StatusConflict, "no_round", err.Error())
			return
		}
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusCreated, vo)
}
