import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 开发期把 /api 转发到 Go 后端。
    // 这样浏览器看到的是「同源请求」，后端不需要为了开发环境开放 CORS。
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8080',
        changeOrigin: true,
      },
    },
  },
})