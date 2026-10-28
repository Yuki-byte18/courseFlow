package course

import (
	"context"
	"fmt"
	"strings"

	"gorm.io/gorm"

	"courseflow/internal/platform/mysql"
)

// OfferingRow 是班次列表/详情的联表投影。
//
// 不直接用 Offering 模型：选课台一行要同时展示课程名、学分、教师名，
// 这些散在三张表里，一次 JOIN 取齐比多次查询划算。
type OfferingRow struct {
	ID          int64   `gorm:"column:id"`
	TermID      int64   `gorm:"column:term_id"`
	CourseID    int64   `gorm:"column:course_id"`
	CourseCode  string  `gorm:"column:course_code"`
	CourseName  string  `gorm:"column:course_name"`
	Credits     float64 `gorm:"column:credits"`
	Department  string  `gorm:"column:department"`
	TeacherID   int64   `gorm:"column:teacher_id"`
	TeacherName string  `gorm:"column:teacher_name"`
	SectionNo   string  `gorm:"column:section_no"`
	Capacity    int     `gorm:"column:capacity"`
	Taken       int     `gorm:"column:taken"`
	Location    string  `gorm:"column:location"`
	Status      int8    `gorm:"column:status"`
}

// DAO 是课程域的数据访问层，只做数据库读写，不含业务判断。
type DAO struct {
	db *gorm.DB
}

// NewDAO 构造 DAO。
func NewDAO(db *gorm.DB) *DAO { return &DAO{db: db} }

// ---------------------------------------------------------------------------
// 学期
// ---------------------------------------------------------------------------

// ListTerms 返回全部学期，当前学期排最前。
func (d *DAO) ListTerms(ctx context.Context) ([]Term, error) {
	var terms []Term
	if err := d.db.WithContext(ctx).
		Order("is_current DESC, starts_on DESC").
		Find(&terms).Error; err != nil {
		return nil, fmt.Errorf("查询学期失败: %w", err)
	}
	return terms, nil
}

// CreateTerm 写入学期。
//
// 若标记为当前学期，先把其它学期的标记清掉 —— 全库只能有一个当前学期，
// 否则前端「当前学期」会随机取到不同的值。
func (d *DAO) CreateTerm(ctx context.Context, term *Term) error {
	return mysql.WithTx(ctx, d.db, func(tx *gorm.DB) error {
		if term.IsCurrent == 1 {
			if err := tx.Model(&Term{}).
				Where("is_current = ?", 1).
				Update("is_current", 0).Error; err != nil {
				return fmt.Errorf("重置当前学期失败: %w", err)
			}
		}
		if err := tx.Create(term).Error; err != nil {
			return fmt.Errorf("写入学期失败: %w", err)
		}
		return nil
	})
}

// ---------------------------------------------------------------------------
// 课程
// ---------------------------------------------------------------------------

// CreateCourse 写入课程主数据。
func (d *DAO) CreateCourse(ctx context.Context, c *Course) error {
	if err := d.db.WithContext(ctx).Create(c).Error; err != nil {
		return fmt.Errorf("写入课程失败: %w", err)
	}
	return nil
}

// ListCourses 按学期无关的方式列出课程（管理端用）。
func (d *DAO) ListCourses(ctx context.Context, keyword string, limit int) ([]Course, error) {
	q := d.db.WithContext(ctx).Model(&Course{})
	if keyword != "" {
		like := "%" + keyword + "%"
		q = q.Where("name LIKE ? OR code LIKE ?", like, like)
	}
	var list []Course
	if err := q.Order("code").Limit(limit).Find(&list).Error; err != nil {
		return nil, fmt.Errorf("查询课程失败: %w", err)
	}
	return list, nil
}

// ---------------------------------------------------------------------------
// 班次
// ---------------------------------------------------------------------------

