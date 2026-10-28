/**
 * 权限码常量，必须与后端 internal/platform/perms/perms.go 保持一致。
 *
 * 为什么前端也要一份：按钮级的显隐和禁用要靠它判断。
 * 这只是**体验**层面的控制 —— 真正的拦截在后端每个接口上，
 * 前端藏了按钮，直接调接口照样 403。
 */
export const PERMS = {
  // 课程与班次
  offeringRead: 'offering:read',
  offeringWrite: 'offering:write',
  offeringImport: 'offering:import',
  courseRead: 'course:read',
  courseWrite: 'course:write',

  // 学生端
  scheduleRead: 'schedule:read',
  enrollmentRead: 'enrollment:read',
  enrollmentCreate: 'enrollment:create',
  enrollmentDrop: 'enrollment:drop',

  // 轮次与账号
  roundRead: 'round:read',
  roundWrite: 'round:write',
  userWrite: 'user:write',

  // 名单
  rosterRead: 'roster:read',
  rosterExport: 'roster:export',

  // 数据与 Agent
  dashboardRead: 'dashboard:read',
  etlRead: 'etl:read',
  agentChat: 'agent:chat',
} as const

/** 权限码的字面量类型 */
export type PermCode = (typeof PERMS)[keyof typeof PERMS]
