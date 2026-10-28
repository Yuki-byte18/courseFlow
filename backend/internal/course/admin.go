package course

// 本文件是课程域的「编辑」与「导出」用例。
//
// 分离出来的理由：controller.go / service.go 已经很长，
// 而这两块和"建课、开班"是不同性质的操作（一个改状态，一个产出文件）。

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"strings"

	"github.com/gin-gonic/gin"
	"go.uber.org/zap"

	"courseflow/internal/platform/auth"
	"courseflow/internal/platform/middleware"
)

// 编辑相关的业务错误。
var (
	ErrCourseNotFound   = errors.New("课程不存在")
	ErrCapacityTooSmall = errors.New("容量不能小于已选人数")
)

// UpdateCourse 局部更新课程（管理员）。
func (s *Service) UpdateCourse(ctx context.Context, courseID int64, req UpdateCourseRequest) (*CourseVO, error) {
	exists, err := s.dao.CourseExists(ctx, courseID)
	if err != nil {
		return nil, err
	}
	if !exists {
		return nil, ErrCourseNotFound
	}

	fields := make(map[string]any, 5)
	if req.Name != nil {
		fields["name"] = strings.TrimSpace(*req.Name)
	}
	if req.Credits != nil {
		fields["credits"] = *req.Credits
	}
	if req.Department != nil {
		fields["department"] = strings.TrimSpace(*req.Department)
	}
	if req.Description != nil {
		fields["description"] = strings.TrimSpace(*req.Description)
	}
	if req.Status != nil {
		fields["status"] = *req.Status
	}

	if err := s.dao.UpdateCourseFields(ctx, courseID, fields); err != nil {
		return nil, err
	}

	course, err := s.dao.CourseByID(ctx, courseID)
	if err != nil {
		return nil, err
	}
	if course == nil {
		return nil, ErrCourseNotFound
	}

	s.log.Info("更新课程", zap.Int64("course_id", courseID), zap.Int("field_count", len(fields)))

	return &CourseVO{
		ID:          course.ID,
		Code:        course.Code,
		Name:        course.Name,
		Credits:     course.Credits,
		Department:  course.Department,
		Description: course.Description,
	}, nil
}

// UpdateOffering 局部更新班次（管理员）。
//
// 一条必须守住的不变量：**容量不能改到比已选人数还小**。
// 否则 taken > capacity，数据库的 CHECK 约束会直接拒绝，
// 与其让 MySQL 报一个看不懂的 3819，不如在这里给出人话。
func (s *Service) UpdateOffering(
	ctx context.Context,
	offeringID int64,
	req UpdateOfferingRequest,
) (*OfferingVO, error) {
	current, err := s.dao.OfferingByID(ctx, offeringID)
	if err != nil {
		return nil, err
	}
	if current == nil {
		return nil, ErrOfferingNotFound
	}

	fields := make(map[string]any, 3)
	if req.Capacity != nil {
		if *req.Capacity < current.Taken {
			return nil, fmt.Errorf("%w：已选 %d 人，容量至少要是 %d",
				ErrCapacityTooSmall, current.Taken, current.Taken)
		}
		fields["capacity"] = *req.Capacity
	}
	if req.Location != nil {
		fields["location"] = strings.TrimSpace(*req.Location)
	}
	if req.Status != nil {
		fields["status"] = *req.Status
	}

	// Slots 非 nil 表示要整体替换
	replaceSlots := req.Slots != nil
	slots := make([]Slot, 0, len(req.Slots))
	if replaceSlots {
		for _, item := range req.Slots {
			if item.EndPeriod < item.StartPeriod {
				return nil, fmt.Errorf("%w: 结束节次不能早于起始节次", ErrInvalidSlot)
			}
			slots = append(slots, Slot{
				Weekday:     item.Weekday,
				StartPeriod: item.StartPeriod,
				EndPeriod:   item.EndPeriod,
			})
		}
	}

	if err := s.dao.UpdateOffering(ctx, offeringID, fields, slots, replaceSlots); err != nil {
		return nil, err
	}

	s.log.Info("更新班次",
		zap.Int64("offering_id", offeringID),
		zap.Int("field_count", len(fields)),
		zap.Bool("slots_replaced", replaceSlots),
	)

	row, err := s.dao.OfferingByID(ctx, offeringID)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, ErrOfferingNotFound
	}

	slotMap, err := s.dao.SlotsFor(ctx, []int64{offeringID})
	if err != nil {
		return nil, err
	}

	vo := toOfferingVO(*row, slotMap[offeringID])
	return &vo, nil
}

