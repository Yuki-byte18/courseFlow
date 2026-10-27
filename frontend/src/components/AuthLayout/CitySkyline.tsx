/**
 * 霓虹未来城市天际线。
 *
 * 全部由 SVG 矩形程序化生成，不引入任何图片：
 *   · 远景楼群（矮、亮一点、被雾化）
 *   · 近景楼群（高、更黑、压住画面底部）
 *   · 霓虹灯带（青 / 品红 / 琥珀三色，只占很小面积）
 *   · 天线与闪烁信号灯
 *
 * 用 viewBox + preserveAspectRatio="xMidYMax slice" 保证：
 * 无论窗口多宽，楼群始终贴着底边、横向铺满。
 */

interface Building {
  x: number
  w: number
  h: number
}

/** 霓虹灯带的色调节点 */
type NeonTone = 'cyan' | 'magenta' | 'amber'

interface Neon {
  x: number
  y: number
  h: number
  tone: NeonTone
}

const VIEW_H = 340

/** 远景楼群：25 栋，高度 108–196 */
const FAR: Building[] = [
  { x: 0, w: 58, h: 118 },
  { x: 66, w: 42, h: 168 },
  { x: 114, w: 66, h: 132 },
  { x: 186, w: 34, h: 196 },
  { x: 226, w: 52, h: 146 },
  { x: 284, w: 44, h: 108 },
  { x: 334, w: 70, h: 160 },
  { x: 410, w: 36, h: 126 },
  { x: 452, w: 56, h: 186 },
  { x: 514, w: 42, h: 136 },
  { x: 562, w: 64, h: 112 },
  { x: 632, w: 34, h: 172 },
  { x: 672, w: 52, h: 140 },
  { x: 730, w: 68, h: 122 },
  { x: 804, w: 38, h: 184 },
  { x: 848, w: 48, h: 134 },
  { x: 902, w: 62, h: 156 },
  { x: 970, w: 34, h: 118 },
  { x: 1010, w: 56, h: 174 },
  { x: 1072, w: 44, h: 130 },
  { x: 1122, w: 66, h: 148 },
  { x: 1194, w: 38, h: 188 },
  { x: 1238, w: 52, h: 124 },
  { x: 1296, w: 60, h: 160 },
  { x: 1362, w: 78, h: 134 },
]

/** 近景楼群：14 栋，更宽更矮，形成前后层次 */
const NEAR: Building[] = [
  { x: 0, w: 96, h: 74 },
  { x: 104, w: 78, h: 102 },
  { x: 190, w: 112, h: 66 },
  { x: 310, w: 86, h: 94 },
  { x: 404, w: 122, h: 58 },
  { x: 534, w: 90, h: 106 },
  { x: 632, w: 98, h: 72 },
  { x: 738, w: 136, h: 56 },
  { x: 882, w: 82, h: 98 },
  { x: 972, w: 106, h: 68 },
  { x: 1086, w: 92, h: 88 },
  { x: 1186, w: 120, h: 62 },
  { x: 1314, w: 80, h: 100 },
  { x: 1402, w: 38, h: 76 },
]

/** 霓虹灯带：贴在楼体侧边的竖向细条 */
const NEON: Neon[] = [
  { x: 196, y: 274, h: 66, tone: 'cyan' },
  { x: 430, y: 282, h: 58, tone: 'magenta' },
  { x: 640, y: 234, h: 106, tone: 'amber' },
  { x: 990, y: 272, h: 68, tone: 'cyan' },
  { x: 1210, y: 278, h: 62, tone: 'magenta' },
  { x: 1330, y: 240, h: 100, tone: 'cyan' },
]

/** 天线：[x 坐标, 楼顶 y, 天线顶端 y] */
const ANTENNAS: Array<[number, number, number]> = [
  [203, 144, 98],
  [480, 154, 104],
  [823, 156, 108],
  [1213, 152, 102],
]

export function CitySkyline() {
  return (
    <svg
      className="city"
      viewBox={`0 0 1440 ${VIEW_H}`}
      preserveAspectRatio="xMidYMax slice"
      aria-hidden="true"
    >
      <defs>
        {/* 城市光污染：从地面往天上打的光。楼群靠它才有剪影 */}
        <linearGradient id="city-sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#0a1224" stopOpacity="0" />
          <stop offset="45%" stopColor="#0e7490" stopOpacity="0.16" />
          <stop offset="70%" stopColor="#22d3ee" stopOpacity="0.34" />
          <stop offset="88%" stopColor="#67e8f9" stopOpacity="0.46" />
          <stop offset="100%" stopColor="#22d3ee" stopOpacity="0.22" />
        </linearGradient>
      </defs>

      {/* 天空辉光必须先于楼群绘制，否则楼群没有可对比的亮背景，看起来就是"没画" */}
      <rect x="0" y="0" width="1440" height={VIEW_H} fill="url(#city-sky)" />

      {/* 远景：被雾化，顶部靠 CSS mask 渐隐 */}
      <g className="city__far">
        {FAR.map((b, i) => (
          <rect key={`f${i}`} x={b.x} y={VIEW_H - b.h} width={b.w} height={b.h} />
        ))}
      </g>

      {/* 天线与信号灯 */}
      <g className="city__antenna">
        {ANTENNAS.map(([x, top, tip]) => (
          <g key={`a${x}`}>
            <line x1={x} y1={top} x2={x} y2={tip} strokeWidth="1.5" />
            <circle className="city__tip" cx={x} cy={tip} r="2.5" />
          </g>
        ))}
      </g>

      {/* 近景：更黑，压住画面底部 */}
      <g className="city__near">
        {NEAR.map((b, i) => (
          <rect key={`n${i}`} x={b.x} y={VIEW_H - b.h} width={b.w} height={b.h} />
        ))}
      </g>

      {/* 霓虹灯带 */}
      <g>
        {NEON.map((n) => (
          <rect
            key={`neon${n.x}`}
            className={`city__neon city__neon--${n.tone}`}
            x={n.x}
            y={n.y}
            width="2"
            height={n.h}
          />
        ))}
      </g>
    </svg>
  )
}
