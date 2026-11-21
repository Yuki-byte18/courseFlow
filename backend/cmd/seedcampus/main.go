// Command seedcampus 造一份「小型校园选课」演示数据。
//
// 与 seeddemo 的区别：
//
//	seeddemo    50 门课 × 200 班次 × 500 学生，规模大、关系稀疏，适合压测
//	seedcampus  20 门课 × 30 班次 × 60 学生，**带真实选课记录**，适合演示 Agent
//
// 为什么需要它：Agent 的能力（筛选/冲突检测/余量统计/选退课）全都依赖
// "有人已经选过课"这个前提。没有 taken > 0 的数据，Agent 看起来就是很笨。
//
// 造数时遵守两条真实业务约束，否则数据本身就是错的：
//  1. 每个学生已选学分不超过轮次上限
//  2. 同一个学生的课不冲突（否则 Agent 检测冲突会检出一堆「不可能存在」的冲突）
//
// 用法：
//
//	go run ./cmd/seedcampus                  # 默认 60 学生
//	go run ./cmd/seedcampus -students 100
package main

import (
	"context"
	"flag"
	"fmt"
	"math/rand"
	"os"
	"time"

	"go.uber.org/zap"
	"golang.org/x/crypto/bcrypt"
	"gorm.io/gorm"

	"courseflow/internal/platform/config"
	"courseflow/internal/platform/mysql"
)

const (
	// 所有演示账号统一密码
	demoPassword = "Passw0rd123"
	// 学期码：与已有数据保持一致，找不到就按当前学期用
	termCode = "2026S"
	// 轮次学分上限
	maxCredits = 30.0
)

type courseSeed struct {
	Code    string
	Name    string
	Credits float64
	Dept    string
	// 开的班数
	Sections int
	// 容量
	Capacity int
	// 热度：1.0 表示尽量招满
	Heat float64
}

type slotSeed struct {
	Weekday int
	Start   int
	End     int
}

// 20 门课。学分刻意打散：3.0 的有 10 门，方便演示「哪些是三学分的课」。
var courses = []courseSeed{
	{"CS101", "数据结构", 3.0, "计算机学院", 2, 40, 0.95},
	{"CS102", "操作系统", 3.0, "计算机学院", 2, 40, 0.80},
	{"CS103", "计算机网络", 3.0, "计算机学院", 2, 40, 0.85},
	{"CS104", "编译原理", 3.0, "计算机学院", 2, 35, 0.55},
	{"CS105", "数据库系统", 3.5, "软件学院", 2, 45, 1.00},
	{"CS106", "软件工程", 2.0, "软件学院", 2, 40, 0.60},
	{"CS107", "算法设计与分析", 3.0, "计算机学院", 2, 40, 0.75},
	{"CS108", "计算机组成原理", 4.0, "计算机学院", 2, 45, 0.90},
	{"CS109", "人工智能导论", 2.0, "计算机学院", 1, 20, 1.00},
	{"CS110", "机器学习", 3.0, "计算机学院", 2, 45, 0.95},
	{"CS111", "分布式系统", 3.0, "软件学院", 1, 35, 0.70},
	{"CS112", "信息安全基础", 2.5, "计算机学院", 1, 15, 1.00},
	{"CS113", "人机交互", 2.0, "软件学院", 1, 12, 1.00},
	{"CS114", "数据挖掘", 3.0, "软件学院", 1, 10, 1.00},
	{"MA201", "线性代数", 2.5, "数学学院", 1, 60, 0.85},
	{"MA202", "概率论与数理统计", 3.0, "数学学院", 1, 60, 0.95},
	{"MA203", "离散数学", 2.5, "数学学院", 1, 40, 0.65},
	{"EE301", "信号与系统", 4.0, "电子学院", 1, 45, 0.55},
	{"EE302", "数字电路", 3.5, "电子学院", 1, 40, 0.50},
	{"EE303", "通信原理", 3.0, "电子学院", 1, 40, 0.45},
}

