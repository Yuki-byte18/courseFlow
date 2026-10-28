package course

// CreateTermRequest 是管理员新建学期的入参。
type CreateTermRequest struct {
	Code      string `json:"code" binding:"required,min=2,max=16"`
	Name      string `json:"name" binding:"required,min=1,max=64"`
	StartsOn  string `json:"starts_on" binding:"required"` // YYYY-MM-DD
	EndsOn    string `json:"ends_on" binding:"required"`
	IsCurrent bool   `json:"is_current"`
}

// CreateCourseRequest 是管理员新建课程的入参。
type CreateCourseRequest struct {
	Code        string  `json:"code" binding:"required,min=2,max=32"`
	Name        string  `json:"name" binding:"required,min=1,max=128"`
	Credits     float64 `json:"credits" binding:"required,gt=0,lte=20"`
	Department  string  `json:"department" binding:"omitempty,max=64"`
	Description string  `json:"description" binding:"omitempty,max=512"`
}

// CreateSlotRequest 是班次的一个上课时间段。
type CreateSlotRequest struct {
	Weekday     int `json:"weekday" binding:"required,min=1,max=7"`
	StartPeriod int `json:"start_period" binding:"required,min=1,max=12"`
	EndPeriod   int `json:"end_period" binding:"required,min=1,max=12"`
}

// CreateOfferingRequest 是管理员开班的入参。
type CreateOfferingRequest struct {
	TermID    int64               `json:"term_id" binding:"required,gt=0"`
	CourseID  int64               `json:"course_id" binding:"required,gt=0"`
	TeacherID int64               `json:"teacher_id" binding:"required,gt=0"`
	SectionNo string              `json:"section_no" binding:"omitempty,max=16"`
	Capacity  int                 `json:"capacity" binding:"required,gt=0,lte=10000"`
	Location  string              `json:"location" binding:"omitempty,max=64"`
	Slots     []CreateSlotRequest `json:"slots" binding:"omitempty,max=10"`
}

// UpdateCourseRequest 是编辑课程的入参。
//
// 全部字段用指针：只有传了的才更新。
// 不能用零值判断"要不要改" —— 0 和"没传"区分不开。
type UpdateCourseRequest struct {
	Name        *string  `json:"name" binding:"omitempty,min=1,max=128"`
	Credits     *float64 `json:"credits" binding:"omitempty,gt=0,lte=20"`
	Department  *string  `json:"department" binding:"omitempty,max=64"`
	Description *string  `json:"description" binding:"omitempty,max=512"`
	Status      *int8    `json:"status" binding:"omitempty,oneof=1 2"`
}

// UpdateOfferingSlotRequest 是编辑班次时的一个时间段。
type UpdateOfferingSlotRequest struct {
	Weekday     int `json:"weekday" binding:"required,min=1,max=7"`
	StartPeriod int `json:"start_period" binding:"required,min=1,max=12"`
	EndPeriod   int `json:"end_period" binding:"required,min=1,max=12"`
}

// UpdateOfferingRequest 是编辑班次的入参。
//
// Slots 传入非 nil 时会**整体替换**该班次的时间段（先删后插）：
// 逐条增删的接口对前端太复杂，而时间段最多十条，整体替换更简单也更不容易出错。
// 传 nil（即 JSON 里不带 slots 字段）表示不动时间段。
type UpdateOfferingRequest struct {
	Capacity *int                        `json:"capacity" binding:"omitempty,gt=0,lte=10000"`
	Location *string                     `json:"location" binding:"omitempty,max=64"`
	Status   *int8                       `json:"status" binding:"omitempty,oneof=1 2"`
	Slots    []UpdateOfferingSlotRequest `json:"slots" binding:"omitempty,max=10,dive"`
}

// OfferingQuery 是班次列表的查询条件。
type OfferingQuery struct {
	TermID     int64
	Department string
	Keyword    string
	// TeacherID 非 0 时只返回该教师开设的班次。
	// 教师端的「我的班次」就是把它设成当前登录用户的 ID —— 身份来自 token，不接受前端传参。
	TeacherID int64
	Page      int
	PageSize  int
}

// ImportRowRequest 是导入课表的一行。
//
// 全部字段都是扁平的基础类型：导入的数据来自 CSV 或手工粘贴，
// 出现嵌套结构只会让前端解析更麻烦。
type ImportRowRequest struct {
	CourseCode  string `json:"course_code" binding:"required,max=32"`
	SectionNo   string `json:"section_no" binding:"omitempty,max=16"`
	Capacity    int    `json:"capacity" binding:"required,gt=0,lte=10000"`
	Location    string `json:"location" binding:"omitempty,max=64"`
	Weekday     int    `json:"weekday" binding:"omitempty,min=1,max=7"`
	StartPeriod int    `json:"start_period" binding:"omitempty,min=1,max=12"`
	EndPeriod   int    `json:"end_period" binding:"omitempty,min=1,max=12"`
}

// ImportOfferingsRequest 是教师批量导入课表的入参。
//
// 注意这里**没有 teacher_id**：服务端会强制把每一条的教师设成当前登录用户。
// 即使调用方伪造这个字段也不会生效。
type ImportOfferingsRequest struct {
	TermID int64              `json:"term_id" binding:"required,gt=0"`
	Rows   []ImportRowRequest `json:"rows" binding:"required,min=1,max=200,dive"`
}

// 分页默认值与上限。
const (
	defaultPage     = 1
	defaultPageSize = 20
	maxPageSize     = 100
)

// Normalize 把分页参数收敛到合法范围。
//
// 放在 dto 里而不是 controller：这样任何调用方（包括种子脚本、
// 将来的 Agent 工具）拿到的都是同一套规则。
func (q *OfferingQuery) Normalize() {
	if q.Page < 1 {
		q.Page = defaultPage
	}
	if q.PageSize < 1 {
		q.PageSize = defaultPageSize
	}
	if q.PageSize > maxPageSize {
		q.PageSize = maxPageSize
	}
}
