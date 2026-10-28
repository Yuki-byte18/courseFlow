package enrollment

import (
	"context"
	"fmt"
	"strings"
	"time"

	"gorm.io/gorm"

	"courseflow/internal/platform/mysql"
)

// EnrollParams 是抢课事务的入参。
type EnrollParams struct {
	StudentID  int64
	OfferingID int64
	RoundID    int64
	// MaxCredits 来自轮次规则，由 service 先校验窗口后再传进来
	MaxCredits float64
	// AllowGrade 来自轮次：0 表示不限年级
	AllowGrade int
	// BypassLimits 为 true 时跳过学分上限与年级限制。
	// 只给管理员「补录」用：补录的意义就是给特殊情况开例外，
	// 但**容量、时间冲突、唯一性三件事绝不绕过**。
	BypassLimits bool
}

// EnrollResult 是抢课事务的结果。
type EnrollResult struct {
	EnrollmentID int64
	OfferingID   int64
	CourseName   string
	Credits      float64
	Capacity     int
	Taken        int
}

// MyEnrollmentRow 是「我的选课」的联表投影。
type MyEnrollmentRow struct {
	EnrollmentID int64     `gorm:"column:enrollment_id"`
	OfferingID   int64     `gorm:"column:offering_id"`
	CourseCode   string    `gorm:"column:course_code"`
	CourseName   string    `gorm:"column:course_name"`
	Credits      float64   `gorm:"column:credits"`
	TeacherName  string    `gorm:"column:teacher_name"`
	SectionNo    string    `gorm:"column:section_no"`
	Location     string    `gorm:"column:location"`
	EnrolledAt   time.Time `gorm:"column:enrolled_at"`
}

// ScheduleRow 是课表的一行（一个时间段）。
type ScheduleRow struct {
	OfferingID  int64  `gorm:"column:offering_id"`
	CourseName  string `gorm:"column:course_name"`
	TeacherName string `gorm:"column:teacher_name"`
	Location    string `gorm:"column:location"`
	Weekday     int    `gorm:"column:weekday"`
	StartPeriod int    `gorm:"column:start_period"`
	EndPeriod   int    `gorm:"column:end_period"`
}

// DAO 是选课域的数据访问层。
type DAO struct {
	db *gorm.DB
}

// NewDAO 构造 DAO。
func NewDAO(db *gorm.DB) *DAO { return &DAO{db: db} }

// ---------------------------------------------------------------------------
// 轮次
// ---------------------------------------------------------------------------

// CreateRound 写入选课轮次。
func (d *DAO) CreateRound(ctx context.Context, r *Round) error {
	if err := d.db.WithContext(ctx).Create(r).Error; err != nil {
		return fmt.Errorf("写入轮次失败: %w", err)
	}
	return nil
}

// ListRounds 按学期列出轮次（管理端）。
func (d *DAO) ListRounds(ctx context.Context, termID int64) ([]Round, error) {
	q := d.db.WithContext(ctx).Model(&Round{})
	if termID > 0 {
		q = q.Where("term_id = ?", termID)
	}
	var list []Round
	if err := q.Order("opens_at DESC").Limit(200).Find(&list).Error; err != nil {
		return nil, fmt.Errorf("查询轮次失败: %w", err)
	}
	return list, nil
}

// RoundCovering 返回在 now 时刻开放中的轮次。没有则返回 (nil, nil)。
func (d *DAO) RoundCovering(ctx context.Context, termID int64, now time.Time) (*Round, error) {
	var rows []Round
	err := d.db.WithContext(ctx).Raw(`
		SELECT id, term_id, name, opens_at, closes_at, max_credits, allow_grade, status, created_at
		  FROM rounds
		 WHERE term_id = ? AND status = 1 AND opens_at <= ? AND closes_at >= ?
		 ORDER BY opens_at DESC
		 LIMIT 1`, termID, now, now).Scan(&rows).Error
	if err != nil {
		return nil, fmt.Errorf("查询当前轮次失败: %w", err)
	}
	if len(rows) == 0 {
		return nil, nil
	}
	return &rows[0], nil
}

