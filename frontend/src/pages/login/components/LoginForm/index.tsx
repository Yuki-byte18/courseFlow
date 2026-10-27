import { Alert, Button, Form, Input } from 'antd'
import { Link } from 'react-router-dom'

import { useLoginForm } from './useLoginForm'
import './index.css'

/**
 * 登录表单。
 *
 * 校验用 antd Form 自带的 rules：字段少、规则简单，
 * 没必要为了两个必填项引入 react-hook-form + zod 两层抽象。
 */
export function LoginForm() {
  const { form, onFinish, submitting, submitError } = useLoginForm()

  return (
    <Form
      form={form}
      layout="vertical"
      requiredMark={false}
      disabled={submitting}
      onFinish={onFinish}
      autoComplete="on"
    >
      {submitError && (
        <Alert
          className="login-form__alert"
          type="error"
          showIcon
          message={submitError}
        />
      )}

      <Form.Item
        label="用户名"
        name="username"
        rules={[{ required: true, message: '请输入用户名' }]}
      >
        <Input size="large" placeholder="学号 / 工号" autoComplete="username" allowClear />
      </Form.Item>

      <Form.Item
        label="密码"
        name="password"
        rules={[{ required: true, message: '请输入密码' }]}
      >
        <Input.Password
          size="large"
          placeholder="请输入密码"
          autoComplete="current-password"
        />
      </Form.Item>

      <Button type="primary" size="large" htmlType="submit" block loading={submitting}>
        登录
      </Button>

      <div className="login-form__footer">
        还没有账号？
        <Link to="/register">注册学生账号</Link>
      </div>
    </Form>
  )
}
