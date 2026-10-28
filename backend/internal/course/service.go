package course

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"time"

	"go.uber.org/zap"
	"gorm.io/gorm"

	"courseflow/internal/identity"
	"courseflow/internal/platform/auth"
)

// 课程域的业务错误。
var (
	ErrOfferingNotFound    = errors.New("班次不存在")
	ErrTermOrCourseMissing = errors.New("学期或课程不存在")
	ErrTermNotFound        = errors.New("学期不存在")
	ErrTeacherMissing      = errors.New("教师不存在或未授予教师角色")
	ErrSectionTaken        = errors.New("该学期下这个班号已存在")
	ErrTermCodeTaken       = errors.New("学期码已存在")
	ErrCourseCodeTaken     = errors.New("课程号已存在")
	ErrInvalidDate         = errors.New("日期格式应为 YYYY-MM-DD")
	ErrInvalidSlot         = errors.New("上课时间段的节次不合法")
)

const dateLayout = "2006-01-02"

// Service 是课程域的业务逻辑层。
type Service struct {
	dao *DAO
	log *zap.Logger
}

// NewService 构造 Service。
func NewService(db *gorm.DB, log *zap.Logger) *Service {
	return &Service{dao: NewDAO(db), log: log}
}

// ---------------------------------------------------------------------------
// 读
// ---------------------------------------------------------------------------

// ListTerms 返回全部学期。
func (s *Service) ListTerms(ctx context.Context) ([]TermVO, error) {
	terms, err := s.dao.ListTerms(ctx)
	if err != nil {
		return nil, err
	}
	out := make([]TermVO, 0, len(terms))
	for _, t := range terms {
		out = append(out, TermVO{
			ID:        t.ID,
			Code:      t.Code,
			Name:      t.Name,
			StartsOn:  t.StartsOn,
			EndsOn:    t.EndsOn,
			IsCurrent: t.IsCurrent == 1,
		})
	}
	return out, nil
}

// ListCourses 列出课程主数据（管理端）。
func (s *Service) ListCourses(ctx context.Context, keyword string, limit int) ([]CourseVO, error) {
	if limit < 1 || limit > 500 {
		limit = 200
	}
	list, err := s.dao.ListCourses(ctx, keyword, limit)
	if err != nil {
		return nil, err
	}
	out := make([]CourseVO, 0, len(list))
	for _, c := range list {
		out = append(out, CourseVO{
			ID:          c.ID,
			Code:        c.Code,
			Name:        c.Name,
			Credits:     c.Credits,
			Department:  c.Department,
			Description: c.Description,
		})
	}
	return out, nil
}

// ListOfferings 分页查询班次，并补齐上课时间段。
//
// 时间段用一次 IN 查询批量取出，避免每个班次查一次（N+1）。
func (s *Service) ListOfferings(ctx context.Context, query OfferingQuery) (*OfferingPageVO, error) {
	query.Normalize()

	rows, total, err := s.dao.ListOfferings(ctx, query)
	if err != nil {
		return nil, err
	}

	ids := make([]int64, 0, len(rows))
	for _, r := range rows {
		ids = append(ids, r.ID)
	}
	slots, err := s.dao.SlotsFor(ctx, ids)
	if err != nil {
		return nil, err
	}

	items := make([]OfferingVO, 0, len(rows))
	for _, r := range rows {
		items = append(items, toOfferingVO(r, slots[r.ID]))
	}

	return &OfferingPageVO{
		Items:    items,
		Total:    total,
		Page:     query.Page,
		PageSize: query.PageSize,
	}, nil
}

// OfferingDetail 查班次详情。
func (s *Service) OfferingDetail(ctx context.Context, id int64) (*OfferingVO, error) {
	row, err := s.dao.OfferingByID(ctx, id)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, ErrOfferingNotFound
	}

	slots, err := s.dao.SlotsFor(ctx, []int64{id})
	if err != nil {
		return nil, err
	}

	vo := toOfferingVO(*row, slots[id])
	return &vo, nil
}

