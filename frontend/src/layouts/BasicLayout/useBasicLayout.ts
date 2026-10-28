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

/** 角色优先级：一个人有多个角色时，界面按权限最大的那个展示 */
const ROLE_PRIORITY = ['admin', 'teacher', 'student'] as const

export interface NavItem {
  to: string
  label: string
}

/** 各端的导航项。key 是"主角色"。 */
const NAV_BY_ROLE: Record<string, NavItem[]> = {
  student: [
    { to: '/student', label: '选课台' },
    { to: '/student/schedule', label: '我的课表' },
  ],
  teacher: [{ to: '/teacher', label: '我的班次' }],
  admin: [
    { to: '/admin', label: '课程' },
    { to: '/admin/terms', label: '学期' },
    { to: '/admin/offerings', label: '班次' },
    { to: '/admin/rounds', label: '选课轮次' },
    { to: '/admin/users', label: '账号' },
  ],
}

/**
 * BasicLayout 的逻辑。
 *
 * 按"逻辑与 UI 分离"的约定，组件里只留 JSX，
 * 取数、跳转、导航项计算都在这里。
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

  /** 主角色：决定导航项和标签显示哪个身份 */
  const primaryRole = useMemo(() => {
    if (!user) return ''
    for (const role of ROLE_PRIORITY) {
      if (user.roles.includes(role)) return role
    }
    return user.roles[0] ?? ''
  }, [user])

  const navItems = useMemo(() => NAV_BY_ROLE[primaryRole] ?? [], [primaryRole])

  const roleLabel = primaryRole ? (ROLE_LABELS[primaryRole] ?? primaryRole) : ''

  return { user, roleLabel, navItems, onLogout, loggingOut }
}
