import { useAuthStore } from '../store/auth'
import type { RoleCode, UserInfo } from '../api/types'

/** 当前登录用户；未登录为 null。 */
export function useCurrentUser(): UserInfo | null {
  return useAuthStore((state) => state.user)
}

/** 是否已登录（有 token 即视为已登录，用户信息在 bootstrap 阶段补齐）。 */
export function useIsAuthenticated(): boolean {
  return useAuthStore((state) => state.token !== null)
}

/**
 * 当前用户是否拥有任意一个给定角色。
 *
 * 只用于控制**显隐**，不用于安全判断。
 * 例：`const isAdmin = useHasRole('admin')` 决定要不要渲染"账号管理"入口。
 */
export function useHasRole(...roles: RoleCode[]): boolean {
  const user = useAuthStore((state) => state.user)
  if (!user) return false
  return roles.some((role) => user.roles.includes(role))
}

/**
 * 当前用户是否拥有任意一个给定权限码。
 *
 * 例：`const canWrite = useHasPerm(perms.OfferingWrite)`
 */
export function useHasPerm(...perms: string[]): boolean {
  const user = useAuthStore((state) => state.user)
  if (!user) return false
  return perms.some((perm) => user.perms.includes(perm))
}