// Roster 查选课名单。
//
// 教师只能看自己开班的名单。越权时返回「班次不存在」而不是「无权」——
// 这是宪法里的约定：不泄露资源是否存在。
func (s *Service) Roster(ctx context.Context, offeringID int64, viewer *auth.User) (*RosterVO, error) {
	row, err := s.dao.OfferingByID(ctx, offeringID)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, ErrOfferingNotFound
	}
	if !hasRole(viewer, identity.RoleAdmin) && row.TeacherID != viewer.ID {
		return nil, ErrOfferingNotFound
	}

	entries, err := s.dao.Roster(ctx, offeringID)
	if err != nil {
		return nil, err
	}

	return &RosterVO{
		OfferingID: row.ID,
		CourseName: row.CourseName,
		SectionNo:  row.SectionNo,
		Capacity:   row.Capacity,
		Taken:      row.Taken,
		Entries:    entries,
	}, nil
}

// ---------------------------------------------------------------------------
// 写（管理员）
// ---------------------------------------------------------------------------

// CreateTerm 新建学期。
func (s *Service) CreateTerm(ctx context.Context, req CreateTermRequest) (*TermVO, error) {
	startsOn, err := time.Parse(dateLayout, req.StartsOn)
	if err != nil {
		return nil, fmt.Errorf("%w: starts_on", ErrInvalidDate)
	}
	endsOn, err := time.Parse(dateLayout, req.EndsOn)
	if err != nil {
		return nil, fmt.Errorf("%w: ends_on", ErrInvalidDate)
	}
	if !endsOn.After(startsOn) {
		return nil, fmt.Errorf("%w: 结束日期必须晚于开始日期", ErrInvalidDate)
	}

	term := &Term{
		Code:      strings.TrimSpace(req.Code),
		Name:      strings.TrimSpace(req.Name),
		StartsOn:  startsOn,
		EndsOn:    endsOn,
		IsCurrent: boolToInt8(req.IsCurrent),
	}
	if err := s.dao.CreateTerm(ctx, term); err != nil {
		if IsDuplicateKey(err) {
			return nil, ErrTermCodeTaken
		}
		return nil, err
	}

	s.log.Info("创建学期", zap.Int64("term_id", term.ID), zap.String("code", term.Code))
	return &TermVO{
		ID:        term.ID,
		Code:      term.Code,
		Name:      term.Name,
		StartsOn:  term.StartsOn,
		EndsOn:    term.EndsOn,
		IsCurrent: term.IsCurrent == 1,
	}, nil
}

// SetCurrentTerm 把某个学期设为当前学期。
//
// 全库只能有一个当前学期：前端的"当前学期"、选课轮的默认值、
// 学生选课时的学期判断都依赖它。
func (s *Service) SetCurrentTerm(ctx context.Context, termID int64) (*TermVO, error) {
	if err := s.dao.SetCurrentTerm(ctx, termID); err != nil {
		return nil, err
	}

	terms, err := s.dao.ListTerms(ctx)
	if err != nil {
		return nil, err
	}
	for _, t := range terms {
		if t.ID == termID {
			s.log.Info("切换当前学期", zap.Int64("term_id", t.ID), zap.String("code", t.Code))
			return &TermVO{
				ID:        t.ID,
				Code:      t.Code,
				Name:      t.Name,
				StartsOn:  t.StartsOn,
				EndsOn:    t.EndsOn,
				IsCurrent: true,
			}, nil
		}
	}
	return nil, ErrTermNotFound
}

