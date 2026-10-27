import { useEffect, useRef, useState } from 'react'

interface TypewriterOptions {
  /** 每个字的打字间隔（毫秒） */
  typeSpeed?: number
  /** 打完一句停留多久 */
  holdMs?: number
  /** 删除一个字间隔 */
  deleteSpeed?: number
}

/**
 * 打字机效果：逐字打出 → 停留 → 逐字删除 → 换下一句。
 *
 * 登录页用它在标题下轮播能力标签（万级洪峰 / 0 超卖 / Agent 一句话选课）。
 *
 * 两个约束：
 * 1. `phrases` 必须是**稳定引用**（模块级常量），否则每次渲染都会重启动画；
 * 2. 用户开启"减少动态效果"或没有短语时，不播动画。
 */
export function useTypewriter(phrases: string[], options: TypewriterOptions = {}): string {
  const { typeSpeed = 60, holdMs = 1800, deleteSpeed = 24 } = options

  const [text, setText] = useState('')
  const [index, setIndex] = useState(0)
  const [phase, setPhase] = useState<'typing' | 'holding' | 'deleting'>('typing')
  const reducedMotion = useRef(false)

  useEffect(() => {
    reducedMotion.current =
      typeof window !== 'undefined' &&
      window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true

    // 减少动态效果时直接静态显示第一句
    if (reducedMotion.current) {
      setText(phrases[0] ?? '')
    }
  }, [phrases])

  useEffect(() => {
    if (reducedMotion.current || phrases.length === 0) return

    const phrase = phrases[index % phrases.length] ?? ''
    let timer = 0

    if (phase === 'typing') {
      timer = window.setTimeout(
        () => setText(phrase.slice(0, text.length + 1)),
        typeSpeed,
      )
      if (text.length >= phrase.length) {
        timer = window.setTimeout(() => setPhase('holding'), holdMs)
      }
    } else if (phase === 'holding') {
      timer = window.setTimeout(() => setPhase('deleting'), 220)
    } else if (text.length > 0) {
      timer = window.setTimeout(() => setText(text.slice(0, -1)), deleteSpeed)
    } else {
      setIndex((prev) => prev + 1)
      setPhase('typing')
      return
    }

    return () => window.clearTimeout(timer)
  }, [text, phase, index, phrases, typeSpeed, holdMs, deleteSpeed])

  return text
}