// RosterCSV 生成选课名单的 CSV。
//
// 两个细节：
//   - 开头写 UTF-8 BOM：否则 Windows 的 Excel 会把中文显示成乱码
//   - 字段里的逗号和引号做转义：姓名里有逗号不会把列冲散
//
// 权限校验与 Roster 一致（教师只能导自己的班）。
func (s *Service) RosterCSV(ctx context.Context, offeringID int64, viewer *auth.User) (string, string, error) {
	roster, err := s.Roster(ctx, offeringID, viewer)
	if err != nil {
		return "", "", err
	}

	var b strings.Builder
	b.WriteString("\uFEFF") // UTF-8 BOM
	b.WriteString("学号,姓名,选课时间\n")

	for _, entry := range roster.Entries {
		b.WriteString(csvCell(entry.StudentNo))
		b.WriteString(",")
		b.WriteString(csvCell(entry.RealName))
		b.WriteString(",")
		b.WriteString(entry.EnrolledAt.Format("2006-01-02 15:04:05"))
		b.WriteString("\n")
	}

	filename := fmt.Sprintf("%s-%s班-名单.csv", roster.CourseName, roster.SectionNo)
	s.log.Info("导出名单",
		zap.Int64("offering_id", offeringID),
		zap.Int64("viewer_id", viewer.ID),
		zap.Int("rows", len(roster.Entries)),
	)

	return b.String(), filename, nil
}

// csvCell 转义一个 CSV 单元格。
func csvCell(value string) string {
	if !strings.ContainsAny(value, ",\"\n\r") {
		return value
	}
	return `"` + strings.ReplaceAll(value, `"`, `""`) + `"`
}

// UpdateCourse 处理 PATCH /api/v1/admin/courses/:id。
func (c *Controller) UpdateCourse(ctx *gin.Context) {
	id := parseInt64(ctx.Param("id"))
	if id <= 0 {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "课程 ID 不合法")
		return
	}

	var req UpdateCourseRequest
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	vo, err := c.svc.UpdateCourse(ctx.Request.Context(), id, req)
	if err != nil {
		if errors.Is(err, ErrCourseNotFound) {
			respondError(ctx, http.StatusNotFound, "course_not_found", "课程不存在")
			return
		}
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusOK, vo)
}

// UpdateOffering 处理 PATCH /api/v1/admin/offerings/:id。
func (c *Controller) UpdateOffering(ctx *gin.Context) {
	id := parseInt64(ctx.Param("id"))
	if id <= 0 {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "班次 ID 不合法")
		return
	}

	var req UpdateOfferingRequest
	if err := ctx.ShouldBindJSON(&req); err != nil {
		respondError(ctx, http.StatusBadRequest, "invalid_argument", "参数不合法: "+err.Error())
		return
	}

	vo, err := c.svc.UpdateOffering(ctx.Request.Context(), id, req)
	if err != nil {
		if errors.Is(err, ErrCapacityTooSmall) {
			respondError(ctx, http.StatusConflict, "capacity_too_small", err.Error())
			return
		}
		c.writeServiceError(ctx, err)
		return
	}
	ctx.JSON(http.StatusOK, vo)
}

// RosterExport 处理 GET /api/v1/offerings/:id/roster/export —— 下载 CSV。
func (c *Controller) RosterExport(ctx *gin.Context) {
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

	content, filename, err := c.svc.RosterCSV(ctx.Request.Context(), id, viewer)
	if err != nil {
		c.writeServiceError(ctx, err)
		return
	}

	// 中文文件名要用 RFC 5987 的 filename* 才能被浏览器正确解码
	ctx.Header("Content-Type", "text/csv; charset=utf-8")
	ctx.Header("Content-Disposition",
		`attachment; filename="roster.csv"; filename*=UTF-8''`+url.PathEscape(filename))
	ctx.String(http.StatusOK, content)
}
