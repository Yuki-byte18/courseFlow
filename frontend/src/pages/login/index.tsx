import { AuthLayout } from '../../components/AuthLayout'
import { LoginForm } from './components/LoginForm'

/**
 * 登录页。
 *
 * 属于静态路由：未登录也要能访问，否则用户永远进不来。
 * 视觉外壳在 AuthLayout（登录/注册共用），这里只声明文案和表单。
 */
export function LoginPage() {
  return (
    <AuthLayout word="登录" eyebrow="SIGN IN / 统一身份入口">
      <LoginForm />
    </AuthLayout>
  )
}
