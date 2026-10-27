import { useMemo } from 'react'
import { useRoutes, type RouteObject } from 'react-router-dom'

import { useAuthStore } from '../store/auth'
import { filterRoutes } from './access'
import { dynamicRoutes } from './dynamicRoutes'
import { notFoundRoute, staticRoutes } from './staticRoutes'
import type { AppRoute } from './types'

/**
 * 应用路由出口。
 *
 * 动态路由方案说明：
 *
 *   ┌ 未登录 ─── 只挂 staticRoutes + 404
 *   └ 已登录 ─── staticRoutes + (按 roles/perms 过滤后的 dynamicRoutes) + 404
 *
 * 为什么用"状态驱动的路由树"而不是 router.patchRoutes：
 *
 *   patchRoutes 只能加不能删。用户退出登录后，之前挂上去的动态路由仍然存在，
 *   必须销毁并重建整个 router 才能清掉，容易留下"退出后还能进 /admin"的漏洞。
 *
 *   而这里路由树是 useMemo 从登录态算出来的：user 一变，树就重算，
 *   退出登录时动态路由自动消失。行为正确性由状态保证，不需要额外的清理代码。
 *
 * 注意：**无权访问的路由根本不会被挂载**，所以用户手输 /admin 会落到 404。
 * 这与后端"越权访问返回 404 而不是 403"的做法一致——不泄露资源是否存在。
 */
export function AppRouter() {
  const user = useAuthStore((state) => state.user)

  const routes = useMemo<AppRoute[]>(() => {
    const accessible = filterRoutes(dynamicRoutes, user)
    return [...staticRoutes, ...accessible, notFoundRoute]
  }, [user])

  // AppRoute 是我们自己的宽松类型，交回 react-router 需要一次断言
  return useRoutes(routes as unknown as RouteObject[])
}
