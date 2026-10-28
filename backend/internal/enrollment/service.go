package enrollment

import (
	"context"
	"errors"
	"fmt"
	"time"

	"go.uber.org/zap"
	"gorm.io/gorm"

	"courseflow/internal/platform/auth"
)

// 选课域的业务错误。
var (
	ErrOfferingNotFound = errors.New("班次不存在")
	ErrOfferingNotOpen  = errors.New("该班次已停开")
	ErrOfferingFull     = errors.New("名额已满")
	ErrAlreadyEnrolled  = errors.New("你已经选过这门课了")
	ErrNotEnrolled      = errors.New("你没有选这门课")
	ErrCreditLimit      = errors.New("超出本轮学分上限")
	ErrRoundClosed      = errors.New("当前没有开放中的选课轮次")
	ErrRoundNotStarted  = errors.New("选课尚未开始")
	ErrTermNotFound     = errors.New("学期不存在")
	ErrInvalidTime      = errors.New("时间格式应为 RFC3339 或 YYYY-MM-DD HH:MM:SS")
)

// Service 是选课域的业务逻辑层。
type Service struct {
	dao *DAO
	log *zap.Logger
}

// NewService 构造 Service。
func NewService(db *gorm.DB, log *zap.Logger) *Service {
	return &Service{dao: NewDAO(db), log: log}
}

// ---------------------------------------------------------------------------
// 轮次
// ---------------------------------------------------------------------------

// CurrentRound 返回当前学生所在学期的开放轮次与已用学分。
//
// 「现在能不能选课」由后端判定并返回 Reason，前端不自己比时间 ——
// 否则每台机器的时钟都会给出不同答案。
func (s *Service) CurrentRound(ctx context.Context, studentID int64) (*RoundVO, error) {
	now, err := s.dao.Now(ctx)
	if err != nil {
		return nil, err
	}

	termID, err := s.dao.CurrentTermID(ctx)
	if err != nil {
		return nil, err
	}
	if termID == 0 {
		return &RoundVO{Open: false, Reason: "还没有配置当前学期"}, nil
	}

	round, err := s.dao.RoundCovering(ctx, termID, now)
	if err != nil {
		return nil, err
	}

	if round == nil {
		next, err := s.dao.NextRound(ctx, termID, now)
		if err != nil {
			return nil, err
		}
		if next != nil {
			return &RoundVO{
				RoundID:    next.ID,
				TermID:     next.TermID,
				Name:       next.Name,
				OpensAt:    next.OpensAt,
				ClosesAt:   next.ClosesAt,
				MaxCredits: next.MaxCredits,
				Open:       false,
				Reason:     fmt.Sprintf("%s 将于 %s 开放", next.Name, next.OpensAt.Format("01-02 15:04")),
			}, nil
		}
		return &RoundVO{
			TermID: termID,
			Open:   false,
			Reason: "当前学期没有开放中的选课轮次",
		}, nil
	}

	used, err := s.dao.UsedCredits(ctx, studentID, termID)
	if err != nil {
		return nil, err
	}

	return &RoundVO{
		RoundID:     round.ID,
		TermID:      round.TermID,
		Name:        round.Name,
		OpensAt:     round.OpensAt,
		ClosesAt:    round.ClosesAt,
		MaxCredits:  round.MaxCredits,
		UsedCredits: used,
		Open:        true,
	}, nil
}

