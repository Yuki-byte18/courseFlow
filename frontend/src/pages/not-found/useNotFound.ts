import { useCallback } from 'react'
import { useNavigate } from 'react-router-dom'

import { homePathFor } from '../../router/access'
import { useAuthStore } from '../../store/auth'

/**
 * 404 页的逻辑。
 *
 * 只有"回哪儿去"这一件事，但仍按约定抽成 hook，保持页面组件纯展示。
 */
export function useNotFound() {
  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)

  const goHome = useCallback(() => {
    // 未登录时 homePathFor 返回 /login，登录后返回对应角色的工作台
    navigate(homePathFor(user), { replace: true })
  }, [navigate, user])

  return {
    goHome,
    isAuthed: user !== null,
  }
}
