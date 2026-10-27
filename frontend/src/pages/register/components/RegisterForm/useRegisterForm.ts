import { useCallback, useState } from 'react'
import { Form } from 'antd'
import { useNavigate } from 'react-router-dom'

import { authApi } from '../../../../api/auth'
import { toErrorMessage } from '../../../../request/errors'
import { homePathFor } from '../../../../router/access'
import { useAuthStore } from '../../../../store/auth'

/** 注册表单字段（含仅前端使用的确认密码） */
export interface RegisterFormValues {
  username: string
  real_name: string
  email?: string
  password: string
  confirmPassword: string
}

/**
 * 注册表单的逻辑。
 *
 * 注意：提交时**丢弃 confirmPassword**，并且请求体里没有任何角色字段——
 * 后端只接受学生注册，这是防止提权的第一道设计（第二道在后端硬编码）。
 */
export function useRegisterForm() {
  const [form] = Form.useForm<RegisterFormValues>()
  const navigate = useNavigate()
  const setAuth = useAuthStore((state) => state.setAuth)

  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)

  const onFinish = useCallback(
    async (values: RegisterFormValues) => {
      setSubmitting(true)
      setSubmitError(null)

      try {
        const result = await authApi.register({
          username: values.username.trim(),
          password: values.password,
          real_name: values.real_name.trim(),
          email: values.email?.trim() || undefined,
        })
        // 注册成功后端直接返回 token，无需再登录一次
        setAuth(result.token, result.user)
        navigate(homePathFor(result.user), { replace: true })
      } catch (err) {
        setSubmitError(toErrorMessage(err, '注册失败，请稍后重试'))
      } finally {
        setSubmitting(false)
      }
    },
    [navigate, setAuth],
  )

  return { form, onFinish, submitting, submitError }
}