// CreateRound 新建选课轮次（管理员）。
func (s *Service) CreateRound(ctx context.Context, req CreateRoundRequest) (*RoundVO, error) {
	opensAt, err := parseDateTime(req.OpensAt)
	if err != nil {
		return nil, err
	}
	closesAt, err := parseDateTime(req.ClosesAt)
	if err != nil {
		return nil, err
	}
	if !closesAt.After(opensAt) {
		return nil, fmt.Errorf("%w: 结束时间必须晚于开始时间", ErrInvalidTime)
	}

	ok, err := s.dao.TermExists(ctx, req.TermID)
	if err != nil {
		return nil, err
	}
	if !ok {
		return nil, ErrTermNotFound
	}

	round := &Round{
		TermID:     req.TermID,
		Name:       req.Name,
		OpensAt:    opensAt,
		ClosesAt:   closesAt,
		MaxCredits: req.MaxCredits,
		AllowGrade: req.AllowGrade,
		Status:     RoundStatusOpen,
	}
	if err := s.dao.CreateRound(ctx, round); err != nil {
		return nil, err
	}

	s.log.Info("创建选课轮次",
		zap.Int64("round_id", round.ID),
		zap.String("name", round.Name),
		zap.Time("opens_at", round.OpensAt),
		zap.Time("closes_at", round.ClosesAt),
	)

	return &RoundVO{
		RoundID:    round.ID,
		TermID:     round.TermID,
		Name:       round.Name,
		OpensAt:    round.OpensAt,
		ClosesAt:   round.ClosesAt,
		MaxCredits: round.MaxCredits,
		Open:       true,
	}, nil
}

// ListRounds 列出轮次（管理员）。
func (s *Service) ListRounds(ctx context.Context, termID int64) ([]RoundVO, error) {
	list, err := s.dao.ListRounds(ctx, termID)
	if err != nil {
		return nil, err
	}
	out := make([]RoundVO, 0, len(list))
	for _, r := range list {
		out = append(out, RoundVO{
			RoundID:    r.ID,
			TermID:     r.TermID,
			Name:       r.Name,
			OpensAt:    r.OpensAt,
			ClosesAt:   r.ClosesAt,
			MaxCredits: r.MaxCredits,
		})
	}
	return out, nil
}

// ---------------------------------------------------------------------------
// 抢课 / 退课
// ---------------------------------------------------------------------------

