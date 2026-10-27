import { Button, Tag } from 'antd'
import { Outlet } from 'react-router-dom'

import { useBasicLayout } from './useBasicLayout'
import './index.css'

/**
 * 登录后的通用外壳：顶部栏 + 内容区。
 *
 * 三端共用一套布局，通过路由表里的 meta.roles 区分能看到哪些页面，
 * 而不是维护三份布局组件。
 */
export function BasicLayout() {
  const { user, roleLabel, onLogout, loggingOut } = useBasicLayout()

  return (
    <div className="basic-layout">
      <header className="basic-layout__header">
        <div className="basic-layout__brand">courseFlow</div>

        <div className="basic-layout__user">
          {roleLabel && <Tag color="blue">{roleLabel}</Tag>}
          <span className="basic-layout__name">{user?.real_name || user?.username || '未登录'}</span>
          <Button size="small" onClick={onLogout} loading={loggingOut}>
            退出登录
          </Button>
        </div>
      </header>

      <main className="basic-layout__content">
        <Outlet />
      </main>
    </div>
  )
}