// ListOfferings 按条件分页查询开放中的班次。
//
// WHERE 子句用切片动态拼装，而不是写死一串"恒真条件 OR"：
// 前者能走索引，后者的空值分支会让优化器放弃索引。
func (d *DAO) ListOfferings(
	ctx context.Context,
	query OfferingQuery,
) ([]OfferingRow, int64, error) {
	where := []string{"o.status = ?"}
	args := []any{OfferingStatusOpen}

	if query.TermID > 0 {
		where = append(where, "o.term_id = ?")
		args = append(args, query.TermID)
	}
	if query.Department != "" {
		where = append(where, "c.department = ?")
		args = append(args, query.Department)
	}
	if query.Keyword != "" {
		where = append(where, "(c.name LIKE ? OR c.code LIKE ?)")
		like := "%" + query.Keyword + "%"
		args = append(args, like, like)
	}
	if query.TeacherID > 0 {
		where = append(where, "o.teacher_id = ?")
		args = append(args, query.TeacherID)
	}

	cond := strings.Join(where, " AND ")

	var total int64
	countSQL := `SELECT COUNT(*) FROM offerings o JOIN courses c ON c.id = o.course_id WHERE ` + cond
	if err := d.db.WithContext(ctx).Raw(countSQL, args...).Scan(&total).Error; err != nil {
		return nil, 0, fmt.Errorf("统计班次失败: %w", err)
	}
	if total == 0 {
		return []OfferingRow{}, 0, nil
	}

	listSQL := `
		SELECT o.id, o.term_id, o.course_id,
		       c.code AS course_code, c.name AS course_name,
		       c.credits, c.department,
		       o.teacher_id, u.real_name AS teacher_name,
		       o.section_no, o.capacity, o.taken, o.location, o.status
		  FROM offerings o
		  JOIN courses c ON c.id = o.course_id
		  JOIN users   u ON u.id = o.teacher_id
		 WHERE ` + cond + `
		 ORDER BY c.code, o.section_no
		 LIMIT ? OFFSET ?`

	args = append(args, query.PageSize, (query.Page-1)*query.PageSize)

	var rows []OfferingRow
	if err := d.db.WithContext(ctx).Raw(listSQL, args...).Scan(&rows).Error; err != nil {
		return nil, 0, fmt.Errorf("查询班次失败: %w", err)
	}
	return rows, total, nil
}

// OfferingByID 查单个班次的联表投影。查不到返回 (nil, nil)。
func (d *DAO) OfferingByID(ctx context.Context, id int64) (*OfferingRow, error) {
	var row OfferingRow
	err := d.db.WithContext(ctx).Raw(`
		SELECT o.id, o.term_id, o.course_id,
		       c.code AS course_code, c.name AS course_name,
		       c.credits, c.department,
		       o.teacher_id, u.real_name AS teacher_name,
		       o.section_no, o.capacity, o.taken, o.location, o.status
		  FROM offerings o
		  JOIN courses c ON c.id = o.course_id
		  JOIN users   u ON u.id = o.teacher_id
		 WHERE o.id = ?`, id).Scan(&row).Error
	if err != nil {
		return nil, fmt.Errorf("查询班次失败: %w", err)
	}
	if row.ID == 0 {
		return nil, nil
	}
	return &row, nil
}

// SlotsFor 批量取多个班次的时间段，避免 N+1 查询。
func (d *DAO) SlotsFor(ctx context.Context, offeringIDs []int64) (map[int64][]Slot, error) {
	result := make(map[int64][]Slot, len(offeringIDs))
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
		result[r.OfferingID] = append(result[r.OfferingID], Slot{
			Weekday:     r.Weekday,
			StartPeriod: r.StartPeriod,
			EndPeriod:   r.EndPeriod,
		})
	}
	return result, nil
}

// CreateOffering 在一个事务里写入班次与其时间段。
//
// 必须用事务：班次写成功但时间段没写进去，会造出一个"没有任何上课时间"
// 的班次，前端课表上是空白，排查起来很费劲。
func (d *DAO) CreateOffering(ctx context.Context, o *Offering, slots []Slot) error {
	return mysql.WithTx(ctx, d.db, func(tx *gorm.DB) error {
		if err := tx.Create(o).Error; err != nil {
			return fmt.Errorf("写入班次失败: %w", err)
		}
		for _, s := range slots {
			row := OfferingSlot{
				OfferingID:  o.ID,
				Weekday:     s.Weekday,
				StartPeriod: s.StartPeriod,
				EndPeriod:   s.EndPeriod,
			}
			if err := tx.Create(&row).Error; err != nil {
				return fmt.Errorf("写入上课时间失败: %w", err)
			}
		}
		return nil
	})
}

