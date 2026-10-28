/**
 * 与后端契约对应的类型。
 *
 * 手工维护，字段来源是后端各域的 vo.go / openapi.yaml。
 * 后续可以用 openapi-typescript 自动生成，替换掉手写部分。
 */

// ---------------------------------------------------------------------------
// 身份
// ---------------------------------------------------------------------------

/** 后端返回的用户视图（GET /me、登录响应里的 user 字段）。 */
export interface UserInfo {
  user_id: number
  username: string
  real_name: string
  roles: string[]
  perms: string[]
}

/** 登录 / 注册成功的响应。 */
export interface AuthResponse {
  token: string
  expires_at: string
  user: UserInfo
}

/** 登录入参。 */
export interface LoginPayload {
  username: string
  password: string
}

/** 注册入参。 */
export interface RegisterPayload {
  username: string
  password: string
  real_name: string
  email?: string
}

/** 统一的错误响应体。 */
export interface ApiErrorPayload {
  error: {
    code: string
    message: string
    request_id: string
  }
}

/** 系统角色标识，与后端 roles.code 一致。 */
export type RoleCode = 'student' | 'teacher' | 'admin'

// ---------------------------------------------------------------------------
// 课程与班次
// ---------------------------------------------------------------------------

/** 一个上课时间段。 */
export interface Slot {
  weekday: number
  start_period: number
  end_period: number
}

/** 学期。 */
export interface TermVO {
  term_id: number
  code: string
  name: string
  starts_on: string
  ends_on: string
  is_current: boolean
}

/** 课程主数据。 */
export interface CourseVO {
  course_id: number
  code: string
  name: string
  credits: number
  department: string
  description: string
}

/** 开课班次。选课台的一行就是它。 */
export interface OfferingVO {
  offering_id: number
  term_id: number
  course_id: number
  course_code: string
  course_name: string
  credits: number
  department: string
  teacher_id: number
  teacher_name: string
  section_no: string
  capacity: number
  taken: number
  remaining: number
  location: string
  slots: Slot[]
}

/** 班次分页结果。 */
export interface OfferingPage {
  items: OfferingVO[]
  total: number
  page: number
  page_size: number
}

/** 班次列表的查询条件。 */
export interface OfferingQuery {
  term_id?: number
  department?: string
  keyword?: string
  page?: number
  page_size?: number
}

/** 选课名单里的一行。 */
export interface RosterEntry {
  enrollment_id: number
  student_no: string
  real_name: string
  enrolled_at: string
}

/** 选课名单。 */
export interface RosterVO {
  offering_id: number
  course_name: string
  section_no: string
  capacity: number
  taken: number
  entries: RosterEntry[]
}

/** 管理员开班的入参。 */
export interface CreateOfferingPayload {
  term_id: number
  course_id: number
  teacher_id: number
  section_no?: string
  capacity: number
  location?: string
  slots?: Array<{ weekday: number; start_period: number; end_period: number }>
}

/** 管理员新建学期的入参。 */
export interface CreateTermPayload {
  code: string
  name: string
  starts_on: string
  ends_on: string
  is_current?: boolean
}

/** 管理员新建课程的入参。 */
export interface CreateCoursePayload {
  code: string
  name: string
  credits: number
  department?: string
  description?: string
}

/** 编辑课程的入参。只传要改的字段。 */
export interface UpdateCoursePayload {
  name?: string
  credits?: number
  department?: string
  description?: string
  status?: number
}

/**
 * 编辑班次的入参。
 *
 * 传了 slots 就**整体替换**该班次的时间段；不传表示不动时间段。
 */
export interface UpdateOfferingPayload {
  capacity?: number
  location?: string
  status?: number
  slots?: Slot[]
}

/** 编辑选课轮次的入参。 */
export interface UpdateRoundPayload {
  name?: string
  opens_at?: string
  closes_at?: string
  max_credits?: number
  allow_grade?: number
  status?: number
}

/**
 * 「针对某个学生的某个班次」的操作入参（强制退课 / 补录）。
 *
 * 用**学号**而不是 user_id：教务手里有学号，没有数据库主键。
 */
export interface StudentOfferingPayload {
  student_no: string
  offering_id: number
}

// ---------------------------------------------------------------------------
// 选课
// ---------------------------------------------------------------------------

/** 当前轮次。open 与 reason 都由后端判定。 */
export interface RoundVO {
  round_id: number
  term_id: number
  name: string
  opens_at: string
  closes_at: string
  max_credits: number
  used_credits: number
  open: boolean
  reason: string
}

/** 抢课成功的响应。 */
export interface EnrollVO {
  enrollment_id: number
  offering_id: number
  course_name: string
  credits: number
  capacity: number
  taken: number
  remaining: number
}

/** 我的选课里的一条。 */
export interface MyEnrollmentVO {
  enrollment_id: number
  offering_id: number
  course_code: string
  course_name: string
  credits: number
  teacher_name: string
  section_no: string
  location: string
  slots: Slot[]
  enrolled_at: string
}

/** 课表的一节课。 */
export interface ScheduleItemVO {
  course_name: string
  teacher_name: string
  location: string
  weekday: number
  start_period: number
  end_period: number
}

/** 按星期几分组的课表。后端返回的 key 是字符串 "1".."7"。 */
export interface ScheduleVO {
  term_id: number
  total_count: number
  total_credits: number
  days: Record<string, ScheduleItemVO[]>
}

/** 管理员新建轮次的入参。 */
export interface CreateRoundPayload {
  term_id: number
  name: string
  opens_at: string
  closes_at: string
  max_credits: number
  allow_grade?: number
}

// ---------------------------------------------------------------------------
// 账号管理（管理员）
// ---------------------------------------------------------------------------

/** 账号管理页的一行。 */
export interface UserAdminVO {
  user_id: number
  username: string
  real_name: string
  email: string
  status: number
  /** 年级：0=未设置，1-5=大一..大五 */
  grade: number
  roles: string[]
  created_at: string
}

/** 编辑账号的入参。只传要改的字段。 */
export interface UpdateUserPayload {
  real_name?: string
  email?: string
  status?: number
  grade?: number
}

/** 账号列表的分页结果。 */
export interface ListUsersResult {
  items: UserAdminVO[]
  total: number
  page: number
  page_size: number
}

/** 管理员建号入参。roles 是唯一能指定角色的入口。 */
export interface CreateUserPayload {
  username: string
  password: string
  real_name: string
  email?: string
  roles: string[]
}

// ---------------------------------------------------------------------------
// 教师导入课表
// ---------------------------------------------------------------------------

/** 导入课表的一行。 */
export interface ImportRowPayload {
  course_code: string
  section_no?: string
  capacity: number
  location?: string
  weekday?: number
  start_period?: number
  end_period?: number
}

/** 导入单行的结果。 */
export interface ImportResultRow {
  index: number
  status: 'created' | 'skipped' | 'failed'
  offering_id: number
  message: string
}

/** 一次导入的整体结果。 */
export interface ImportResult {
  created: number
  skipped: number
  failed: number
  results: ImportResultRow[]
}