// 时间段：从周一到周五，上午/下午/晚上都覆盖到，方便演示冲突检测。
var timeSlots = []slotSeed{
	{1, 1, 2}, {1, 3, 4}, {1, 5, 6}, {1, 7, 8},
	{2, 1, 2}, {2, 3, 4}, {2, 5, 6}, {2, 7, 8},
	{3, 1, 2}, {3, 3, 4}, {3, 5, 6}, {3, 7, 8},
	{4, 1, 2}, {4, 3, 4}, {4, 5, 6}, {4, 7, 8},
	{5, 1, 2}, {5, 3, 4}, {5, 5, 6}, {5, 7, 8},
}

var teacherNames = []string{"张伟", "李娜", "王强", "刘敏", "陈杰", "赵磊"}
var locations = []string{"一教 301", "一教 405", "二教 210", "二教 512", "三教 108", "三教 203"}

func main() {
	students := flag.Int("students", 60, "造多少个学生")
	graduates := flag.Int("grade", 2, "学生年级（1-5）")
	flag.Parse()

	if err := run(*students, *graduates); err != nil {
		fmt.Fprintf(os.Stderr, "seedcampus 失败: %v\n", err)
		os.Exit(1)
	}
}

func run(studentCount, grade int) error {
	cfg, err := config.Load("")
	if err != nil {
		return err
	}
	if cfg.SourceFile == "" {
		return fmt.Errorf("没找到 configs/config.yaml")
	}

	ctx := context.Background()
	db, err := mysql.New(ctx, cfg.MySQL, zap.NewNop())
	if err != nil {
		return err
	}

	rng := rand.New(rand.NewSource(42)) // 固定种子：重复跑结果一致

	// ---- 1. 学期 ----
	termID, err := ensureTerm(db)
	if err != nil {
		return err
	}
	fmt.Printf("学期: id=%d (%s)\n", termID, termCode)

	// ---- 2. 轮次：把时间窗打开，否则学生选不了课 ----
	roundID, err := ensureRound(db, termID)
	if err != nil {
		return err
	}
	fmt.Printf("轮次: id=%d（已开放，学分上限 %.0f）\n", roundID, maxCredits)

	// ---- 3. 教师 ----
	teacherIDs, err := ensureTeachers(db, len(teacherNames))
	if err != nil {
		return err
	}
	fmt.Printf("教师: %d 位\n", len(teacherIDs))

	// ---- 4. 学生 ----
	studentIDs, err := ensureStudents(db, studentCount, grade)
	if err != nil {
		return err
	}
	fmt.Printf("学生: %d 位\n", len(studentIDs))

	// ---- 5. 课程 ----
	if err := ensureCourses(db); err != nil {
		return err
	}
	fmt.Printf("课程: %d 门\n", len(courses))

	// ---- 6. 班次 + 时间段 ----
	offerings, err := ensureOfferings(db, termID, teacherIDs, rng)
	if err != nil {
		return err
	}
	fmt.Printf("班次: %d 个（新增 %d）\n", len(offerings), countNew(offerings))

	// ---- 7. 模拟选课 ----
	enrolled, err := simulateEnrollments(db, roundID, offerings, studentIDs, rng)
	if err != nil {
		return err
	}
	fmt.Printf("选课记录: 新增 %d 条\n", enrolled)

	// ---- 8. 汇总 ----
	if err := report(db, termID); err != nil {
		return err
	}

	fmt.Printf("\n演示账号（统一密码 %s）:\n", demoPassword)
	fmt.Println("  学生: 2024001 .. 2024" + fmt.Sprintf("%03d", studentCount))
	fmt.Println("  教师: T1001 .. T100" + fmt.Sprint(len(teacherNames)))
	return nil
}