// TermAndCourseExist 一次往返校验学期与课程都存在。
func (d *DAO) TermAndCourseExist(ctx context.Context, termID, courseID int64) (bool, error) {
	var ok int
	err := d.db.WithContext(ctx).Raw(`
		SELECT (EXISTS(SELECT 1 FROM terms WHERE id = ?)
		    AND EXISTS(SELECT 1 FROM courses WHERE id = ?))`, termID, courseID).Scan(&ok).Error
	if err != nil {
		return false, fmt.Errorf("校验学期与课程失败: %w", err)
	}
	return ok == 1, nil
}

// TermExists 判断学期是否存在。
func (d *DAO) TermExists(ctx context.Context, termID int64) (bool, error) {
	var ok int
	if err := d.db.WithContext(ctx).Raw(
		`SELECT EXISTS(SELECT 1 FROM terms WHERE id = ?)`, termID).Scan(&ok).Error; err != nil {
		return false, fmt.Errorf("校验学期失败: %w", err)
	}
	return ok == 1, nil
}

// CourseIDsByCodes 批量把课程号映射成课程 ID。
//
// 导入课表时一次查完全部课程号，避免每行查一次（N+1）。
func (d *DAO) CourseIDsByCodes(ctx context.Context, codes []string) (map[string]int64, error) {
	result := make(map[string]int64, len(codes))
	if len(codes) == 0 {
		return result, nil
	}

	var rows []struct {
		ID   int64  `gorm:"column:id"`
		Code string `gorm:"column:code"`
	}
	if err := d.db.WithContext(ctx).Raw(
		`SELECT id, code FROM courses WHERE code IN ?`, codes).Scan(&rows).Error; err != nil {
		return nil, fmt.Errorf("查询课程失败: %w", err)
	}
	for _, r := range rows {
		result[r.Code] = r.ID
	}
	return result, nil
}

// SetCurrentTerm 把指定学期设为当前学期，并清掉其它学期的标记。
//
// 先查存在性再更新，不能用 UPDATE 的 RowsAffected 判断：
// MySQL 的 RowsAffected 返回的是"实际改变的行数"，
// 把一个本来就是当前学期的记录再设一次会返回 0，从而被误判成"不存在"。
func (d *DAO) SetCurrentTerm(ctx context.Context, termID int64) error {
	return mysql.WithTx(ctx, d.db, func(tx *gorm.DB) error {
		var exists int
		if err := tx.Raw(
			`SELECT EXISTS(SELECT 1 FROM terms WHERE id = ?)`, termID).Scan(&exists).Error; err != nil {
			return fmt.Errorf("校验学期失败: %w", err)
		}
		if exists == 0 {
			return ErrTermNotFound
		}

		if err := tx.Exec(
			`UPDATE terms SET is_current = 0 WHERE is_current = 1 AND id <> ?`, termID).Error; err != nil {
			return fmt.Errorf("清理当前学期失败: %w", err)
		}
		if err := tx.Exec(
			`UPDATE terms SET is_current = 1 WHERE id = ?`, termID).Error; err != nil {
			return fmt.Errorf("设置当前学期失败: %w", err)
		}
		return nil
	})
}

// CourseExists 判断课程是否存在。
func (d *DAO) CourseExists(ctx context.Context, courseID int64) (bool, error) {
	var ok int
	if err := d.db.WithContext(ctx).Raw(
		`SELECT EXISTS(SELECT 1 FROM courses WHERE id = ?)`, courseID).Scan(&ok).Error; err != nil {
		return false, fmt.Errorf("校验课程失败: %w", err)
	}
	return ok == 1, nil
}