// Enroll 抢课。
//
// 流程：找班次所属学期 → 找开放中的轮次 → 进事务扣名额并写记录。
//
// 学生身份只从 token 推导（u.ID），不接受任何请求参数里的 student_id。
func (s *Service) Enroll(ctx context.Context, u *auth.User, offeringID int64) (*EnrollVO, error) {
	termID, err := s.dao.TermOfOffering(ctx, offeringID)
	if err != nil {
		return nil, err
	}
	if termID == 0 {
		return nil, ErrOfferingNotFound
	}

	now, err := s.dao.Now(ctx)
	if err != nil {
		return nil, err
	}

	round, err := s.dao.RoundCovering(ctx, termID, now)
	if err != nil {
		return nil, err
	}
	if round == nil {
		next, err := s.dao.NextRound(ctx, termID, now)
		if err != nil {
			return nil, err
		}
		if next != nil {
			return nil, fmt.Errorf("%w: %s 将于 %s 开放",
				ErrRoundNotStarted, next.Name, next.OpensAt.Format("01-02 15:04"))
		}
		return nil, ErrRoundClosed
	}

	res, err := s.dao.Enroll(ctx, EnrollParams{
		StudentID:  u.ID,
		OfferingID: offeringID,
		RoundID:    round.ID,
		MaxCredits: round.MaxCredits,
		AllowGrade: round.AllowGrade,
	})
	if err != nil {
		if errors.Is(err, ErrOfferingFull) {
			s.log.Info("抢课失败：名额已满",
				zap.Int64("student_id", u.ID),
				zap.Int64("offering_id", offeringID),
			)
		}
		return nil, err
	}

	remaining := res.Capacity - res.Taken
	if remaining < 0 {
		remaining = 0
	}

	s.log.Info("抢课成功",
		zap.Int64("student_id", u.ID),
		zap.Int64("offering_id", offeringID),
		zap.Int64("round_id", round.ID),
		zap.Int("taken", res.Taken),
		zap.Int("capacity", res.Capacity),
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

// Drop 退课。
func (s *Service) Drop(ctx context.Context, u *auth.User, offeringID int64) error {
	if err := s.dao.Drop(ctx, u.ID, offeringID); err != nil {
		return err
	}
	s.log.Info("退课成功",
		zap.Int64("student_id", u.ID),
		zap.Int64("offering_id", offeringID),
	)
	return nil
}

// ---------------------------------------------------------------------------
// 我的选课 / 课表
// ---------------------------------------------------------------------------

// MyEnrollments 返回我的选课列表，并补齐上课时间段。
func (s *Service) MyEnrollments(
	ctx context.Context,
	studentID int64,
	query MyEnrollmentQuery,
) ([]MyEnrollmentVO, error) {
	if query.TermID <= 0 {
		termID, err := s.dao.CurrentTermID(ctx)
		if err != nil {
			return nil, err
		}
		query.TermID = termID
	}

	rows, err := s.dao.MyEnrollments(ctx, studentID, query)
	if err != nil {
		return nil, err
	}

	ids := make([]int64, 0, len(rows))
	for _, r := range rows {
		ids = append(ids, r.OfferingID)
	}
	slots, err := s.dao.SlotsFor(ctx, ids)
	if err != nil {
		return nil, err
	}

	out := make([]MyEnrollmentVO, 0, len(rows))
	for _, r := range rows {
		s := slots[r.OfferingID]
		if s == nil {
			s = []SlotVO{}
		}
		out = append(out, MyEnrollmentVO{
			EnrollmentID: r.EnrollmentID,
			OfferingID:   r.OfferingID,
			CourseCode:   r.CourseCode,
			CourseName:   r.CourseName,
			Credits:      r.Credits,
			TeacherName:  r.TeacherName,
			SectionNo:    r.SectionNo,
			Location:     r.Location,
			Slots:        s,
			EnrolledAt:   r.EnrolledAt,
		})
	}
	return out, nil
}

// MySchedule 返回按星期几分好组的课表。
func (s *Service) MySchedule(ctx context.Context, studentID, termID int64) (*ScheduleVO, error) {
	if termID <= 0 {
		id, err := s.dao.CurrentTermID(ctx)
		if err != nil {
			return nil, err
		}
		termID = id
	}

	rows, err := s.dao.MySchedule(ctx, studentID, termID)
	if err != nil {
		return nil, err
	}

	days := make(map[int][]ScheduleItemVO, 7)
	seen := make(map[int64]struct{}, len(rows))
	for _, r := range rows {
		seen[r.OfferingID] = struct{}{}
		days[r.Weekday] = append(days[r.Weekday], ScheduleItemVO{
			CourseName:  r.CourseName,
			TeacherName: r.TeacherName,
			Location:    r.Location,
			Weekday:     r.Weekday,
			StartPeriod: r.StartPeriod,
			EndPeriod:   r.EndPeriod,
		})
	}

	credits, err := s.dao.UsedCredits(ctx, studentID, termID)
	if err != nil {
		return nil, err
	}

	return &ScheduleVO{
		TermID:       termID,
		TotalCount:   len(seen),
		TotalCredits: credits,
		Days:         days,
	}, nil
}

// ---------------------------------------------------------------------------
// 内部工具
// ---------------------------------------------------------------------------

// parseDateTime 依次尝试 RFC3339、带秒的本地时间、纯日期。
//
// 用 ParseInLocation 而不是 Parse：不带时区的时间串要按服务器本地时区解释，
// 否则 UTC 与本地时间会差 8 小时，轮次窗口就全错了。
func parseDateTime(s string) (time.Time, error) {
	layouts := []string{
		time.RFC3339,
		"2006-01-02 15:04:05",
		"2006-01-02T15:04:05",
		"2006-01-02",
	}
	for _, layout := range layouts {
		if t, err := time.ParseInLocation(layout, s, time.Local); err == nil {
			return t, nil
		}
	}
	return time.Time{}, ErrInvalidTime
}
