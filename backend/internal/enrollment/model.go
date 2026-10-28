// Package enrollment 是选课域：选课轮次、抢课、退课、我的课表。
//
// 表结构见 backend/migrations/0004_enrollment_schema.sql：
//
//	terms ──< rounds
//	enrollments >── users（学生）
//	            >── offerings
//	            >── rounds
//
// **这是整个系统唯一的高并发写路径。**
//
// M1 的做法（有意为之的"笨"版本）：
//
//	SELECT ... FOR UPDATE          -- 锁住班次行，串行化同一门课的抢课
//	UPDATE offerings SET taken=taken+1 WHERE id=? AND taken<capacity
//	INSERT INTO enrollments ...    -- 唯一索引兜底
//
// 这三步在单机 MySQL 下的极限就是 M1 基线。M2 会把扣减搬进 Redis Lua，
// 插入变成 Kafka 异步落库 —— 但「名额永不超卖」这条不变量一个字都不变。
package enrollment

import "time"

// 选课记录状态。
const (
	StatusActive  int8 = 1 // 有效
	StatusDropped int8 = 2 // 已退
)

// 轮次状态。
const (
	RoundStatusOpen   int8 = 1 // 启用
	RoundStatusClosed int8 = 2 // 关闭
)

// Round 对应 rounds 表：一轮选课的开放窗口与规则。
type Round struct {
	ID         int64     `gorm:"column:id;primaryKey"`
	TermID     int64     `gorm:"column:term_id"`
	Name       string    `gorm:"column:name"`
	OpensAt    time.Time `gorm:"column:opens_at"`
	ClosesAt   time.Time `gorm:"column:closes_at"`
	MaxCredits float64   `gorm:"column:max_credits"`
	AllowGrade int       `gorm:"column:allow_grade"`
	Status     int8      `gorm:"column:status"`
	CreatedAt  time.Time `gorm:"column:created_at"`
}

// TableName 显式指定表名，不依赖 GORM 的复数化推断。
func (Round) TableName() string { return "rounds" }

// IsOpenAt 判断某个时刻是否在本轮的开放窗口内。
func (r *Round) IsOpenAt(t time.Time) bool {
	return r.Status == RoundStatusOpen && !t.Before(r.OpensAt) && !t.After(r.ClosesAt)
}

// Enrollment 对应 enrollments 表。
//
// 注意 Credits 是**快照**：课程学分之后被改，也不影响这条历史记录的统计。
// DroppedAt 用指针：NULL 表示从未退过，和"零值时间"能区分开。
type Enrollment struct {
	ID         int64      `gorm:"column:id;primaryKey"`
	StudentID  int64      `gorm:"column:student_id"`
	OfferingID int64      `gorm:"column:offering_id"`
	RoundID    int64      `gorm:"column:round_id"`
	Credits    float64    `gorm:"column:credits"`
	Status     int8       `gorm:"column:status"`
	CreatedAt  time.Time  `gorm:"column:created_at"`
	DroppedAt  *time.Time `gorm:"column:dropped_at"`
}

// TableName 显式指定表名。
func (Enrollment) TableName() string { return "enrollments" }
