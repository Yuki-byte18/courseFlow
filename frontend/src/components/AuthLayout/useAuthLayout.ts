import { useEffect, useMemo, useRef } from 'react'
import { useNavigate } from 'react-router-dom'

import { homePathFor } from '../../router/access'
import { useAuthStore } from '../../store/auth'

/** 坐标读数补零到 4 位，避免数字宽度变化导致抖动 */
function pad(value: number): string {
  return String(Math.max(0, Math.round(value))).padStart(4, '0')
}

/**
 * AuthLayout 的逻辑。
 *
 * 1. 已登录用户访问 /login 或 /register → 直接送回工作台；
 * 2. 自定义光标环：lerp 缓动跟随（每帧只改一个元素的 transform，极便宜）；
 * 3. "照亮网格"：把指针位置写进 CSS 变量 --mx / --my，
 *    驱动一个全屏 mask-image 重绘 —— 这是本页**唯一较贵的绘制**，
 *    所以刻意降到约 30fps（隔帧写入），肉眼没有区别但重绘量减半；
 * 4. 图纸标注的实时读数：底部坐标（真实指针位置）与 QPS（演示数据），
 *    每 ~420ms 更新一次文本，避免 60fps 写 textContent。
 *
 * 另外缓存的 getBoundingClientRect 只在 resize / scroll 时刷新，
 * 避免在每帧循环里反复触发布局计算。
 */
export function useAuthLayout() {
  const navigate = useNavigate()
  const user = useAuthStore((state) => state.user)
  const shellRef = useRef<HTMLDivElement | null>(null)
  const cursorRef = useRef<HTMLDivElement | null>(null)
  const coordsRef = useRef<HTMLSpanElement | null>(null)
  const qpsRef = useRef<HTMLSpanElement | null>(null)

  useEffect(() => {
    if (user) {
      navigate(homePathFor(user), { replace: true })
    }
  }, [navigate, user])

  useEffect(() => {
    const shell = shellRef.current
    if (!shell) return

    const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true
    const finePointer = window.matchMedia?.('(pointer: fine)').matches === true
    const animate = finePointer && !reduced

    if (animate) {
      shell.classList.add('is-pointer')
    }

    const target = { x: 0, y: 0 }
    const current = { x: 0, y: 0 }
    let primed = false
    let frame = 0
    let tick = 0
    let lastTelemetry = 0
    let rect = shell.getBoundingClientRect()

    const refreshRect = () => {
      rect = shell.getBoundingClientRect()
    }

    const onMove = (event: MouseEvent) => {
      target.x = event.clientX
      target.y = event.clientY

      if (!primed) {
        current.x = target.x
        current.y = target.y
        primed = true
      }
    }

    const onOver = (event: MouseEvent) => {
      const el = event.target as HTMLElement | null
      shell.classList.toggle('is-hovering', Boolean(el?.closest('a, button, input, textarea')))
    }

    const loop = () => {
      // lerp：0.18 的系数让环有明显但舒服的"拖尾"
      current.x += (target.x - current.x) * 0.18
      current.y += (target.y - current.y) * 0.18
      tick += 1

      if (cursorRef.current) {
        cursorRef.current.style.transform = `translate3d(${current.x}px, ${current.y}px, 0)`
      }

      // 隔帧更新 mask 位置：约 30fps，重绘量减半
      if (tick % 2 === 0 && rect.width > 0 && rect.height > 0) {
        shell.style.setProperty('--mx', `${((current.x - rect.left) / rect.width) * 100}%`)
        shell.style.setProperty('--my', `${((current.y - rect.top) / rect.height) * 100}%`)
      }

      const now = performance.now()
      if (now - lastTelemetry > 420) {
        lastTelemetry = now
        if (coordsRef.current) {
          coordsRef.current.textContent = `X ${pad(current.x)} · Y ${pad(current.y)}`
        }
        // 演示数据：一条正弦曲线制造"在跳动"的观感，不代表真实压测结果
        if (qpsRef.current) {
          qpsRef.current.textContent = String(10243 + Math.round(Math.sin(now / 900) * 186))
        }
      }

      frame = window.requestAnimationFrame(loop)
    }

    shell.addEventListener('mousemove', onMove)
    shell.addEventListener('mouseover', onOver)
    window.addEventListener('resize', refreshRect)
    window.addEventListener('scroll', refreshRect, { passive: true })

    if (animate) {
      frame = window.requestAnimationFrame(loop)
    }

    return () => {
      shell.removeEventListener('mousemove', onMove)
      shell.removeEventListener('mouseover', onOver)
      window.removeEventListener('resize', refreshRect)
      window.removeEventListener('scroll', refreshRect)
      if (frame) window.cancelAnimationFrame(frame)
    }
  }, [])

  const year = useMemo(() => new Date().getFullYear(), [])

  return { year, shellRef, cursorRef, coordsRef, qpsRef }
}
