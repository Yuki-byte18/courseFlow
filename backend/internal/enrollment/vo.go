package enrollment

import "time"

// RoundVO 是当前轮次的对外视图。
//
// UsedCredits / Open / Reason 都是后端算好的：
// 前端不该自己拿 opens_at 和 now 比，否则每台机器的时钟都不一样。
type RoundVO struct {
	RoundID     int64     `json:"round_id"`
	TermID      int64     `json:"term_id"`
	Name        string    `json:"name"`
	OpensAt     time.Time `json:"opens_at"`
	ClosesAt    time.Time `json:"closes_at"`
	MaxCredits  float64   `json:"max_credits"`
	UsedCredits float64   `json:"used_credits"`
	Open        bool      `json:"open"`
	// Reason 说明当前为什么不能选课；能选时为空串
	Reason string `json:"reason"`
}

// EnrollVO 是抢课成功的响应。
//
// Taken / Remaining 直接返回，前端点完按钮就能就地更新余量，不用再请求一次列表。
type EnrollVO struct {
	EnrollmentID int64   `json:"enrollment_id"`
	OfferingID   int64   `json:"offering_id"`
	CourseName   string  `json:"course_name"`
	Credits      float64 `json:"credits"`
	Capacity     int     `json:"capacity"`
	Taken        int     `json:"taken"`
	Remaining    int     `json:"remaining"`
}

// SlotVO 是一个上课时间段。
//
// 刻意不在 enrollment 域里复用 course.Slot：两个域保持独立，
// 不因为一个三元组结构体就产生包依赖。
type SlotVO struct {
	Weekday     int `json:"weekday"`
	StartPeriod int `json:"start_period"`
	EndPeriod   int `json:"end_period"`
}

// MyEnrollmentVO 是「我的选课」里的一条。
type MyEnrollmentVO struct {
	EnrollmentID int64     `json:"enrollment_id"`
	OfferingID   int64     `json:"offering_id"`
	CourseCode   string    `json:"course_code"`
	CourseName   string    `json:"course_name"`
	Credits      float64   `json:"credits"`
	TeacherName  string    `json:"teacher_name"`
	SectionNo    string    `json:"section_no"`
	Location     string    `json:"location"`
	Slots        []SlotVO  `json:"slots"`
	EnrolledAt   time.Time `json:"enrolled_at"`
}

// ScheduleItemVO 是课表里的一节课（一个时间段一行）。
type ScheduleItemVO struct {
	CourseName  string `json:"course_name"`
	TeacherName string `json:"teacher_name"`
	Location    string `json:"location"`
	Weekday     int    `json:"weekday"`
	StartPeriod int    `json:"start_period"`
	EndPeriod   int    `json:"end_period"`
}

// ScheduleVO 是按星期几分好组的课表。
//
// 前端直接按 1..7 取数组渲染周视图，不用自己分组。
type ScheduleVO struct {
	TermID       int64                    `json:"term_id"`
	TotalCount   int                      `json:"total_count"`
	TotalCredits float64                  `json:"total_credits"`
	Days         map[int][]ScheduleItemVO `json:"days"`
}
