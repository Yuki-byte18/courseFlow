// Package course 是课程域：学期、课程主数据、开课班次、上课时间段。
//
// 表结构见 backend/migrations/0003_course_schema.sql：
//
//	terms ──< offerings >── courses
//	            │  └──< offering_slots
//	            └── users（教师）
//
// 这个包是「读为主、写为辅」：学生端只读班次与余量，管理端才写。
// 真正的高并发写路径在 enrollment 域，不在这里。
package course

import "time"

// 班次状态。
const (
	OfferingStatusOpen    int8 = 1 // 开放选课
	OfferingStatusStopped int8 = 2 // 停开
)

// 课程状态。
const (
	CourseStatusActive   int8 = 1
	CourseStatusInactive int8 = 2
)

// Term 对应 terms 表。
type Term struct {
	ID        int64     `gorm:"column:id;primaryKey"`
	Code      string    `gorm:"column:code"`
	Name      string    `gorm:"column:name"`
	StartsOn  time.Time `gorm:"column:starts_on"`
	EndsOn    time.Time `gorm:"column:ends_on"`
	IsCurrent int8      `gorm:"column:is_current"`
}

// TableName 显式指定表名，不依赖 GORM 的复数化推断。
func (Term) TableName() string { return "terms" }

// Course 对应 courses 表：课程主数据。
//
// 一门课（如「数据结构」）可以开多个班次，班次才承载容量。
type Course struct {
	ID          int64     `gorm:"column:id;primaryKey"`
	Code        string    `gorm:"column:code"`
	Name        string    `gorm:"column:name"`
	Credits     float64   `gorm:"column:credits"`
	Department  string    `gorm:"column:department"`
	Description string    `gorm:"column:description"`
	Status      int8      `gorm:"column:status"`
	CreatedAt   time.Time `gorm:"column:created_at"`
	UpdatedAt   time.Time `gorm:"column:updated_at"`
}

// TableName 显式指定表名。
func (Course) TableName() string { return "courses" }

// Offering 对应 offerings 表：开课班次，是名额的载体。
//
// Taken 是整个系统最热的字段：每次抢课、退课都会改它。
// M2 会把它镜像进 Redis，但「take 只能 <= capacity」这条不变量不变。
type Offering struct {
	ID        int64     `gorm:"column:id;primaryKey"`
	TermID    int64     `gorm:"column:term_id"`
	CourseID  int64     `gorm:"column:course_id"`
	TeacherID int64     `gorm:"column:teacher_id"`
	SectionNo string    `gorm:"column:section_no"`
	Capacity  int       `gorm:"column:capacity"`
	Taken     int       `gorm:"column:taken"`
	Location  string    `gorm:"column:location"`
	Status    int8      `gorm:"column:status"`
	CreatedAt time.Time `gorm:"column:created_at"`
	UpdatedAt time.Time `gorm:"column:updated_at"`
}

// TableName 显式指定表名。
func (Offering) TableName() string { return "offerings" }

// OfferingSlot 对应 offering_slots 表：一个班次的上课时间段。
type OfferingSlot struct {
	ID          int64 `gorm:"column:id;primaryKey"`
	OfferingID  int64 `gorm:"column:offering_id"`
	Weekday     int   `gorm:"column:weekday"`
	StartPeriod int   `gorm:"column:start_period"`
	EndPeriod   int   `gorm:"column:end_period"`
}

// TableName 显式指定表名。
func (OfferingSlot) TableName() string { return "offering_slots" }

// Slot 是时间段的值对象，对外输出用。
type Slot struct {
	Weekday     int `json:"weekday"`      // 1=周一 … 7=周日
	StartPeriod int `json:"start_period"` // 起始节次
	EndPeriod   int `json:"end_period"`   // 结束节次
}

// Remaining 返回剩余名额。
//
// 用 max(0, ...) 兜底：数据库有 CHECK 约束保证 taken <= capacity，
// 但读路径不该因为脏数据给前端返回负数。
func (o *Offering) Remaining() int {
	left := o.Capacity - o.Taken
	if left < 0 {
		return 0
	}
	return left
}