// CreateCourse 新建课程主数据。
func (s *Service) CreateCourse(ctx context.Context, req CreateCourseRequest) (*CourseVO, error) {
	c := &Course{
		Code:        strings.TrimSpace(req.Code),
		Name:        strings.TrimSpace(req.Name),
		Credits:     req.Credits,
		Department:  strings.TrimSpace(req.Department),
		Description: strings.TrimSpace(req.Description),
		Status:      CourseStatusActive,
	}
	if err := s.dao.CreateCourse(ctx, c); err != nil {
		if IsDuplicateKey(err) {
			return nil, ErrCourseCodeTaken
		}
		return nil, err
	}

	s.log.Info("创建课程",
		zap.Int64("course_id", c.ID),
		zap.String("code", c.Code),
		zap.Float64("credits", c.Credits),
	)
	return &CourseVO{
		ID:          c.ID,
		Code:        c.Code,
		Name:        c.Name,
		Credits:     c.Credits,
		Department:  c.Department,
		Description: c.Description,
	}, nil
}

// CreateOffering 开班：把课程 × 教师 × 学期 × 容量组合成一个可选的班次。
func (s *Service) CreateOffering(ctx context.Context, req CreateOfferingRequest) (*OfferingVO, error) {
	ok, err := s.dao.TermAndCourseExist(ctx, req.TermID, req.CourseID)
	if err != nil {
		return nil, err
	}
	if !ok {
		return nil, ErrTermOrCourseMissing
	}

	ok, err = s.dao.TeacherExists(ctx, req.TeacherID)
	if err != nil {
		return nil, err
	}
	if !ok {
		return nil, ErrTeacherMissing
	}

	slots := make([]Slot, 0, len(req.Slots))
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

	sectionNo := strings.TrimSpace(req.SectionNo)
	if sectionNo == "" {
		sectionNo = "01"
	}

	offering := &Offering{
		TermID:    req.TermID,
		CourseID:  req.CourseID,
		TeacherID: req.TeacherID,
		SectionNo: sectionNo,
		Capacity:  req.Capacity,
		Taken:     0,
		Location:  strings.TrimSpace(req.Location),
		Status:    OfferingStatusOpen,
	}

	if err := s.dao.CreateOffering(ctx, offering, slots); err != nil {
		if IsDuplicateKey(err) {
			return nil, ErrSectionTaken
		}
		return nil, err
	}

	s.log.Info("开班成功",
		zap.Int64("offering_id", offering.ID),
		zap.Int("capacity", offering.Capacity),
		zap.Int("slot_count", len(slots)),
	)

	row, err := s.dao.OfferingByID(ctx, offering.ID)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, ErrOfferingNotFound
	}
	vo := toOfferingVO(*row, slots)
	return &vo, nil
}

// ---------------------------------------------------------------------------
// 教师端：我的班次 / 导入课表
// ---------------------------------------------------------------------------

// MyOfferings 返回某教师自己开设的班次。
//
// teacherID 由调用方从 token 取，不接受前端传参 ——
// 否则改个参数就能看到别人的班次。
func (s *Service) MyOfferings(
	ctx context.Context,
	teacherID int64,
	query OfferingQuery,
) (*OfferingPageVO, error) {
	query.TeacherID = teacherID
	return s.ListOfferings(ctx, query)
}

