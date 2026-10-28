import { Button } from 'antd'
import { NavLink, Outlet } from 'react-router-dom'

import { useBasicLayout } from './useBasicLayout'
import './index.css'

/**
 * 登录后的通用外壳：顶部导航 + 内容区。
 *
 * 三端共用一套布局，导航项由当前用户的主角色决定；
 * 具体哪个页面能访问，仍然由路由表的 meta.roles 管 —— 布局不参与鉴权。
 */
export function BasicLayout() {
  const { user, roleLabel, navItems, onLogout, loggingOut } = useBasicLayout()

  return (
    <div className="basic-layout">
      <header className="basic-layout__header">
        <div className="basic-layout__left">
          <span className="basic-layout__brand">
            <i className="basic-layout__dot" aria-hidden="true" />
            courseFlow
          </span>

          <nav className="basic-layout__nav">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/student' || item.to === '/admin'}
                className={({ isActive }) =>
                  isActive
                    ? 'basic-layout__link basic-layout__link--active'
                    : 'basic-layout__link'
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>

        <div className="basic-layout__right">
          {roleLabel && <span className="basic-layout__role">{roleLabel}</span>}
          <span className="basic-layout__name">
            {user?.real_name || user?.username || '未登录'}
          </span>
          <Button size="small" type="text" onClick={onLogout} loading={loggingOut}>
            退出
          </Button>
        </div>
      </header>

      <main className="basic-layout__content">
        <Outlet />
      </main>
    </div>
  )
}
