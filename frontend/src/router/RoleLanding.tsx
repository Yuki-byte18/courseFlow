import { Navigate } from 'react-router-dom'

import { useAuthStore } from '../store/auth'
import { homePathFor } from './access'

/**
 * `/` 的落点。
 *
 * 未登录 → 登录页；已登录 → 按角色找到自己的工作台。
 * 单独做成组件而不是写死在路由表里，是因为它需要读登录态。
 */
export function RoleLanding() {
  const user = useAuthStore((state) => state.user)
  return <Navigate to={homePathFor(user)} replace />
}