// ---------------------------------------------------------------------------
// 学期 / 轮次
// ---------------------------------------------------------------------------
func ensureTerm(db *gorm.DB) (int64, error) {
	var id int64
	// 优先用现有的当前学期
	if err := db.Raw("SELECT id FROM terms WHERE is_current = 1 LIMIT 1").Scan(&id).Error; err != nil {
		return 0, err
	}
	if id > 0 {
		return id, nil
	}
	if err := db.Raw("SELECT id FROM terms WHERE code = ? LIMIT 1", termCode).Scan(&id).Error; err != nil {
		return 0, err
	}
	if id > 0 {
		// 没有当前学期就把它设成当前
		if err := db.Exec("UPDATE terms SET is_current = 0").Error; err != nil {
			return 0, err
		}
		if err := db.Exec("UPDATE terms SET is_current = 1 WHERE id = ?", id).Error; err != nil {
			return 0, err
		}
		return id, nil
	}

	now := time.Now()
	if err := db.Exec(`
		INSERT INTO terms (code, name, starts_on, ends_on, is_current, created_at, updated_at)
		VALUES (?, ?, ?, ?, 1, NOW(3), NOW(3))`,
		termCode, "2026 春季学期",
		now.AddDate(0, -2, 0).Format("2006-01-02"),
		now.AddDate(0, 4, 0).Format("2006-01-02"),
	).Error; err != nil {
		return 0, err
	}
	if err := db.Raw("SELECT id FROM terms WHERE code = ? LIMIT 1", termCode).Scan(&id).Error; err != nil {
		return 0, err
	}
	return id, nil
}

func ensureRound(db *gorm.DB, termID int64) (int64, error) {
	var id int64
	err := db.Raw(`
		SELECT id FROM rounds
		 WHERE term_id = ? AND status = 1
		 ORDER BY opens_at DESC LIMIT 1`, termID).Scan(&id).Error
	if err != nil {
		return 0, err
	}

	// 时间窗覆盖前后 60 天：演示时无论什么时候跑，选课都是开放的
	opensAt := time.Now().AddDate(0, 0, -30)
	closesAt := time.Now().AddDate(0, 0, 30)

	if id > 0 {
		// 已存在就把它打开（原来多半是「未开放」，学生根本选不了课）
		if err := db.Exec(`
			UPDATE rounds
			   SET opens_at = ?, closes_at = ?, max_credits = ?, allow_grade = 0, status = 1
			 WHERE id = ?`,
			opensAt, closesAt, maxCredits, id).Error; err != nil {
			return 0, err
		}
		// 只留一个开放轮次：两个同时开放会让「当前轮次」变得不确定
		if err := db.Exec(
			"UPDATE rounds SET status = 2 WHERE term_id = ? AND id <> ?", termID, id).Error; err != nil {
			return 0, err
		}
		return id, nil
	}

	if err := db.Exec(`
		INSERT INTO rounds (term_id, name, opens_at, closes_at, max_credits, allow_grade, status, created_at)
		VALUES (?, ?, ?, ?, ?, 0, 1, NOW(3))`,
		termID, "第一轮选课", opensAt, closesAt, maxCredits).Error; err != nil {
		return 0, err
	}
	if err := db.Raw("SELECT id FROM rounds WHERE term_id = ? ORDER BY id DESC LIMIT 1", termID).Scan(&id).Error; err != nil {
		return 0, err
	}
	return id, nil
}

// ---------------------------------------------------------------------------
// 用户
// ---------------------------------------------------------------------------
func ensureUser(db *gorm.DB, username, realName, roleCode string, grade int) (int64, error) {
	var id int64
	if err := db.Raw("SELECT id FROM users WHERE username = ? LIMIT 1", username).Scan(&id).Error; err != nil {
		return 0, err
	}

	if id == 0 {
		hash, err := bcrypt.GenerateFromPassword([]byte(demoPassword), bcrypt.MinCost)
		if err != nil {
			return 0, err
		}
		if err := db.Exec(`
			INSERT INTO users (username, password_hash, real_name, email, status, grade, created_at, updated_at)
			VALUES (?, ?, ?, '', 1, ?, NOW(3), NOW(3))`,
			username, string(hash), realName, grade).Error; err != nil {
			return 0, err
		}
		if err := db.Raw("SELECT id FROM users WHERE username = ? LIMIT 1", username).Scan(&id).Error; err != nil {
			return 0, err
		}
	}

	// 角色绑定：幂等
	if err := db.Exec(`
		INSERT IGNORE INTO user_roles (user_id, role_id, granted_at)
		SELECT ?, id, NOW(3) FROM roles WHERE code = ?`, id, roleCode).Error; err != nil {
		return 0, err
	}
	return id, nil
}

