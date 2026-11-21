import { useCallback, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { PERMS } from '../../api/perms'
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
  /**
   * 需要的权限码。
   *
   * 导航项按权限过滤，而不是按角色写死 ——
   * Agent 是三端共用的能力，用角色判断会变成三份重复配置，
   * 而且角色和权限一旦不同步（比如只管了权限没管角色）就会错。
   */
  perm?: string
}

/** 各端的导航项。key 是"主角色"。 */
const NAV_BY_ROLE: Record<string, NavItem[]> = {
  student: [
    { to: '/student', label: '选课台' },
    { to: '/student/schedule', label: '我的课表' },
    { to: '/agent', label: 'Agent', perm: PERMS.agentChat },
    { to: '/agent/trace', label: 'Trace', perm: PERMS.agentChat },
  ],
  teacher: [
    { to: '/teacher', label: '我的班次' },
    { to: '/agent', label: 'Agent', perm: PERMS.agentChat },
    { to: '/agent/trace', label: 'Trace', perm: PERMS.agentChat },
  ],
  admin: [
    { to: '/admin', label: '课程' },
    { to: '/admin/terms', label: '学期' },
    { to: '/admin/offerings', label: '班次' },
    { to: '/admin/rounds', label: '选课轮次' },
    { to: '/admin/users', label: '账号' },
    { to: '/agent', label: 'Agent', perm: PERMS.agentChat },
    { to: '/agent/trace', label: 'Trace', perm: PERMS.agentChat },
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
  const [aiModalOpen, setAiModalOpen] = useState(false)

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

  const perms = useMemo(() => user?.perms ?? [], [user])

  const navItems = useMemo(() => {
    const items = NAV_BY_ROLE[primaryRole] ?? []
    return items.filter((item) => !item.perm || perms.includes(item.perm))
  }, [primaryRole, perms])

  const roleLabel = primaryRole ? (ROLE_LABELS[primaryRole] ?? primaryRole) : ''

  /** 只有能用 Agent 的人才需要在菜单里看到「AI 设置」 */
  const canUseAgent = perms.includes(PERMS.agentChat)

  return {
    user,
    roleLabel,
    navItems,
    onLogout,
    loggingOut,
    canUseAgent,
    aiModalOpen,
    openAiModal: () => setAiModalOpen(true),
    closeAiModal: () => setAiModalOpen(false),
  }
}
