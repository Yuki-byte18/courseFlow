import { create } from 'zustand'

import type { UserInfo } from '../api/types'
import { getToken, removeToken, saveToken } from '../utils/storage'

/**
 * 全局登录态。
 *
 * 为什么用 zustand 而不是 Redux：这里只有"当前用户 + token"一个共享状态，
 * zustand 不需要 Provider、不需要 action/reducer 样板，跨页面读写足够。
 *
 * token 落盘在 localStorage（刷新页面能恢复登录），user 只放内存
 * （刷新后用 GET /me 重新拉，避免持久化过期权限）。
 */
interface AuthState {
  token: string | null
  user: UserInfo | null
  /** 登录/注册成功后写入 token 和用户。 */
  setAuth: (token: string, user: UserInfo) => void
  /** 仅更新用户（例如 /me 刷新后覆盖）。 */
  setUser: (user: UserInfo) => void
  /** 清空登录态（退出登录、401 时调用）。 */
  clear: () => void
}

export const useAuthStore = create<AuthState>()((set) => ({
  // 初始 token 直接从本地存储读，这样刷新页面不会瞬间显示未登录
  token: getToken(),
  user: null,

  setAuth: (token, user) => {
    saveToken(token)
    set({ token, user })
  },

  setUser: (user) => set({ user }),

  clear: () => {
    removeToken()
    set({ token: null, user: null })
  },
}))
