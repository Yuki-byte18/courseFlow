import type { CSSProperties, ReactNode } from 'react'
import { ConfigProvider } from 'antd'

import { authTheme } from '../../theme/authTheme'
import { CitySkyline } from './CitySkyline'
import { useAuthLayout } from './useAuthLayout'
import './index.css'

/** 底部跑马灯词条（渲染两遍以实现无缝循环） */
const TICKER = [
  'REDIS LUA',
  'KAFKA',
  '0 OVERSOLD',
  'P99 100MS',
  'FLINK',
  'SPARK',
  'AGENT READ-ONLY',
]

/** 左侧标尺刻度数 */
const RULER_TICKS = 15

/** 遥测柱基础高度（%）。写死而非随机，避免每次渲染抖动。 */
const BARS = [42, 68, 35, 88, 55, 74, 30, 92, 60, 46, 80, 52]

/** 左侧三层状态行 */
const LAYERS = [
  { id: '01', name: '在线层', state: 'ONLINE' },
  { id: '02', name: '近线层', state: 'STREAM' },
  { id: '03', name: '离线层', state: 'BATCH' },
]

interface AuthLayoutProps {
  /** 巨大显示词，例如「登录」「注册」 */
  word: string
  /** 显示词上方的小标签 */
  eyebrow?: string
  children: ReactNode
}

/**
 * 登录 / 注册页外壳：赛博朋克终端。
 *
 * 画面分四层：
 *   背景层  网格 + 光标照亮 + CRT 扫描纹 + 噪点 + 暗角
 *   结构层  内缩边框、四角霓虹刻度、左侧标尺
 *   主体层  左：旋转 HUD 环 + 状态行 + 遥测；右：表单（偏置，不居中）
 *   标注层  顶部/底部等宽元信息、底部跑马灯、实时坐标
 *
 * 全部图形由 SVG/CSS 生成，没有图片和视频依赖。
 */
export function AuthLayout({ word, eyebrow, children }: AuthLayoutProps) {
  const { year, shellRef, cursorRef, coordsRef, qpsRef } = useAuthLayout()

  return (
    <ConfigProvider theme={authTheme}>
      <div className="auth" ref={shellRef}>
        {/* ---------- 背景层 ---------- */}
        <div className="auth__grid" aria-hidden="true" />
        <div className="auth__shafts" aria-hidden="true" />
        <CitySkyline />
        <div className="auth__grid auth__grid--lit" aria-hidden="true" />
        <div className="auth__grain" aria-hidden="true" />
        <div className="auth__vignette" aria-hidden="true" />
        <div className="auth__crt" aria-hidden="true" />

        {/* ---------- 结构层 ---------- */}
        <div className="auth__frame" aria-hidden="true">
          <span className="auth__tick auth__tick--tl" />
          <span className="auth__tick auth__tick--tr" />
          <span className="auth__tick auth__tick--bl" />
          <span className="auth__tick auth__tick--br" />
        </div>

        <div className="auth__ruler" aria-hidden="true">
          <span className="auth__ruler-label">SELECTION TERMINAL</span>
          <div className="auth__ruler-ticks">
            {Array.from({ length: RULER_TICKS }, (_, i) => (
              <i key={i} className={i % 4 === 0 ? 'is-major' : undefined} />
            ))}
          </div>
        </div>

        <header className="auth__meta">
          <span className="auth__brand">
            <i className="auth__dot" aria-hidden="true" />
            COURSEFLOW
          </span>
          <span>SYSTEM / ACCESS</span>
        </header>

        {/* ---------- 主体层 ---------- */}
        <main className="auth__stage">
          {/* 左：终端标识 + HUD + 遥测 */}
          <aside className="auth__side" aria-hidden="true">
            <HudRings />

            <div className="auth__side-copy">
              <p className="auth__side-title">选课终端</p>
              <p className="auth__side-sub">ACCESS TERMINAL / NODE 01</p>
            </div>

            <dl className="auth__layers">
              {LAYERS.map((layer) => (
                <div className="auth__layer" key={layer.id}>
                  <dt>
                    <span className="auth__layer-id">{layer.id}</span>
                    {layer.name}
                  </dt>
                  <dd>{layer.state}</dd>
                </div>
              ))}
            </dl>

            <div className="auth__telemetry">
              <div className="auth__telemetry-head">
                <span>DEMO TELEMETRY</span>
                <i className="auth__dot auth__dot--sm" />
              </div>
              <div className="auth__bars">
                {BARS.map((height, index) => (
                  <i key={index} style={{ '--h': height, '--d': index } as CSSProperties} />
                ))}
              </div>
              <dl className="auth__readings">
                <div className="auth__reading">
                  <dt>QPS</dt>
                  <dd>
                    <span ref={qpsRef}>10243</span>
                  </dd>
                </div>
                <div className="auth__reading">
                  <dt>P99</dt>
                  <dd>87 ms</dd>
                </div>
                <div className="auth__reading">
                  <dt>OVERSOLD</dt>
                  <dd>0</dd>
                </div>
              </dl>
            </div>
          </aside>

          {/* 右：表单（偏置，刻意不居中） */}
          <div className="auth__form">
            {eyebrow && <p className="auth__eyebrow">{eyebrow}</p>}
            <h1 className="auth__display" data-word={word}>
              {word}
            </h1>
            <div className="auth__rule" aria-hidden="true" />
            {children}
          </div>
        </main>

        {/* ---------- 标注层 ---------- */}
        <div className="auth__ticker" aria-hidden="true">
          <div className="auth__ticker-track">
            {[0, 1].map((copy) => (
              <div className="auth__ticker-group" key={copy}>
                {TICKER.map((token) => (
                  <span className="auth__ticker-item" key={`${copy}-${token}`}>
                    {token}
                    <i className="auth__dot auth__dot--sm" />
                  </span>
                ))}
              </div>
            ))}
          </div>
        </div>

        <footer className="auth__meta">
          <span>© {year} COURSEFLOW</span>
          <span className="auth__coords" ref={coordsRef}>
            X 0000 · Y 0000
          </span>
          <span>V0.1.0</span>
        </footer>

        <div className="auth__cursor" ref={cursorRef} aria-hidden="true" />
      </div>
    </ConfigProvider>
  )
}

/**
 * HUD 环：三个同心圆环反向旋转 + 十字准星。
 *
 * 用 SVG 而不是图片：任意分辨率清晰，改颜色只改 CSS 变量。
 * 旋转用 transform-box: fill-box，圆心自动对齐元素包围盒中心。
 */
function HudRings() {
  return (
    <svg className="hud" viewBox="0 0 220 220" fill="none" aria-hidden="true">
      <circle className="hud__ring hud__ring--slow" cx="110" cy="110" r="96" />
      <circle className="hud__ring hud__ring--fast" cx="110" cy="110" r="74" />
      <circle className="hud__ring hud__ring--dash" cx="110" cy="110" r="52" />
      <path
        className="hud__cross"
        d="M110 0v26M110 194v26M0 110h26M194 110h26"
      />
      <circle className="hud__core" cx="110" cy="110" r="3.5" />
      <circle className="hud__halo" cx="110" cy="110" r="18" />
    </svg>
  )
}
