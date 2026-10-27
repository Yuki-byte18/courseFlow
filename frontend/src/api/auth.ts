import { request } from '../request/client'
import type { AuthResponse, LoginPayload, RegisterPayload, UserInfo } from './types'

/**
 * 身份域接口。
 *
 * 对应规格：specs/002-auth/openapi.yaml
 * 所有角色/权限判断只在后端生效，这里拿到的 roles/perms 仅用于前端显隐。
 */
export const authApi = {
  /** POST /auth/login 登录，返回 token 和用户信息。 */
  login(payload: LoginPayload): Promise<AuthResponse> {
    return request<AuthResponse>('/auth/login', {
      method: 'POST',
      body: payload,
      auth: false,
    })
  },

  /** POST /auth/register 注册学生账号，成功后同样返回 token。 */
  register(payload: RegisterPayload): Promise<AuthResponse> {
    return request<AuthResponse>('/auth/register', {
      method: 'POST',
      body: payload,
      auth: false,
    })
  },

  /** GET /me 当前登录用户。用于刷新页面后恢复登录态。 */
  me(): Promise<UserInfo> {
    return request<UserInfo>('/me')
  },

  /**
   * POST /auth/logout 退出登录。
   *
   * JWT 是无状态的，服务端不会让 token 立刻失效，这里只是记审计日志。
   * 真正生效靠前端清掉本地 token。
   */
  logout(): Promise<void> {
    return request<void>('/auth/logout', { method: 'POST' })
  },
}
