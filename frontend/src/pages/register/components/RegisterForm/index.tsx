import { Alert, Button, Form, Input } from 'antd'
import { Link } from 'react-router-dom'

import { useRegisterForm } from './useRegisterForm'
import './index.css'

/** 与后端一致的最小密码长度 */
const MIN_PASSWORD_LENGTH = 8

export function RegisterForm() {
  const { form, onFinish, submitting, submitError } = useRegisterForm()

  return (
    <Form
      form={form}
      layout="vertical"
      requiredMark={false}
      disabled={submitting}
      onFinish={onFinish}
    >
      {submitError && (
        <Alert
          className="register-form__alert"
          type="error"
          showIcon
          message={submitError}
        />
      )}

      <Form.Item
        label="用户名"
        name="username"
        rules={[
          { required: true, message: '请输入用户名' },
          { min: 4, max: 64, message: '用户名长度为 4-64 个字符' },
        ]}
      >
        <Input size="large" placeholder="学号" autoComplete="username" allowClear />
      </Form.Item>

      <Form.Item
        label="姓名"
        name="real_name"
        rules={[
          { required: true, message: '请输入姓名' },
          { max: 64, message: '姓名最长 64 个字符' },
        ]}
      >
        <Input size="large" placeholder="真实姓名" allowClear />
      </Form.Item>

      <Form.Item
        label="邮箱（选填）"
        name="email"
        rules={[{ type: 'email', message: '邮箱格式不正确' }]}
      >
        <Input size="large" placeholder="用于接收通知，可留空" allowClear />
      </Form.Item>

      <Form.Item
        label="密码"
        name="password"
        rules={[
          { required: true, message: '请输入密码' },
          { min: MIN_PASSWORD_LENGTH, max: 72, message: `密码长度为 ${MIN_PASSWORD_LENGTH}-72 位` },
        ]}
      >
        <Input.Password
          size="large"
          placeholder={`至少 ${MIN_PASSWORD_LENGTH} 位`}
          autoComplete="new-password"
        />
      </Form.Item>

      <Form.Item
        label="确认密码"
        name="confirmPassword"
        dependencies={['password']}
        rules={[
          { required: true, message: '请再次输入密码' },
          ({ getFieldValue }) => ({
            validator(_rule, value: string) {
              const password: unknown = getFieldValue('password')
              if (!value || password === value) {
                return Promise.resolve()
              }
              return Promise.reject(new Error('两次输入的密码不一致'))
            },
          }),
        ]}
      >
        <Input.Password size="large" placeholder="再次输入密码" autoComplete="new-password" />
      </Form.Item>

      <Button type="primary" size="large" htmlType="submit" block loading={submitting}>
        注册并登录
      </Button>

      <div className="register-form__footer">
        已有账号？
        <Link to="/login">去登录</Link>
      </div>
    </Form>
  )
}