// NextRound 返回 now 之后最早开放的一轮，用于给「还没开始」提供明确提示。
func (d *DAO) NextRound(ctx context.Context, termID int64, now time.Time) (*Round, error) {
	var rows []Round
	err := d.db.WithContext(ctx).Raw(`
		SELECT id, term_id, name, opens_at, closes_at, max_credits, allow_grade, status, created_at
		  FROM rounds
		 WHERE term_id = ? AND status = 1 AND opens_at > ?
		 ORDER BY opens_at ASC
		 LIMIT 1`, termID, now).Scan(&rows).Error
	if err != nil {
		return nil, fmt.Errorf("查询下一轮次失败: %w", err)
	}
	if len(rows) == 0 {
		return nil, nil
	}
	return &rows[0], nil
}

// TermOfOffering 返回班次所属学期。班次不存在时返回 0。
func (d *DAO) TermOfOffering(ctx context.Context, offeringID int64) (int64, error) {
	var termID int64
	err := d.db.WithContext(ctx).Raw(
		`SELECT term_id FROM offerings WHERE id = ?`, offeringID).Scan(&termID).Error
	if err != nil {
		return 0, fmt.Errorf("查询班次所属学期失败: %w", err)
	}
	return termID, nil
}

// TermExists 判断学期是否存在。
func (d *DAO) TermExists(ctx context.Context, termID int64) (bool, error) {
	var n int64
	err := d.db.WithContext(ctx).Raw(`SELECT COUNT(*) FROM terms WHERE id = ?`, termID).Scan(&n).Error
	if err != nil {
		return false, fmt.Errorf("校验学期失败: %w", err)
	}
	return n > 0, nil
}

// UsedCredits 统计某学生在某学期已占用的学分（只看有效记录）。
func (d *DAO) UsedCredits(ctx context.Context, studentID, termID int64) (float64, error) {
	var used float64
	err := d.db.WithContext(ctx).Raw(`
		SELECT COALESCE(SUM(e.credits), 0)
		  FROM enrollments e
		  JOIN offerings o ON o.id = e.offering_id
		 WHERE e.student_id = ? AND e.status = 1 AND o.term_id = ?`,
		studentID, termID).Scan(&used).Error
	if err != nil {
		return 0, fmt.Errorf("统计已选学分失败: %w", err)
	}
	return used, nil
}

// ---------------------------------------------------------------------------
// 抢课：整个系统的核心事务
// ---------------------------------------------------------------------------

