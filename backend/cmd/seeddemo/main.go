// Command seeddemo 生成压测与演示用的假数据。
//
// 为什么单独一个命令而不是挂在 cmd/seed 上：
//   - cmd/seed 是"生产也跑"的引导命令，只造一个管理员，必须幂等且极简
//   - 这个命令是"开发压测专用"，会写入几万行数据，不该出现在部署脚本里
//
// 全部用 INSERT IGNORE / NOT EXISTS 保证可重复执行：
// 跑第二遍不会报错，也不会重复插入。
//
// 用法：
//
//	go run ./cmd/seeddemo                    # 默认 50 课 / 200 班次 / 500 学生
//	go run ./cmd/seeddemo -offerings 2000    # 加大规模压测
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"strings"

	"go.uber.org/zap"
	"golang.org/x/crypto/bcrypt"
	"gorm.io/gorm"

	"courseflow/internal/platform/config"
	"courseflow/internal/platform/logger"
	"courseflow/internal/platform/mysql"
)

// 所有种子账号共用的密码。压测时用同一个密码登录即可。
const seedPassword = "Passw0rd123"

// 种子数据的标识前缀：靠它区分"假数据"和真实数据，也靠它做幂等判断。
const (
	termCode      = "SEED-2026S"
	teacherPrefix = "seed_teacher_"
	studentPrefix = "seed_stu_"
	courseCodeFmt = "D%03d"
)

// 课程名池：用真课名，压测结果和演示截图才像真的
var courseNames = []string{
	"数据结构", "操作系统", "计算机网络", "线性代数", "概率论与数理统计",
	"编译原理", "数据库系统", "软件工程", "算法设计与分析", "计算机组成原理",
	"离散数学", "人工智能导论", "机器学习", "分布式系统", "信息安全基础",
	"数字信号处理", "信号与系统", "电路分析", "电磁场与电磁波", "通信原理",
}

var departments = []string{"计算机学院", "软件学院", "数学学院", "电子学院"}

func main() {
	if err := run(); err != nil {
		fmt.Fprintf(os.Stderr, "seeddemo 失败: %v\n", err)
		os.Exit(1)
	}
}

func run() error {
	courseCount := flag.Int("courses", 50, "课程数量")
	offeringCount := flag.Int("offerings", 200, "班次数量")
	studentCount := flag.Int("students", 500, "学生数量")
	teacherCount := flag.Int("teachers", 8, "教师数量")
	capacity := flag.Int("capacity", 60, "每个班次的容量")
	flag.Parse()

	cfg, err := config.Load("")
	if err != nil {
		return err
	}

	log, err := logger.New(cfg.App.Env, cfg.Log.Level, cfg.Log.Format)
	if err != nil {
		return err
	}
	defer func() { _ = log.Sync() }()

	ctx := context.Background()

	db, err := mysql.New(ctx, cfg.MySQL, log)
	if err != nil {
		return err
	}

	// 所有种子账号共用一个 bcrypt 哈希：
	// 真给每个用户算一次要 500 × 70ms ≈ 35 秒，而压测并不关心盐是否唯一。
	hash, err := bcrypt.GenerateFromPassword([]byte(seedPassword), bcrypt.MinCost)
	if err != nil {
		return fmt.Errorf("生成密码哈希失败: %w", err)
	}

	termID, err := ensureTerm(ctx, db)
	if err != nil {
		return err
	}
	log.Info("学期就绪", zap.Int64("term_id", termID), zap.String("code", termCode))

	if err := ensureUsers(ctx, db, teacherPrefix, "教师", *teacherCount, string(hash), "teacher"); err != nil {
		return err
	}
	if err := ensureUsers(ctx, db, studentPrefix, "学生", *studentCount, string(hash), "student"); err != nil {
		return err
	}
	log.Info("账号就绪",
		zap.Int("teachers", *teacherCount),
		zap.Int("students", *studentCount),
		zap.String("password", seedPassword),
	)

	if err := ensureCourses(ctx, db, *courseCount); err != nil {
		return err
	}
	log.Info("课程就绪", zap.Int("courses", *courseCount))

	if err := ensureOfferings(ctx, db, termID, *offeringCount, *capacity); err != nil {
		return err
	}
	if err := ensureSlots(ctx, db); err != nil {
		return err
	}
	log.Info("班次就绪", zap.Int("offerings", *offeringCount), zap.Int("capacity", *capacity))

	if err := ensureRound(ctx, db, termID); err != nil {
		return err
	}

	// 汇总，方便直接对着数字设计压测
	var summary struct {
		Users     int64 `gorm:"column:users"`
		Courses   int64 `gorm:"column:courses"`
		Offerings int64 `gorm:"column:offerings"`
		Slots     int64 `gorm:"column:slots"`
		Capacity  int64 `gorm:"column:capacity"`
	}
	if err := db.WithContext(ctx).Raw(`
		SELECT (SELECT COUNT(*) FROM users)                                        AS users,
		       (SELECT COUNT(*) FROM courses)                                      AS courses,
		       (SELECT COUNT(*) FROM offerings)                                    AS offerings,
		       (SELECT COUNT(*) FROM offering_slots)                               AS slots,
		       (SELECT COALESCE(SUM(capacity), 0) FROM offerings)                  AS capacity`).
		Scan(&summary).Error; err != nil {
		return fmt.Errorf("统计失败: %w", err)
	}

	log.Info("===== 种子数据就绪 =====",
		zap.Int64("总用户", summary.Users),
		zap.Int64("课程", summary.Courses),
		zap.Int64("班次", summary.Offerings),
		zap.Int64("时间段", summary.Slots),
		zap.Int64("总名额", summary.Capacity),
		zap.String("女生账号示例", studentPrefix+"1"),
		zap.String("教师账号示例", teacherPrefix+"1"),
		zap.String("密码", seedPassword),
	)

	return nil
}

