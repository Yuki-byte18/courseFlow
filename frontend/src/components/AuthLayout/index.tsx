import type { CSSProperties, ReactNode } from 'react'
import { ConfigProvider } from 'antd'

import { authTheme } from '../../theme/authTheme'
import { useAuthLayout } from './useAuthLayout'
import './index.css'

/** 主标题。逐字渲染，用于入场编排 */
const HEADLINE = '所有课程，一处开始。'

/** 左栏能力清单：标签 + 可验证的指标 */
const CAPABILITIES = [
  { label: '毫秒级扣减', value: 'p99 ≤ 100ms' },
  { label: '0 超卖', value: 'Redis Lua 原子扣减' },
  { label: '业务 Agent', value: '只读工具 · 可审计' },
]

/** 底部产品预览里的课程卡（纯展示数据） */
const PREVIEW = [
  { name: '数据结构', credits: 3, left: 7, total: 60 },
  { name: '操作系统', credits: 3, left: 12, total: 60 },
  { name: '计算机网络', credits: 3, left: 5, total: 40 },
  { name: '线性代数', credits: 2, left: 43, total: 120 },
]

interface AuthLayoutProps {
  /** 卡片标题，例如「登录」「注册」 */
  word: string
  /** 卡片标题下的小标签 */
  eyebrow?: string
  children: ReactNode
}

/**
 * 登录 / 注册页外壳。参考 getdesign.md / Apple。
 *
 * 版面分三段，而不是只放一个表单：
 *   ① 左栏 hero —— 逐字入场的大标题 + 带指标的能力清单
 *   ② 右栏表单 —— 卡片堆（悬停散开）+ 填充式圆角输入 + 金色 focus ring
 *   ③ 底部预览 —— 纯 CSS 画出来的"选课台"窗口，余量条入场时生长
 *
 * 全部图形由 div + CSS 生成，没有图片、没有视频、没有额外依赖。
 * 逻辑一行未动：useAuthLayout 仍被调用，"已登录自动回工作台"保持不变。
 */
export function AuthLayout({ word, eyebrow, children }: AuthLayoutProps) {
  const { year } = useAuthLayout()

  return (
    <ConfigProvider theme={authTheme}>
      <div className="auth">
        <header className="auth__top">
          <span className="auth__logo">
            <i className="auth__logo-dot" aria-hidden="true" />
            courseFlow
          </span>
          <nav className="auth__nav">
            <span>选课</span>
            <span>课表</span>
            <span>Agent</span>
          </nav>
          <span className="auth__ver">V0.1.0</span>
        </header>

        <main className="auth__stage">
          <div className="auth__row">
            {/* ---------- 左：hero ---------- */}
            <section className="auth__hero">
              <p className="auth__eyebrow">COURSE SELECTION PLATFORM</p>

              <h1 className="auth__headline" aria-label={HEADLINE}>
                {Array.from(HEADLINE).map((char, index) => (
                  <span
                    key={index}
                    aria-hidden="true"
                    style={{ '--i': index } as CSSProperties}
                  >
                    {char}
                  </span>
                ))}
              </h1>

              <p className="auth__sub">
                登录你的 courseFlow 账号，进入选课、课表与 AI 助教。
              </p>

              <dl className="auth__caps">
                {CAPABILITIES.map((item) => (
                  <div className="cap" key={item.label}>
                    <dt>
                      <i aria-hidden="true" />
                      {item.label}
                    </dt>
                    <dd>{item.value}</dd>
                  </div>
                ))}
              </dl>
            </section>

            {/* ---------- 右：表单卡片堆 ---------- */}
            <div className="auth__card-wrap">
              <span className="auth__sheet auth__sheet--2" aria-hidden="true" />
              <span className="auth__sheet auth__sheet--1" aria-hidden="true" />

              <section className="auth__card">
                <div className="auth__card-head">
                  <h2 className="auth__card-title">{word}</h2>
                  {eyebrow && <p className="auth__card-eyebrow">{eyebrow}</p>}
                </div>
                {children}
              </section>
            </div>
          </div>

          {/* ---------- 底部：产品预览 ---------- */}
          <section className="preview" aria-hidden="true">
            <div className="preview__bar">
              <span className="preview__dots">
                <i />
                <i />
                <i />
              </span>
              <span className="preview__title">courseFlow / 选课台</span>
              <span className="preview__term">2026 春</span>
            </div>

            <div className="preview__body">
              {PREVIEW.map((course) => (
                <article className="course" key={course.name}>
                  <div className="course__head">
                    <span className="course__name">{course.name}</span>
                    <span className="course__credits">{course.credits} 学分</span>
                  </div>

                  <div className="course__bar">
                    <i style={{ width: `${(course.left / course.total) * 100}%` }} />
                  </div>

                  <div className="course__foot">
                    <span className="course__stock">
                      余量 {course.left} / {course.total}
                    </span>
                    <span className="course__action">选课</span>
                  </div>
                </article>
              ))}
            </div>
          </section>
        </main>

        <footer className="auth__footer">
          <span>© {year} courseFlow</span>
          <span className="auth__status">
            <i aria-hidden="true" />
            系统运行正常
          </span>
          <span className="auth__footer-links">
            <span>隐私</span>
            <span>条款</span>
          </span>
        </footer>
      </div>
    </ConfigProvider>
  )
}
