package course

import "time"

// TermVO 是学期的对外视图。
type TermVO struct {
	ID        int64     `json:"term_id"`
	Code      string    `json:"code"`
	Name      string    `json:"name"`
	StartsOn  time.Time `json:"starts_on"`
	EndsOn    time.Time `json:"ends_on"`
	IsCurrent bool      `json:"is_current"`
}

// CourseVO 是课程的对外视图。
type CourseVO struct {
	ID          int64   `json:"course_id"`
	Code        string  `json:"code"`
	Name        string  `json:"name"`
	Credits     float64 `json:"credits"`
	Department  string  `json:"department"`
	Description string  `json:"description"`
}

// OfferingVO 是班次的对外视图：选课台每一行就是它。
//
// Remaining 后端算好再返回，不让前端拿 capacity-taken 自己减 ——
// 将来 M2 余量来自 Redis 时，前端不用改。
type OfferingVO struct {
	ID          int64   `json:"offering_id"`
	TermID      int64   `json:"term_id"`
	CourseID    int64   `json:"course_id"`
	CourseCode  string  `json:"course_code"`
	CourseName  string  `json:"course_name"`
	Credits     float64 `json:"credits"`
	Department  string  `json:"department"`
	TeacherID   int64   `json:"teacher_id"`
	TeacherName string  `json:"teacher_name"`
	SectionNo   string  `json:"section_no"`
	Capacity    int     `json:"capacity"`
	Taken       int     `json:"taken"`
	Remaining   int     `json:"remaining"`
	Location    string  `json:"location"`
	Slots       []Slot  `json:"slots"`
}

// OfferingPageVO 是班次列表的分页结果。
type OfferingPageVO struct {
	Items    []OfferingVO `json:"items"`
	Total    int64        `json:"total"`
	Page     int          `json:"page"`
	PageSize int          `json:"page_size"`
}

// RosterEntryVO 是选课名单里的一行（教师 / 管理员可见）。
type RosterEntryVO struct {
	EnrollmentID int64     `json:"enrollment_id"`
	StudentNo    string    `json:"student_no"`
	RealName     string    `json:"real_name"`
	EnrolledAt   time.Time `json:"enrolled_at"`
}

// RosterVO 是名单的整体视图。
type RosterVO struct {
	OfferingID int64           `json:"offering_id"`
	CourseName string          `json:"course_name"`
	SectionNo  string          `json:"section_no"`
	Capacity   int             `json:"capacity"`
	Taken      int             `json:"taken"`
	Entries    []RosterEntryVO `json:"entries"`
}

// 导入单行的结果状态
const (
	ImportStatusCreated = "created"
	ImportStatusSkipped = "skipped" // 班次已存在，跳过
	ImportStatusFailed  = "failed"  // 数据有问题，未创建
)

// ImportRowResult 是导入时每一行的处理结果。
type ImportRowResult struct {
	Index      int    `json:"index"`
	Status     string `json:"status"`
	OfferingID int64  `json:"offering_id"`
	Message    string `json:"message"`
}

// ImportResultVO 是一次导入的整体结果。
//
// 保留逐行结果而不是只返回成功数：
// 教师导 40 行、成功 38 行时，必须能指出是哪两行出了问题。
type ImportResultVO struct {
	Created int               `json:"created"`
	Skipped int               `json:"skipped"`
	Failed  int               `json:"failed"`
	Results []ImportRowResult `json:"results"`
}