// ensureTerm 保证存在一个当前学期。
func ensureTerm(ctx context.Context, db *gorm.DB) (int64, error) {
	var id int64
	if err := db.WithContext(ctx).Raw(
		`SELECT id FROM terms WHERE code = ?`, termCode).Scan(&id).Error; err != nil {
		return 0, fmt.Errorf("查询学期失败: %w", err)
	}
	if id != 0 {
		return id, nil
	}

	// 先清掉别的"当前学期"标记，全库只能有一个
	if err := db.WithContext(ctx).Exec(`UPDATE terms SET is_current = 0 WHERE is_current = 1`).Error; err != nil {
		return 0, fmt.Errorf("重置当前学期失败: %w", err)
	}

	res := db.WithContext(ctx).Exec(`
		INSERT INTO terms (code, name, starts_on, ends_on, is_current)
		VALUES (?, '2026 春季学期', '2026-02-23 00:00:00', '2026-07-05 00:00:00', 1)`, termCode)
	if res.Error != nil {
		return 0, fmt.Errorf("写入学期失败: %w", res.Error)
	}
	if err := db.WithContext(ctx).Raw(
		`SELECT id FROM terms WHERE code = ?`, termCode).Scan(&id).Error; err != nil {
		return 0, fmt.Errorf("回查学期失败: %w", err)
	}
	return id, nil
}

// ensureUsers 批量创建带指定角色的账号，并绑定角色。
func ensureUsers(
	ctx context.Context,
	db *gorm.DB,
	prefix, label string,
	count int,
	passwordHash, roleCode string,
) error {
	if count <= 0 {
		return nil
	}

	rows := make([]string, 0, count)
	for i := 1; i <= count; i++ {
		rows = append(rows, fmt.Sprintf(
			"('%s%d', '%s', '%s%d', NULL, 1)",
			prefix, i, passwordHash, label, i,
		))
	}

	sql := `INSERT IGNORE INTO users (username, password_hash, real_name, email, status) VALUES `
	if err := insertBatches(ctx, db, sql, rows, 500); err != nil {
		return fmt.Errorf("写入 %s 账号失败: %w", label, err)
	}

	// 再统一绑定角色：这样无论账号是本次新建还是上次已存在，都能补上角色
	roleGrant := fmt.Sprintf(`
		INSERT IGNORE INTO user_roles (user_id, role_id)
		SELECT u.id, r.id
		  FROM users u
		  JOIN roles r ON r.code = '%s'
		 WHERE u.username LIKE '%s%%'`, roleCode, prefix)
	if err := db.WithContext(ctx).Exec(roleGrant).Error; err != nil {
		return fmt.Errorf("绑定 %s 角色失败: %w", label, err)
	}
	return nil
}

// ensureCourses 批量创建课程主数据。
func ensureCourses(ctx context.Context, db *gorm.DB, count int) error {
	if count <= 0 {
		return nil
	}

	rows := make([]string, 0, count)
	for i := 1; i <= count; i++ {
		name := courseNames[(i-1)%len(courseNames)]
		// 同一门课名后加序号，避免视觉上重名
		if i > len(courseNames) {
			name = fmt.Sprintf("%s(%d)", name, i/len(courseNames)+1)
		}
		dept := departments[(i-1)%len(departments)]
		// 学分在 1.0 ~ 4.0 之间，0.5 步进 —— 和真实教务一致
		credits := float64(1+i%7) * 0.5

		rows = append(rows, fmt.Sprintf(
			"('%s', '%s', %.1f, '%s', '', 1)",
			fmt.Sprintf(courseCodeFmt, i), name, credits, dept,
		))
	}

	sql := `INSERT IGNORE INTO courses (code, name, credits, department, description, status) VALUES `
	if err := insertBatches(ctx, db, sql, rows, 500); err != nil {
		return fmt.Errorf("写入课程失败: %w", err)
	}
	return nil
}

