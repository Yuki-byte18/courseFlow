import type { ReactNode } from 'react'

import './index.css'

interface PageHeaderProps {
  title: string
  /** 一句话说明这个页面做什么 */
  subtitle?: string
  /** 右侧操作区（按钮等） */
  extra?: ReactNode
}

/**
 * 页面标题条。
 *
 * 抽出来的理由：每个业务页都需要「大标题 + 说明 + 右侧操作」这一段，
 * 各写一份就会在字号、间距、对齐上出现细微差异，整站看起来就不统一了。
 */
export function PageHeader({ title, subtitle, extra }: PageHeaderProps) {
  return (
    <header className="page-header">
      <div className="page-header__text">
        <h1 className="page-header__title">{title}</h1>
        {subtitle && <p className="page-header__subtitle">{subtitle}</p>}
      </div>
      {extra && <div className="page-header__extra">{extra}</div>}
    </header>
  )
}