// Enroll 在**一个事务**里完成：锁行 → 校验 → 扣名额 → 写记录。
//
// 为什么这几步必须在同一个事务里：
//   - 扣了名额但记录没写进去 → 名额被白白吃掉（幽灵占位）
//   - 记录写进去了但名额没扣   → 超卖
//
// `FOR UPDATE` 会锁住这一行 offerings，把同一门课的并发抢课串行化。
// 这正是 M1 的性能天花板，也是 M2 要用 Redis Lua 绕过的东西。
func (d *DAO) Enroll(ctx context.Context, p EnrollParams) (*EnrollResult, error) {
	var res EnrollResult

	err := mysql.WithTx(ctx, d.db, func(tx *gorm.DB) error {
		// ---- 1. 锁住班次行，同时取课程学分 ----
		var row struct {
			CourseName string  `gorm:"column:course_name"`
			Credits    float64 `gorm:"column:credits"`
			Capacity   int     `gorm:"column:capacity"`
			Taken      int     `gorm:"column:taken"`
			Status     int8    `gorm:"column:status"`
			TermID     int64   `gorm:"column:term_id"`
		}
		err := tx.Raw(`
			SELECT c.name AS course_name, c.credits,
			       o.capacity, o.taken, o.status, o.term_id
			  FROM offerings o
			  JOIN courses   c ON c.id = o.course_id
			 WHERE o.id = ?
			 FOR UPDATE`, p.OfferingID).Scan(&row).Error
		if err != nil {
			return fmt.Errorf("读取班次失败: %w", err)
		}
		// 查不到时 Scan 不会赋值，term_id 保持 0
		if row.TermID == 0 {
			return ErrOfferingNotFound
		}
		if row.Status != offeringStatusOpen {
			return ErrOfferingNotOpen
		}

		// ---- 2. 年级限制 ----
		//
		// grade = 0 表示"未设置"，此时**不拦截** ——
		// 否则一升级就把所有没有年级信息的老账号挡在门外。
		if !p.BypassLimits && p.AllowGrade > 0 {
			var grade int
			if err := tx.Raw(`SELECT grade FROM users WHERE id = ?`, p.StudentID).
				Scan(&grade).Error; err != nil {
				return fmt.Errorf("读取年级失败: %w", err)
			}
			if grade > 0 && grade != p.AllowGrade {
				return fmt.Errorf("%w：本轮仅面向 %d 年级", ErrGradeRestricted, p.AllowGrade)
			}
		}

		// ---- 3. 时间冲突 ----
		//
		// 判定两个时间段相交：同一天，且 s1.start <= s2.end && s2.start <= s1.end。
		// 用「已选记录的时间段」JOIN「新班次的时间段」，一次查询出结果。
		// 班次没有配时间段时 JOIN 为空 → 不构成冲突（这是合理的：没时间就没法冲突）。
		var conflictCourse string
		if err := tx.Raw(`
			SELECT c.name
			  FROM enrollments e
			  JOIN offerings      o  ON o.id  = e.offering_id
			  JOIN courses        c  ON c.id  = o.course_id
			  JOIN offering_slots s1 ON s1.offering_id = e.offering_id
			  JOIN offering_slots s2 ON s2.offering_id = ?
			 WHERE e.student_id = ? AND e.status = 1
			   AND s1.weekday = s2.weekday
			   AND s1.start_period <= s2.end_period
			   AND s2.start_period <= s1.end_period
			 LIMIT 1`, p.OfferingID, p.StudentID).Scan(&conflictCourse).Error; err != nil {
			return fmt.Errorf("校验时间冲突失败: %w", err)
		}
		if conflictCourse != "" {
			return fmt.Errorf("%w：与「%s」的上课时间重叠", ErrTimeConflict, conflictCourse)
		}

		// ---- 4. 学分上限（按同一学期累计）----
		var used float64
		if !p.BypassLimits {
			if err := tx.Raw(`
				SELECT COALESCE(SUM(e.credits), 0)
				  FROM enrollments e
				  JOIN offerings o ON o.id = e.offering_id
				 WHERE e.student_id = ? AND e.status = 1 AND o.term_id = ?`,
				p.StudentID, row.TermID).Scan(&used).Error; err != nil {
				return fmt.Errorf("统计已选学分失败: %w", err)
			}
			if used+row.Credits > p.MaxCredits {
				return fmt.Errorf("%w: 已选 %.1f，本门 %.1f，上限 %.1f",
					ErrCreditLimit, used, row.Credits, p.MaxCredits)
			}
		}

		// ---- 5. 是否已有记录（含已退的）----
		var existing struct {
			ID     int64 `gorm:"column:id"`
			Status int8  `gorm:"column:status"`
		}
		if err := tx.Raw(
			`SELECT id, status FROM enrollments WHERE student_id = ? AND offering_id = ?`,
			p.StudentID, p.OfferingID).Scan(&existing).Error; err != nil {
			return fmt.Errorf("查询已有选课记录失败: %w", err)
		}
		if existing.ID != 0 && existing.Status == StatusActive {
			return ErrAlreadyEnrolled
		}

		// ---- 6. 条件扣减：影响行数为 0 就是满了 ----
		//
		// 这里和 1 的 FOR UPDATE 有重复保护：即使去掉锁，
		// `AND taken < capacity` 也保证不会超卖。锁是为了让并发变成排队，
		// 而不是让大量事务走到第 4 步才失败回滚。
		upd := tx.Exec(
			`UPDATE offerings SET taken = taken + 1 WHERE id = ? AND taken < capacity`,
			p.OfferingID)
		if upd.Error != nil {
			return fmt.Errorf("扣减名额失败: %w", upd.Error)
		}
		if upd.RowsAffected == 0 {
			return ErrOfferingFull
		}

		// ---- 7. 写记录（退课重选走 UPDATE）----
		if existing.ID != 0 {
			if err := tx.Exec(`
				UPDATE enrollments
				   SET status = 1, dropped_at = NULL, round_id = ?, credits = ?
				 WHERE id = ?`,
				p.RoundID, row.Credits, existing.ID).Error; err != nil {
				return fmt.Errorf("恢复选课记录失败: %w", err)
			}
			res.EnrollmentID = existing.ID
		} else {
			e := Enrollment{
				StudentID:  p.StudentID,
				OfferingID: p.OfferingID,
				RoundID:    p.RoundID,
				Credits:    row.Credits,
				Status:     StatusActive,
			}
			if err := tx.Create(&e).Error; err != nil {
				// 唯一索引是最后一道防线：并发下两个请求都通过了第 3 步时，
				// 这里会拦住一个。
				if isDuplicateKey(err) {
					return ErrAlreadyEnrolled
				}
				return fmt.Errorf("写入选课记录失败: %w", err)
			}
			res.EnrollmentID = e.ID
		}

		res.OfferingID = p.OfferingID
		res.CourseName = row.CourseName
		res.Credits = row.Credits
		res.Capacity = row.Capacity
		// 行已被本事务锁住，读取时的 Taken 加 1 就是本次操作后的值
		res.Taken = row.Taken + 1
		return nil
	})
	if err != nil {
		return nil, err
	}
	return &res, nil
}

