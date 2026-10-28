import { request } from '../request/client'
import type {
  CreateUserPayload,
  ListUsersResult,
  UpdateUserPayload,
  UserAdminVO,
  UserInfo,
} from './types'

/**
 * 管理员接口。
 *
 * 对应后端：
 *   GET   /admin/users      账号列表
 *   POST  /admin/users      建号（唯一能指定角色的入口）
 *   PATCH /admin/users/:id  编辑（姓名 / 邮箱 / 年级 / 启用禁用）
 *
 * 三个都要求「已登录 + admin 角色 + user:write 权限」。
 */
export const adminApi = {
  /** 账号列表，支持按角色与关键词过滤。 */
  users(query: {
    role?: string
    keyword?: string
    page?: number
    page_size?: number
  }): Promise<ListUsersResult> {
    return request<ListUsersResult>('/admin/users', {
      query: {
        role: query.role,
        keyword: query.keyword,
        page: query.page,
        page_size: query.page_size,
      },
    })
  },

  /** 建号。 */
  createUser(body: CreateUserPayload): Promise<UserInfo> {
    return request<UserInfo>('/admin/users', { method: 'POST', body })
  },

  /** 编辑账号：只传要改的字段。 */
  updateUser(userId: number, body: UpdateUserPayload): Promise<UserAdminVO> {
    return request<UserAdminVO>(`/admin/users/${userId}`, { method: 'PATCH', body })
  },
}
