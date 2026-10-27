import { Button, Result } from 'antd'

import { useNotFound } from './useNotFound'
import './index.css'

/**
 * 404 页面。
 *
 * 它在两种情况下出现：
 * 1. 路径确实不存在
 * 2. **用户已登录但没有该页面的权限**——无权访问的路由根本不会被挂载，
 *    所以会落到这里。这与后端"越权返回 404 而不是 403"的做法一致：
 *    不告诉对方"这个资源存在，只是你没有权限"。
 */
export function NotFoundPage() {
  const { goHome, isAuthed } = useNotFound()

  return (
    <div className="not-found">
      <Result
        status="404"
        title="404"
        subTitle={isAuthed ? '页面不存在，或你没有访问该页面的权限' : '页面不存在'}
        extra={
          <Button type="primary" onClick={goHome}>
            {isAuthed ? '返回工作台' : '去登录'}
          </Button>
        }
      />
    </div>
  )
}