// Drop 退课：把记录置为已退，并把名额还回去。
func (d *DAO) Drop(ctx context.Context, studentID, offeringID int64) error {
	return mysql.WithTx(ctx, d.db, func(tx *gorm.DB) error {
		res := tx.Exec(`
			UPDATE enrollments
			   SET status = 2, dropped_at = NOW(3)
			 WHERE student_id = ? AND offering_id = ? AND status = 1`,
			studentID, offeringID)
		if res.Error != nil {
			return fmt.Errorf("退课失败: %w", res.Error)
		}
		if res.RowsAffected == 0 {
			return ErrNotEnrolled
		}

		// taken > 0 兜底：即使记录与计数不同步，也绝不把名额还成负数
		if err := tx.Exec(
			`UPDATE offerings SET taken = taken - 1 WHERE id = ? AND taken > 0`,
			offeringID).Error; err != nil {
			return fmt.Errorf("归还名额失败: %w", err)
		}
		return nil
	})
}

// ---------------------------------------------------------------------------
// 我的选课 / 课表
// ---------------------------------------------------------------------------

// MyEnrollments 查某学生的选课记录。
func (d *DAO) MyEnrollments(
	ctx context.Context,
	studentID int64,
	q MyEnrollmentQuery,
) ([]MyEnrollmentRow, error) {
	where := []string{"e.student_id = ?"}
	args := []any{studentID}

	if !q.IncludeDropped {
		where = append(where, "e.status = 1")
	}
	if q.TermID > 0 {
		where = append(where, "o.term_id = ?")
		args = append(args, q.TermID)
	}

	sql := `
		SELECT e.id AS enrollment_id, e.offering_id,
		       c.code AS course_code, c.name AS course_name,
		       e.credits,
		       u.real_name AS teacher_name,
		       o.section_no, o.location,
		       e.created_at AS enrolled_at
		  FROM enrollments e
		  JOIN offerings o ON o.id = e.offering_id
		  JOIN courses   c ON c.id = o.course_id
		  JOIN users     u ON u.id = o.teacher_id
		 WHERE ` + strings.Join(where, " AND ") + `
		 ORDER BY c.code`

	var rows []MyEnrollmentRow
	if err := d.db.WithContext(ctx).Raw(sql, args...).Scan(&rows).Error; err != nil {
		return nil, fmt.Errorf("查询我的选课失败: %w", err)
	}
	return rows, nil
}