// ImportOfferings 教师批量导入自己的授课课表。
//
// 关键安全约束：每一行的 teacher_id 都被强制设为传入的 teacherID
// （来自 token），即使请求体里伪造 teacher_id 也不生效。
//
// 逐行处理而不是整体事务：教师导 40 行时，某一行的课程号写错不应该
// 让另外 39 行一起失败。所以返回逐行结果，让前端明确指出哪行有问题。
func (s *Service) ImportOfferings(
	ctx context.Context,
	teacherID int64,
	req ImportOfferingsRequest,
) (*ImportResultVO, error) {
	ok, err := s.dao.TermExists(ctx, req.TermID)
	if err != nil {
		return nil, err
	}
	if !ok {
		return nil, ErrTermNotFound
	}

	// 先收集去重后的课程号，一次查完全部 ID，避免每行一次查询
	seen := make(map[string]struct{}, len(req.Rows))
	codes := make([]string, 0, len(req.Rows))
	for _, row := range req.Rows {
		code := strings.TrimSpace(row.CourseCode)
		if code == "" {
			continue
		}
		if _, dup := seen[code]; dup {
			continue
		}
		seen[code] = struct{}{}
		codes = append(codes, code)
	}

	courseIDs, err := s.dao.CourseIDsByCodes(ctx, codes)
	if err != nil {
		return nil, err
	}

	result := &ImportResultVO{Results: make([]ImportRowResult, 0, len(req.Rows))}

	for index, row := range req.Rows {
		out := ImportRowResult{Index: index, Status: ImportStatusFailed}
		code := strings.TrimSpace(row.CourseCode)

		courseID, found := courseIDs[code]
		if !found {
			out.Message = fmt.Sprintf("课程号 %q 不存在", code)
			result.Failed++
			result.Results = append(result.Results, out)
			continue
		}

		// 时间段整体选填：三个字段都给了才建
		slots := make([]Slot, 0, 1)
		if row.Weekday > 0 && row.StartPeriod > 0 && row.EndPeriod > 0 {
			if row.EndPeriod < row.StartPeriod {
				out.Message = "结束节次不能早于起始节次"
				result.Failed++
				result.Results = append(result.Results, out)
				continue
			}
			slots = append(slots, Slot{
				Weekday:     row.Weekday,
				StartPeriod: row.StartPeriod,
				EndPeriod:   row.EndPeriod,
			})
		}

		sectionNo := strings.TrimSpace(row.SectionNo)
		if sectionNo == "" {
			sectionNo = "01"
		}

		offering := &Offering{
			TermID:    req.TermID,
			CourseID:  courseID,
			TeacherID: teacherID, // 强制为当前登录用户
			SectionNo: sectionNo,
			Capacity:  row.Capacity,
			Location:  strings.TrimSpace(row.Location),
			Status:    OfferingStatusOpen,
		}

		if err := s.dao.CreateOffering(ctx, offering, slots); err != nil {
			if IsDuplicateKey(err) {
				out.Status = ImportStatusSkipped
				out.Message = "该班次已存在，已跳过"
				result.Skipped++
			} else {
				out.Message = err.Error()
				result.Failed++
			}
			result.Results = append(result.Results, out)
			continue
		}

		out.Status = ImportStatusCreated
		out.OfferingID = offering.ID
		result.Created++
		result.Results = append(result.Results, out)
	}

	s.log.Info("教师导入课表",
		zap.Int64("teacher_id", teacherID),
		zap.Int64("term_id", req.TermID),
		zap.Int("created", result.Created),
		zap.Int("skipped", result.Skipped),
		zap.Int("failed", result.Failed),
	)

	return result, nil
}

// ---------------------------------------------------------------------------
// 内部工具
// ---------------------------------------------------------------------------

func toOfferingVO(row OfferingRow, slots []Slot) OfferingVO {
	remaining := row.Capacity - row.Taken
	if remaining < 0 {
		remaining = 0
	}
	if slots == nil {
		slots = []Slot{}
	}
	return OfferingVO{
		ID:          row.ID,
		TermID:      row.TermID,
		CourseID:    row.CourseID,
		CourseCode:  row.CourseCode,
		CourseName:  row.CourseName,
		Credits:     row.Credits,
		Department:  row.Department,
		TeacherID:   row.TeacherID,
		TeacherName: row.TeacherName,
		SectionNo:   row.SectionNo,
		Capacity:    row.Capacity,
		Taken:       row.Taken,
		Remaining:   remaining,
		Location:    row.Location,
		Slots:       slots,
	}
}

func boolToInt8(v bool) int8 {
	if v {
		return 1
	}
	return 0
}

func hasRole(u *auth.User, role string) bool {
	if u == nil {
		return false
	}
	for _, r := range u.Roles {
		if r == role {
			return true
		}
	}
	return false
}
