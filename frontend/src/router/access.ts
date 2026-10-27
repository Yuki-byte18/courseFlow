import type { RoleCode, UserInfo } from '../api/types'
import type { AppRoute, RouteMeta } from './types'

/**
 * 判断当前用户能否访问一条路由。
 *
 * 规则：
 * - 没有 meta → 任何登录用户都能访问（公开路由不在此列，它们不进动态表）
 * - meta.roles → 命中任意一个角色即可
 * - meta.perms → 命中任意一个权限码即可（roles 优先）
 * - 未登录 → 一律 false
 */
export function canAccess(meta: RouteMeta | undefined, user: UserInfo | null): boolean {
  if (!meta) return true
  if (!user) return false

  const { roles, perms } = meta

  if (roles && roles.length > 0) {
    return roles.some((role) => user.roles.includes(role))
  }

  if (perms && perms.length > 0) {
    return perms.some((perm) => user.perms.includes(perm))
  }

  return true
}

/**
 * 递归过滤路由表，只留下当前用户能访问的条目。
 *
 * 子路由没有 meta 时会继承父路由的判定结果（父路由已经被筛过了）。
 */
export function filterRoutes(routes: AppRoute[], user: UserInfo | null): AppRoute[] {
  return routes
    .filter((route) => canAccess(route.meta, user))
    .map((route) =>
      route.children ? { ...route, children: filterRoutes(route.children, user) } : route,
    )
}

/**
 * 根据角色决定登录后落到哪个首页。
 *
 * 优先级 admin > teacher > student：一个人同时是教师和管理员时，
 * 进门先给他权限更大的那个工作台，避免他以为自己没权限。
 */
export function homePathFor(user: UserInfo | null): string {
  if (!user) return '/login'
  if (user.roles.includes('admin')) return '/admin'
  if (user.roles.includes('teacher')) return '/teacher'
  if (user.roles.includes('student')) return '/student'
  return '/login'
}

/** 该角色需要哪些角色的首页路径，供测试和调试用。 */
export function pathOfRole(role: RoleCode): string {
  return `/${role}`
}