// CourseByID 按 ID 查课程。查不到返回 (nil, nil)。
func (d *DAO) CourseByID(ctx context.Context, courseID int64) (*Course, error) {
	var rows []Course
	if err := d.db.WithContext(ctx).Raw(
		`SELECT * FROM courses WHERE id = ?`, courseID).Scan(&rows).Error; err != nil {
		return nil, fmt.Errorf("查询课程失败: %w", err)
	}
	if len(rows) == 0 {
		return nil, nil
	}
	return &rows[0], nil
}

// UpdateCourseFields 局部更新课程。
func (d *DAO) UpdateCourseFields(ctx context.Context, courseID int64, fields map[string]any) error {
	if len(fields) == 0 {
		return nil
	}
	if err := d.db.WithContext(ctx).Model(&Course{}).
		Where("id = ?", courseID).
		Updates(fields).Error; err != nil {
		return fmt.Errorf("更新课程失败: %w", err)
	}
	return nil
}

// UpdateOffering 在一个事务里更新班次字段，并在 slots 非 nil 时替换时间段。
//
// 为什么整体替换而不是逐条增删：一次编辑只需要一个动作，
// 前端不必维护"哪条时间段被删了"，而时间段最多十条，代价可以忽略。
func (d *DAO) UpdateOffering(
	ctx context.Context,
	offeringID int64,
	fields map[string]any,
	slots []Slot,
	replaceSlots bool,
) error {
	return mysql.WithTx(ctx, d.db, func(tx *gorm.DB) error {
		if len(fields) > 0 {
			if err := tx.Model(&Offering{}).
				Where("id = ?", offeringID).
				Updates(fields).Error; err != nil {
				return fmt.Errorf("更新班次失败: %w", err)
			}
		}

		if !replaceSlots {
			return nil
		}

		if err := tx.Exec(
			`DELETE FROM offering_slots WHERE offering_id = ?`, offeringID).Error; err != nil {
			return fmt.Errorf("清理时间段失败: %w", err)
		}
		for _, s := range slots {
			row := OfferingSlot{
				OfferingID:  offeringID,
				Weekday:     s.Weekday,
				StartPeriod: s.StartPeriod,
				EndPeriod:   s.EndPeriod,
			}
			if err := tx.Create(&row).Error; err != nil {
				return fmt.Errorf("写入时间段失败: %w", err)
			}
		}
		return nil
	})
}

// TeacherExists 判断该用户是否具备教师或管理员角色。
//
// 不做这一层校验的话，随便一个学生 ID 都能被设成授课教师。
func (d *DAO) TeacherExists(ctx context.Context, userID int64) (bool, error) {
	var n int64
	err := d.db.WithContext(ctx).Raw(`
		SELECT COUNT(*)
		  FROM user_roles ur
		  JOIN roles r ON r.id = ur.role_id
		 WHERE ur.user_id = ? AND r.code IN ('teacher', 'admin')`, userID).Scan(&n).Error
	if err != nil {
		return false, fmt.Errorf("校验教师身份失败: %w", err)
	}
	return n > 0, nil
}

// Roster 查某个班次的有效选课名单。
func (d *DAO) Roster(ctx context.Context, offeringID int64) ([]RosterEntryVO, error) {
	var rows []RosterEntryVO
	err := d.db.WithContext(ctx).Raw(`
		SELECT e.id AS enrollment_id, u.username AS student_no,
		       u.real_name, e.created_at AS enrolled_at
		  FROM enrollments e
		  JOIN users u ON u.id = e.student_id
		 WHERE e.offering_id = ? AND e.status = 1
		 ORDER BY u.username`, offeringID).Scan(&rows).Error
	if err != nil {
		return nil, fmt.Errorf("查询选课名单失败: %w", err)
	}
	return rows, nil
}

// IsDuplicateKey 判断错误是否是唯一键冲突。
func IsDuplicateKey(err error) bool {
	if err == nil {
		return false
	}
	msg := strings.ToLower(err.Error())
	return strings.Contains(msg, "duplicate") || strings.Contains(msg, "1062")
}
