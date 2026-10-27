import { Spin } from 'antd'

import './index.css'

interface PageLoadingProps {
  /** 占满整个视口，用于应用启动阶段（登录态恢复中） */
  fullscreen?: boolean
  tip?: string
}

/**
 * 加载占位。
 *
 * 单独抽成组件而不是到处写 <Spin/>，是为了统一"启动中"和"页面内加载"的观感，
 * 也方便以后替换成骨架屏。
 */
export function PageLoading({ fullscreen = false, tip = '加载中…' }: PageLoadingProps) {
  const className = fullscreen
    ? 'page-loading page-loading--fullscreen'
    : 'page-loading'

  return (
    <div className={className}>
      <Spin size="large" />
      <p className="page-loading__tip">{tip}</p>
    </div>
  )
}