// SlotsFor 批量取时间段，避免 N+1。
func (d *DAO) SlotsFor(ctx context.Context, offeringIDs []int64) (map[int64][]SlotVO, error) {
	result := make(map[int64][]SlotVO, len(offeringIDs))
	if len(offeringIDs) == 0 {
		return result, nil
	}

	type slotRow struct {
		OfferingID  int64 `gorm:"column:offering_id"`
		Weekday     int   `gorm:"column:weekday"`
		StartPeriod int   `gorm:"column:start_period"`
		EndPeriod   int   `gorm:"column:end_period"`
	}

	var rows []slotRow
	err := d.db.WithContext(ctx).Raw(`
		SELECT offering_id, weekday, start_period, end_period
		  FROM offering_slots
		 WHERE offering_id IN ?
		 ORDER BY weekday, start_period`, offeringIDs).Scan(&rows).Error
	if err != nil {
		return nil, fmt.Errorf("查询上课时间失败: %w", err)
	}

	for _, r := range rows {
		result[r.OfferingID] = append(result[r.OfferingID], SlotVO{
			Weekday:     r.Weekday,
			StartPeriod: r.StartPeriod,
			EndPeriod:   r.EndPeriod,
		})
	}
	return result, nil
}

// MySchedule 查某学生的课表（平铺的时间段行）。
func (d *DAO) MySchedule(ctx context.Context, studentID, termID int64) ([]ScheduleRow, error) {
	where := []string{"e.student_id = ?", "e.status = 1"}
	args := []any{studentID}
	if termID > 0 {
		where = append(where, "o.term_id = ?")
		args = append(args, termID)
	}

	sql := `
		SELECT e.offering_id, c.name AS course_name, u.real_name AS teacher_name, o.location,
		       s.weekday, s.start_period, s.end_period
		  FROM enrollments e
		  JOIN offerings      o ON o.id = e.offering_id
		  JOIN courses        c ON c.id = o.course_id
		  JOIN users          u ON u.id = o.teacher_id
		  JOIN offering_slots s ON s.offering_id = o.id
		 WHERE ` + strings.Join(where, " AND ") + `
		 ORDER BY s.weekday, s.start_period`

	var rows []ScheduleRow
	if err := d.db.WithContext(ctx).Raw(sql, args...).Scan(&rows).Error; err != nil {
		return nil, fmt.Errorf("查询课表失败: %w", err)
	}
	return rows, nil
}

// offeringStatusOpen 与 course.OfferingStatusOpen 取值一致。
//
// 这里刻意不 import course 包：选课域不该因为一个常量就依赖课程域，
// 两边共享的是数据库里的同一个值。
const offeringStatusOpen int8 = 1

// CurrentTermID 返回标记为当前学期的 ID；没有则返回 0。
func (d *DAO) CurrentTermID(ctx context.Context) (int64, error) {
	var id int64
	err := d.db.WithContext(ctx).Raw(
		`SELECT id FROM terms WHERE is_current = 1 ORDER BY id DESC LIMIT 1`).Scan(&id).Error
	if err != nil {
		return 0, fmt.Errorf("查询当前学期失败: %w", err)
	}
	return id, nil
}

// Now 返回数据库的当前时刻。
//
// 用数据库时钟而不是 Go 的 time.Now()：轮次窗口的比较要和
// `dropped_at = NOW(3)` 用同一个时钟，否则服务器时区不一致时两者会错开。
func (d *DAO) Now(ctx context.Context) (time.Time, error) {
	var now time.Time
	if err := d.db.WithContext(ctx).Raw(`SELECT NOW(3)`).Scan(&now).Error; err != nil {
		return time.Time{}, fmt.Errorf("读取数据库时间失败: %w", err)
	}
	return now, nil
}

// isDuplicateKey 判断唯一键冲突。
func isDuplicateKey(err error) bool {
	if err == nil {
		return false
	}
	msg := strings.ToLower(err.Error())
	return strings.Contains(msg, "duplicate") || strings.Contains(msg, "1062")
}
