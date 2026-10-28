package enrollment

// CreateRoundRequest 是管理员新建选课轮次的入参。
type CreateRoundRequest struct {
	TermID     int64   `json:"term_id" binding:"required,gt=0"`
	Name       string  `json:"name" binding:"required,min=1,max=64"`
	OpensAt    string  `json:"opens_at" binding:"required"` // RFC3339 或 YYYY-MM-DD
	ClosesAt   string  `json:"closes_at" binding:"required"`
	MaxCredits float64 `json:"max_credits" binding:"required,gt=0,lte=60"`
	AllowGrade int     `json:"allow_grade" binding:"omitempty,min=0,max=8"`
}

// EnrollRequest 是抢课入参。
//
// 只有 offering_id：学生身份从 JWT 拿，绝不接受请求体里的 student_id。
type EnrollRequest struct {
	OfferingID int64 `json:"offering_id" binding:"required,gt=0"`
}

// MyEnrollmentQuery 是我的选课列表的查询条件。
type MyEnrollmentQuery struct {
	TermID int64
	// IncludeDropped 是否包含已退课的记录（默认不包含）
	IncludeDropped bool
}
