// Package perms 定义系统全部权限码常量。
//
// 权限码的唯一事实来源是数据库 permissions 表
// （见 backend/migrations/0002_permissions_seed.sql）。这里只是把同一批
// 字符串固化成常量。
//
// 为什么需要常量：权限码拼错不会报错，只会让鉴权永远返回 403，
// 排查起来极其痛苦。用常量可以让编译器帮你发现拼写错误。
package perms

// 课程与班次。
const (
	OfferingRead  = "offering:read"
	OfferingWrite = "offering:write"
	// OfferingImport 教师批量导入自己的授课课表。
	// 服务端会强制把 teacher_id 设为当前登录用户，所以它不等于"随便开班"。
	OfferingImport = "offering:import"
	CourseRead     = "course:read"
	CourseWrite    = "course:write"
)

// 学生端。
const (
	ScheduleRead     = "schedule:read"
	EnrollmentRead   = "enrollment:read"
	EnrollmentCreate = "enrollment:create"
	EnrollmentDrop   = "enrollment:drop"
)

// 管理员操作。
const (
	EnrollmentForceDrop  = "enrollment:force_drop"
	EnrollmentSupplement = "enrollment:supplement"
	RoundRead            = "round:read"
	RoundWrite           = "round:write"
	StockPreheat         = "stock:preheat"
	UserWrite            = "user:write"
)

// 教师端（只读）。
const (
	RosterRead   = "roster:read"
	RosterExport = "roster:export"
)

// 数据与 Agent。
const (
	DashboardRead = "dashboard:read"
	ETLRead       = "etl:read"
	AgentChat     = "agent:chat"
)

// All 返回全部权限码。
//
// 用途：种子数据校验、测试里生成「拥有全部权限」的 token。
// 如果新增了常量却忘了加进 All()，对应的单测会失败。
func All() []string {
	return []string{
		OfferingRead, OfferingWrite, OfferingImport, CourseRead, CourseWrite,
		ScheduleRead, EnrollmentRead, EnrollmentCreate, EnrollmentDrop,
		EnrollmentForceDrop, EnrollmentSupplement,
		RoundRead, RoundWrite, StockPreheat, UserWrite,
		RosterRead, RosterExport,
		DashboardRead, ETLRead, AgentChat,
	}
}
