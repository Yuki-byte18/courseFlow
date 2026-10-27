import type { ReactNode } from 'react'

import type { RoleCode } from '../api/types'

/**
 * 路由元信息：声明这条路由"谁能访问"。
 *
 * 注意：这只影响**前端要不要挂载这条路由**，是体验层面的控制。
 * 真正的安全边界在后端每个接口自己的鉴权——前端藏了路由，直接调接口照样要被拦。
 */
export interface RouteMeta {
  /** 菜单/页面标题，后续做面包屑和菜单用 */
  title?: string
  /** 允许访问的角色，命中任意一个即可 */
  roles?: RoleCode[]
  /** 允许访问的权限码，命中任意一个即可；与 roles 同时存在时以 roles 为准 */
  perms?: string[]
}

/**
 * 我们自己的路由类型。
 *
 * 不直接 extends react-router 的 RouteObject，因为它是个联合类型，
 * 无法通过 interface 扩展；同时我们需要额外的 meta 字段。
 * 交给 useRoutes 时做一次类型断言即可。
 */
export interface AppRoute {
  path?: string
  index?: boolean
  element?: ReactNode
  children?: AppRoute[]
  meta?: RouteMeta
}