func ensureTeachers(db *gorm.DB, count int) ([]int64, error) {
	ids := make([]int64, 0, count)
	for i := 0; i < count; i++ {
		username := fmt.Sprintf("T100%d", i+1)
		id, err := ensureUser(db, username, teacherNames[i%len(teacherNames)], "teacher", 0)
		if err != nil {
			return nil, err
		}
		ids = append(ids, id)
	}
	return ids, nil
}

func ensureStudents(db *gorm.DB, count, grade int) ([]int64, error) {
	ids := make([]int64, 0, count)
	for i := 0; i < count; i++ {
		username := fmt.Sprintf("2024%03d", i+1)
		id, err := ensureUser(db, username, fmt.Sprintf("学生%02d", i+1), "student", grade)
		if err != nil {
			return nil, err
		}
		ids = append(ids, id)
	}
	return ids, nil
}

// ---------------------------------------------------------------------------
// 课程
// ---------------------------------------------------------------------------
func ensureCourses(db *gorm.DB) error {
	for _, c := range courses {
		if err := db.Exec(`
			INSERT IGNORE INTO courses
				(code, name, credits, department, description, status, created_at, updated_at)
			VALUES (?, ?, ?, ?, '', 1, NOW(3), NOW(3))`,
			c.Code, c.Name, c.Credits, c.Dept).Error; err != nil {
			return err
		}
	}
	return nil
}

// ---------------------------------------------------------------------------
// 班次
// ---------------------------------------------------------------------------
type offeringRef struct {
	ID       int64
	CourseID int64
	Code     string
	Credits  float64
	Capacity int
	Heat     float64
	Slots    []slotSeed
	IsNew    bool
}

func ensureOfferings(db *gorm.DB, termID int64, teacherIDs []int64, rng *rand.Rand) ([]offeringRef, error) {
	result := make([]offeringRef, 0, 40)
	slotCursor := 0

	for ci, c := range courses {
		var courseID int64
		if err := db.Raw("SELECT id FROM courses WHERE code = ? LIMIT 1", c.Code).Scan(&courseID).Error; err != nil {
			return nil, err
		}
		if courseID == 0 {
			return nil, fmt.Errorf("课程 %s 不存在（先确保 ensureCourses 成功）", c.Code)
		}

		for s := 1; s <= c.Sections; s++ {
			sectionNo := fmt.Sprintf("%02d", s)
			teacherID := teacherIDs[(ci+s)%len(teacherIDs)]
			location := locations[(ci+s)%len(locations)]

			var offeringID int64
			err := db.Raw(`
				SELECT id FROM offerings
				 WHERE term_id = ? AND course_id = ? AND section_no = ? LIMIT 1`,
				termID, courseID, sectionNo).Scan(&offeringID).Error
			if err != nil {
				return nil, err
			}

			isNew := offeringID == 0
			if isNew {
				if err := db.Exec(`
					INSERT INTO offerings
						(term_id, course_id, teacher_id, section_no, capacity, taken, location, status, created_at, updated_at)
					VALUES (?, ?, ?, ?, ?, 0, ?, 1, NOW(3), NOW(3))`,
					termID, courseID, teacherID, sectionNo, c.Capacity, location).Error; err != nil {
					return nil, err
				}
				if err := db.Raw(`
					SELECT id FROM offerings
					 WHERE term_id = ? AND course_id = ? AND section_no = ? LIMIT 1`,
					termID, courseID, sectionNo).Scan(&offeringID).Error; err != nil {
					return nil, err
				}
			}

			// 时间段：已有的不覆盖（可能是别人手动配的）
			var slotCount int
			if err := db.Raw("SELECT COUNT(*) FROM offering_slots WHERE offering_id = ?", offeringID).
				Scan(&slotCount).Error; err != nil {
				return nil, err
			}

			ownSlots := []slotSeed{}
			if slotCount == 0 {
				// 每个班次顺次取一个全局时段，同门课的两个班自然错开
				picked := timeSlots[slotCursor%len(timeSlots)]
				slotCursor++
				ownSlots = append(ownSlots, picked)
				if err := db.Exec(`
					INSERT INTO offering_slots (offering_id, weekday, start_period, end_period)
					VALUES (?, ?, ?, ?)`,
					offeringID, picked.Weekday, picked.Start, picked.End).Error; err != nil {
					return nil, err
				}
			} else {
				rows, err := db.Raw(`
					SELECT weekday, start_period, end_period FROM offering_slots
					 WHERE offering_id = ?`, offeringID).Rows()
				if err != nil {
					return nil, err
				}
				for rows.Next() {
					var s slotSeed
					if err := rows.Scan(&s.Weekday, &s.Start, &s.End); err != nil {
						_ = rows.Close()
						return nil, err
					}
					ownSlots = append(ownSlots, s)
				}
				_ = rows.Close()
			}

			result = append(result, offeringRef{
				ID:       offeringID,
				CourseID: courseID,
				Code:     c.Code,
				Credits:  c.Credits,
				Capacity: c.Capacity,
				Heat:     c.Heat,
				Slots:    ownSlots,
				IsNew:    isNew,
			})
		}
	}

	_ = rng // 目前没用随机，但保留参数以便后续加随机化
	return result, nil
}

