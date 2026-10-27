import { useCallback, useState } from 'react'
import { Form } from 'antd'
import { useNavigate } from 'react-router-dom'

import { authApi } from '../../../../api/auth'
import type { LoginPayload } from '../../../../api/types'
import { toErrorMessage } from '../../../../request/errors'
import { homePathFor } from '../../../../router/access'
import { useAuthStore } from '../../../../store/auth'

/**
 * 登录表单的逻辑。
 *
 * 按"逻辑与 UI 分离"：组件里只有 JSX，取数、跳转、错误态都在这个 hook 里。
 */
export function useLoginForm() {
  const [form] = Form.useForm<LoginPayload>()
  const navigate = useNavigate()
  const setAuth = useAuthStore((state) => state.setAuth)

  const [submitting, setSubmitting] = useState(false)
  /** 提交后的整体错误（字段错误由 antd Form 自己展示） */
  const [submitError, setSubmitError] = useState<string | null>(null)

  const onFinish = useCallback(
    async (values: LoginPayload) => {
      setSubmitting(true)
      setSubmitError(null)

      try {
        const result = await authApi.login(values)
        // 先写全局登录态，路由树会根据 user 重算，动态路由随之挂载
        setAuth(result.token, result.user)
        navigate(homePathFor(result.user), { replace: true })
      } catch (err) {
        // 后端对「用户不存在」和「密码错误」返回同一句话，这里直接展示
        setSubmitError(toErrorMessage(err, '登录失败，请稍后重试'))
      } finally {
        setSubmitting(false)
      }
    },
    [navigate, setAuth],
  )

  return { form, onFinish, submitting, submitError }
}
