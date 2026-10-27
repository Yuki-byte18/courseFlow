import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'

import { authApi } from '../api/auth'
import { useAuthStore } from '../store/auth'

/**
 * 应用启动时恢复登录态。
 *
 * 场景：用户刷新页面或重新打开标签页。
 * 这时 localStorage 里有 token，但内存里没有用户信息（我们刻意不持久化 user，
 * 免得把过期的角色/权限当作可信数据）。所以要拿 token 去换一次 /me。
 *
 * 三种结果：
 *   没有 token        → 直接 ready，路由表只有静态路由
 *   有 token 且拉成功  → 写入 user，动态路由挂载
 *   有 token 但拉失败  → 清空登录态（401 已在 request 层触发清 token），退回未登录
 *
 * 返回 ready 表示"可以渲染路由了"，避免闪现不该出现的页面。
 */
export function useAuthBootstrap(): { ready: boolean } {
  const token = useAuthStore((state) => state.token)
  const user = useAuthStore((state) => state.user)
  const setUser = useAuthStore((state) => state.setUser)
  const clear = useAuthStore((state) => state.clear)

  const query = useQuery({
    // token 进 key：换了 token 就是一次新的身份查询
    queryKey: ['auth', 'me', token],
    queryFn: () => authApi.me(),
    // 只有"有 token 但还没有用户信息"时才需要查
    enabled: Boolean(token) && user === null,
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
  })

  useEffect(() => {
    if (query.data) {
      setUser(query.data)
    }
  }, [query.data, setUser])

  useEffect(() => {
    if (query.isError) {
      // token 失效或服务异常：全部退回未登录，让用户重新登录
      clear()
    }
  }, [query.isError, clear])

  // 没有 token，或已经有用户信息，就算就绪
  const ready = !token || user !== null

  return { ready }
}
