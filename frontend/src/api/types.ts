/**
 * 与后端契约对应的类型。
 *
 * 手工维护，字段来源是 specs/002-auth/openapi.yaml。
 * 后续可以用 openapi-typescript 从这里生成 types.gen.ts，替换掉手写部分。
 */

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