func countNew(refs []offeringRef) int {
	n := 0
	for _, r := range refs {
		if r.IsNew {
			n++
		}
	}
	return n
}

// ---------------------------------------------------------------------------
// 模拟选课
// ---------------------------------------------------------------------------
type studentState struct {
	Credits float64
	Busy    map[[2]int]bool // (weekday, period)
}

func simulateEnrollments(
	db *gorm.DB,
	roundID int64,
	offerings []offeringRef,
	studentIDs []int64,
	rng *rand.Rand,
) (int, error) {
	states := make(map[int64]*studentState, len(studentIDs))
	for _, id := range studentIDs {
		states[id] = &studentState{Busy: map[[2]int]bool{}}
	}

	// 先去重：同一个学生不能选同一门课的两个班（现实里不允许，而且会让"我的课表"重复）
	// 保留 id 最小的那条。这一步是自愈的 —— 早期版本造出来的重复数据也会被清掉。
	if err := db.Exec(`
		DELETE e1 FROM enrollments e1
		JOIN enrollments e2  ON e2.student_id = e1.student_id AND e1.id > e2.id
		JOIN offerings   o1  ON o1.id = e1.offering_id
		JOIN offerings   o2  ON o2.id = e2.offering_id
		WHERE e1.status = 1 AND e2.status = 1
		  AND o1.course_id = o2.course_id`).Error; err != nil {
		return 0, err
	}

	// 再把已有的选课记录读进来，避免重复插入时状态算错
	existing := map[[2]int64]bool{}
	takenCourses := map[int64]map[int64]bool{}
	rows, err := db.Raw(`
		SELECT e.student_id, e.offering_id, o.course_id
		  FROM enrollments e
		  JOIN offerings o ON o.id = e.offering_id
		 WHERE e.status = 1`).Rows()
	if err != nil {
		return 0, err
	}
	for rows.Next() {
		var sid, oid, cid int64
		if err := rows.Scan(&sid, &oid, &cid); err != nil {
			_ = rows.Close()
			return 0, err
		}
		existing[[2]int64{sid, oid}] = true
		if takenCourses[sid] == nil {
			takenCourses[sid] = map[int64]bool{}
		}
		takenCourses[sid][cid] = true
	}
	_ = rows.Close()

	// 已有记录要还原学分与时间占用
	for _, off := range offerings {
		for _, sid := range studentIDs {
			if existing[[2]int64{sid, off.ID}] {
				st := states[sid]
				st.Credits += off.Credits
				for _, s := range off.Slots {
					for p := s.Start; p <= s.End; p++ {
						st.Busy[[2]int{s.Weekday, p}] = true
					}
				}
			}
		}
	}

	inserted := 0
	for _, off := range offerings {
		// 目标人数：按热度比例，但不低于 1
		target := int(float64(off.Capacity) * off.Heat)
		if target > len(studentIDs) {
			target = len(studentIDs)
		}
		if target < 1 {
			target = 1
		}

		already := 0
		for _, sid := range studentIDs {
			if existing[[2]int64{sid, off.ID}] {
				already++
			}
		}
		if already >= target {
			continue
		}

		// 打乱学生顺序，避免总是前几个人先选满
		order := rng.Perm(len(studentIDs))

		for _, idx := range order {
			if already >= target {
				break
			}
			sid := studentIDs[idx]
			if existing[[2]int64{sid, off.ID}] {
				continue
			}
			// 约束 0：同一门课只选一个班
			if takenCourses[sid][off.CourseID] {
				continue
			}
			st := states[sid]

			// 约束 1：学分上限
			if st.Credits+off.Credits > maxCredits {
				continue
			}
			// 约束 2：时间不能冲突
			conflict := false
			for _, s := range off.Slots {
				for p := s.Start; p <= s.End; p++ {
					if st.Busy[[2]int{s.Weekday, p}] {
						conflict = true
						break
					}
				}
				if conflict {
					break
				}
			}
			if conflict {
				continue
			}

			if err := db.Exec(`
				INSERT IGNORE INTO enrollments
					(student_id, offering_id, round_id, credits, status, created_at)
				VALUES (?, ?, ?, ?, 1, NOW(3))`,
				sid, off.ID, roundID, off.Credits).Error; err != nil {
				return inserted, err
			}

			existing[[2]int64{sid, off.ID}] = true
			if takenCourses[sid] == nil {
				takenCourses[sid] = map[int64]bool{}
			}
			takenCourses[sid][off.CourseID] = true
			st.Credits += off.Credits
			for _, s := range off.Slots {
				for p := s.Start; p <= s.End; p++ {
					st.Busy[[2]int{s.Weekday, p}] = true
				}
			}
			already++
			inserted++
		}
	}

	// taken 必须等于真实在选人数，否则余量全是错的
	if err := db.Exec(`
		UPDATE offerings o
		   SET o.taken = (
		       SELECT COUNT(*) FROM enrollments e
		        WHERE e.offering_id = o.id AND e.status = 1
		   )`).Error; err != nil {
		return inserted, err
	}

	return inserted, nil
}

