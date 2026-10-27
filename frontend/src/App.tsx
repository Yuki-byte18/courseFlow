import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'

import { PageLoading } from './components/PageLoading'
import { useAuthBootstrap } from './hooks/useAuthBootstrap'
import { setUnauthorizedHandler } from './request/client'
import { AppRouter } from './router'
import { useAuthStore } from './store/auth'

/**
 * 应用根组件。
 *
 * 职责只有两件：
 * 1. 启动时恢复登录态（见 useAuthBootstrap），恢复完之前不渲染路由，
 *    避免"先闪现登录页再跳工作台"的抖动；
 * 2. 注册全局 401 处理器：任何请求凭据失效，统一清态并回登录页。
 */
export function App() {
  const { ready } = useAuthBootstrap()
  const navigate = useNavigate()
  const clear = useAuthStore((state) => state.clear)

  useEffect(() => {
    setUnauthorizedHandler(() => {
      // 只清登录态并跳转，不在这里弹提示：
      // token 过期是常态，用户看到登录页就知道要重新登录了
      clear()
      navigate('/login', { replace: true })
    })
    return () => setUnauthorizedHandler(null)
  }, [clear, navigate])

  if (!ready) {
    return <PageLoading fullscreen tip="正在恢复登录状态…" />
  }

  return <AppRouter />
}
