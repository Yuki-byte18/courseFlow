import { create } from 'zustand'

import type { UserInfo } from '../api/types'
import { getToken, removeToken, saveToken } from '../utils/storage'
import { resetAgentRun } from './agentRun'
import { clearUserScopedCache } from './queryClient'

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

export const useAuthStore = create<AuthState>()((set, get) => ({
  // 初始 token 直接从本地存储读，这样刷新页面不会瞬间显示未登录
  token: getToken(),
  user: null,

  setAuth: (token, user) => {
    // 换账号（含首次登录）都必须清掉上一个账号的会话态。
    // 这一步漏了就会出现"B 登录后看到 A 的对话"。
    if (get().user?.user_id !== user.user_id) {
      resetAgentRun()
      clearUserScopedCache()
    }
    saveToken(token)
    set({ token, user })
  },

  setUser: (user) => {
    // /me 刷新时 id 相同，不触发清理 —— 否则刷新页面会把正在跑的对话清掉
    const previous = get().user
    if (previous && previous.user_id !== user.user_id) {
      resetAgentRun()
      clearUserScopedCache()
    }
    set({ user })
  },

  clear: () => {
    removeToken()
    // 退出登录必须连带清掉 Agent 会话与查询缓存，否则下一个人登录就看到上一个人的数据
    resetAgentRun()
    clearUserScopedCache()
    set({ token: null, user: null })
  },
}))
