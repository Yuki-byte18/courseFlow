import { QueryClient } from '@tanstack/react-query'

/**
 * 全局唯一的 QueryClient。
 *
 * 为什么单独成模块而不是在 main.tsx 里内联：
 * **换账号时必须清空缓存**。缓存里全是上一个账号的数据（会话列表、运行记录、
 * 名单、课程列表……），如果只在 main.tsx 里 new 一个局部变量，
 * 退出登录的代码拿不到它，B 账号登录后会在 15 秒 staleTime 内看到 A 的数据。
 *
 * 默认值取舍：
 *   retry: 1              默认重试 3 次，对登录这类操作会放大失败等待
 *   refetchOnWindowFocus  教务系统不需要"切回标签页就刷新"，抢课页切回来发请求还会干扰压测
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 15_000,
    },
  },
})

/**
 * 清掉所有用户相关状态。
 *
 * 换账号 / 退出登录时**必须**调用，否则会串号。
 * 这里只清缓存，不动 store —— store 的清理在 auth store 里做，
 * 因为不同 store 的清理口径不一样（有的要保留主题、有的要保留语言偏好）。
 */
export function clearUserScopedCache(): void {
  queryClient.clear()
}
