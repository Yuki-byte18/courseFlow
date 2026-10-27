import { LoginPage } from '../pages/login'
import { NotFoundPage } from '../pages/not-found'
import { RegisterPage } from '../pages/register'
import { RoleLanding } from './RoleLanding'
import type { AppRoute } from './types'

/**
 * 静态路由：**未登录也要挂载**的路由。
 *
 * 只有登录、注册、根路径跳转，以及最后兜底的 404。
 * 任何需要身份或权限的页面都不该出现在这里。
 */
export const staticRoutes: AppRoute[] = [
  { path: '/login', element: <LoginPage /> },
  { path: '/register', element: <RegisterPage /> },
  { path: '/', element: <RoleLanding /> },
]

/**
 * 404 必须单独放在路由表**最后**，
 * 否则 `*` 会把后面所有动态路由都吃掉。
 */
export const notFoundRoute: AppRoute = {
  path: '*',
  element: <NotFoundPage />,
}