// ensureOfferings 批量开班：课程 × 教师 × 班号。
//
// 班号按「同一门课的第几个班」递增，保证 (term, course, section) 唯一。
func ensureOfferings(ctx context.Context, db *gorm.DB, termID int64, count, capacity int) error {
	if count <= 0 {
		return nil
	}

	var courses []struct {
		ID int64 `gorm:"column:id"`
	}
	if err := db.WithContext(ctx).Raw(
		`SELECT id FROM courses ORDER BY code LIMIT ?`, 1000).Scan(&courses).Error; err != nil {
		return fmt.Errorf("查询课程失败: %w", err)
	}
	if len(courses) == 0 {
		return fmt.Errorf("还没有课程，无法开班")
	}

	var teachers []struct {
		ID int64 `gorm:"column:id"`
	}
	if err := db.WithContext(ctx).Raw(
		`SELECT id FROM users WHERE username LIKE ? ORDER BY id`, teacherPrefix+"%").Scan(&teachers).Error; err != nil {
		return fmt.Errorf("查询教师失败: %w", err)
	}
	if len(teachers) == 0 {
		return fmt.Errorf("还没有教师，无法开班")
	}

	locations := []string{"一教 301", "一教 405", "二教 210", "二教 512", "三教 108"}

	rows := make([]string, 0, count)
	for i := 0; i < count; i++ {
		course := courses[i%len(courses)]
		teacher := teachers[i%len(teachers)]
		section := i/len(courses) + 1
		location := locations[i%len(locations)]

		rows = append(rows, fmt.Sprintf(
			"(%d, %d, %d, '%02d', %d, 0, '%s', 1)",
			termID, course.ID, teacher.ID, section, capacity, location,
		))
	}

	sql := `INSERT IGNORE INTO offerings
	        (term_id, course_id, teacher_id, section_no, capacity, taken, location, status) VALUES `
	if err := insertBatches(ctx, db, sql, rows, 500); err != nil {
		return fmt.Errorf("写入班次失败: %w", err)
	}
	return nil
}

// ensureSlots 给还没有时间段的班次补上，用 id 做确定性分布。
//
// 一条 INSERT ... SELECT 就能覆盖全部班次，比在 Go 里循环快两个数量级。
func ensureSlots(ctx context.Context, db *gorm.DB) error {
	err := db.WithContext(ctx).Exec(`
		INSERT INTO offering_slots (offering_id, weekday, start_period, end_period)
		SELECT o.id,
		       1 + MOD(o.id, 5),
		       1 + MOD(o.id, 3) * 3,
		       2 + MOD(o.id, 3) * 3
		  FROM offerings o
		 WHERE NOT EXISTS (SELECT 1 FROM offering_slots s WHERE s.offering_id = o.id)`).Error
	if err != nil {
		return fmt.Errorf("写入上课时间失败: %w", err)
	}
	return nil
}

// ensureRound 保证此刻有一个开放中的轮次，否则一抢课就返回 403。
func ensureRound(ctx context.Context, db *gorm.DB, termID int64) error {
	err := db.WithContext(ctx).Exec(`
		INSERT INTO rounds (term_id, name, opens_at, closes_at, max_credits, allow_grade, status)
		SELECT ?, '第一轮选课', NOW(3) - INTERVAL 1 HOUR, NOW(3) + INTERVAL 60 DAY, 30.0, 0, 1
		 WHERE NOT EXISTS (
			SELECT 1 FROM rounds
			 WHERE term_id = ? AND status = 1 AND opens_at <= NOW(3) AND closes_at >= NOW(3)
		 )`, termID, termID).Error
	if err != nil {
		return fmt.Errorf("写入选课轮次失败: %w", err)
	}
	return nil
}

// insertBatches 把一堆 VALUES 元组分批执行，避免单条 SQL 过长。
func insertBatches(ctx context.Context, db *gorm.DB, prefix string, rows []string, perBatch int) error {
	for start := 0; start < len(rows); start += perBatch {
		end := start + perBatch
		if end > len(rows) {
			end = len(rows)
		}
		sql := prefix + strings.Join(rows[start:end], ",")
		if err := db.WithContext(ctx).Exec(sql).Error; err != nil {
			return err
		}
	}
	return nil
}
