import { Empty, Typography } from 'antd'

import './index.css'

interface PagePlaceholderProps {
  title: string
  description?: string
  /** 后续要在这个页面实现的功能点，写给自己和队友看 */
  todos?: string[]
}

/**
 * 页面占位。
 *
 * 三端首页目前还没有业务内容，先用它撑着，
 * 这样动态路由机制可以被真实地验证（登录后能看到对应端的首页，换角色看到不同的页面）。
 */
export function PagePlaceholder({ title, description, todos = [] }: PagePlaceholderProps) {
  return (
    <div className="page-placeholder">
      <Typography.Title level={3}>{title}</Typography.Title>
      {description && (
        <Typography.Paragraph type="secondary">{description}</Typography.Paragraph>
      )}

      {todos.length > 0 && (
        <div className="page-placeholder__todos">
          <Typography.Text strong>待实现</Typography.Text>
          <ul>
            {todos.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      <Empty description="页面建设中" image={Empty.PRESENTED_IMAGE_SIMPLE} />
    </div>
  )
}