// ---------------------------------------------------------------------------
// 汇总
// ---------------------------------------------------------------------------
func report(db *gorm.DB, termID int64) error {
	fmt.Println("\n---------------- 数据总览 ----------------")
	for _, item := range []struct{ label, sql string }{
		{"课程", "SELECT COUNT(*) AS n FROM courses"},
		{"班次", "SELECT COUNT(*) AS n FROM offerings WHERE term_id = ?"},
		{"选课记录", `SELECT COUNT(*) AS n FROM enrollments e
		              JOIN offerings o ON o.id = e.offering_id
		             WHERE e.status = 1 AND o.term_id = ?`},
		{"学生", `SELECT COUNT(*) AS n FROM users u
		          JOIN user_roles ur ON ur.user_id = u.id
		          JOIN roles r ON r.id = ur.role_id WHERE r.code = 'student'`},
	} {
		var n int
		var err error
		if item.label == "课程" || item.label == "学生" {
			err = db.Raw(item.sql).Scan(&n).Error
		} else {
			err = db.Raw(item.sql, termID).Scan(&n).Error
		}
		if err != nil {
			return err
		}
		fmt.Printf("  %-8s %d\n", item.label, n)
	}

	type hot struct {
		Code     string  `gorm:"column:code"`
		Name     string  `gorm:"column:name"`
		Section  string  `gorm:"column:section_no"`
		Taken    int     `gorm:"column:taken"`
		Capacity int     `gorm:"column:capacity"`
		FillRate float64 `gorm:"column:fill_rate"`
	}
	var rows []hot
	if err := db.Raw(`
		SELECT c.code, c.name, o.section_no, o.taken, o.capacity,
		       ROUND(o.taken / o.capacity * 100, 1) AS fill_rate
		  FROM offerings o
		  JOIN courses c ON c.id = o.course_id
		 WHERE o.term_id = ?
		 ORDER BY fill_rate DESC, o.taken DESC
		 LIMIT 8`, termID).Scan(&rows).Error; err != nil {
		return err
	}

	fmt.Println("\n---------------- 最抢手的 8 个班次 ----------------")
	fmt.Printf("  %-8s %-18s %-6s %-10s %s\n", "课程号", "课程名", "班号", "已选/容量", "占用率")
	for _, r := range rows {
		fmt.Printf("  %-8s %-18s %-6s %d/%-8d %.1f%%\n",
			r.Code, r.Name, r.Section, r.Taken, r.Capacity, r.FillRate)
	}
	return nil
}
