import { useCallback, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { authApi } from '../../api/auth'
import { useAuthStore } from '../../store/auth'

/** 角色码 → 展示名 */
const ROLE_LABELS: Record<string, string> = {
  admin: '管理员',
  teacher: '教师',
  student: '学生',
}

/**
 * BasicLayout 的逻辑。
 *
 * 按"逻辑与 UI 分离"的约定，组件里只留 JSX，
 * 取数、跳转、状态都放在这里。
 */
export function useBasicLayout() {
  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)
  const clear = useAuthStore((state) => state.clear)
  const [loggingOut, setLoggingOut] = useState(false)

  /** 退出登录：先通知服务端（记审计），无论成败都清本地登录态。 */
  const onLogout = useCallback(async () => {
    setLoggingOut(true)
    try {
      await authApi.logout()
    } catch {
      // 服务端不可用也必须能退出：JWT 无状态，真正的"退出"就是丢掉本地 token
    } finally {
      clear()
      setLoggingOut(false)
      navigate('/login', { replace: true })
    }
  }, [clear, navigate])

  /** 展示用角色名：一个人可能有多个角色，显示权限最大的那个 */
  const roleLabel = useMemo(() => {
    if (!user) return ''
    const priority = ['admin', 'teacher', 'student']
    for (const role of priority) {
      if (user.roles.includes(role)) {
        return ROLE_LABELS[role] ?? role
      }
    }
    return user.roles[0] ?? ''
  }, [user])

  return { user, roleLabel, onLogout, loggingOut }
}
