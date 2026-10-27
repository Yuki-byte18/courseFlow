import { AuthLayout } from '../../components/AuthLayout'
import { RegisterForm } from './components/RegisterForm'

/**
 * 注册页。
 *
 * 属于静态路由。注意注册出来的账号**只能是学生**，
 * 教师与管理员账号由教务管理员在后台创建（后端硬编码，前端也无法指定角色）。
 */
export function RegisterPage() {
  return (
    <AuthLayout word="注册" eyebrow="SIGN UP / 学生账号自助开通">
      <RegisterForm />
    </AuthLayout>
  )
}
