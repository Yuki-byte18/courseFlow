import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App as AntdApp, ConfigProvider } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { BrowserRouter } from 'react-router-dom'

import 'antd/dist/reset.css'
import './styles/tokens.css'
import './index.css'

import { App } from './App'
import { appTheme } from './theme/appTheme'

/**
 * TanStack Query 的全局默认值。
 *
 * retry: 1 —— 默认重试 3 次，对登录这类操作会放大失败等待，降到 1 次。
 * refetchOnWindowFocus: false —— 教务系统不需要"切回标签页就刷新"，
 * 而且抢课页面切回来就发请求会干扰压测。
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      staleTime: 15_000,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ConfigProvider locale={zhCN} theme={appTheme}>
      <AntdApp>
        <QueryClientProvider client={queryClient}>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </QueryClientProvider>
      </AntdApp>
    </ConfigProvider>
  </StrictMode>,
)
