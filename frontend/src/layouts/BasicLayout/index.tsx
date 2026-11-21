import { Button, Dropdown, Modal } from 'antd'
import { NavLink, Outlet } from 'react-router-dom'

import { LlmKeyPanel } from '../../components/LlmKeyPanel'
import { useBasicLayout } from './useBasicLayout'
import './index.css'

const AI_MENU_KEY = 'ai-settings'
const LOGOUT_MENU_KEY = 'logout'

/**
 * 登录后的通用外壳：顶部导航 + 内容区。
 *
 * 三端共用一套布局，导航项由当前用户的主角色 + 权限决定；
 * 具体哪个页面能访问，仍然由路由表的 meta 管 —— 布局不参与鉴权。
 */
export function BasicLayout() {
  const {
    user,
    roleLabel,
    navItems,
    onLogout,
    loggingOut,
    canUseAgent,
    aiModalOpen,
    openAiModal,
    closeAiModal,
  } = useBasicLayout()

  const menuItems = [
    ...(canUseAgent
      ? [{ key: AI_MENU_KEY, label: 'AI 设置（DeepSeek Key）' }]
      : []),
    { key: LOGOUT_MENU_KEY, label: '退出登录', danger: true },
  ]

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
                // **每一项都精确匹配。**
                //
                // NavLink 默认是前缀匹配，所以 /agent 会匹配到 /agent/trace ——
                // 表现为"点了 Trace，Agent 还亮着"。这里每个导航项都是一个
                // 独立页面（没有"父级只负责分组"的情况），所以全部用 end。
                end
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

          <Dropdown
            trigger={['click']}
            menu={{
              items: menuItems,
              onClick: ({ key }) => {
                if (key === AI_MENU_KEY) {
                  openAiModal()
                  return
                }
                void onLogout()
              },
            }}
          >
            <Button size="small" type="text" loading={loggingOut}>
              {user?.real_name || user?.username || '未登录'} ▾
            </Button>
          </Dropdown>
        </div>
      </header>

      <main className="basic-layout__content">
        <Outlet />
      </main>

      <Modal
        title="AI 设置"
        open={aiModalOpen}
        onCancel={closeAiModal}
        footer={null}
        width={620}
      >
        <LlmKeyPanel compact onConfigured={closeAiModal} />
      </Modal>
    </div>
  )
}
