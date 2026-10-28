import './index.css'

interface StockBarProps {
  /** 已选人数 */
  taken: number
  /** 容量 */
  capacity: number
  /** 剩余名额（后端算好的，前端不自己减） */
  remaining: number
}

/**
 * 余量条。
 *
 * 用「已选 / 容量」画进度，但右侧显示的是**剩余**名额 ——
 * 学生关心的是"还能不能抢到"，不是"已经选了多少"。
 */
export function StockBar({ taken, capacity, remaining }: StockBarProps) {
  const percent = capacity > 0 ? Math.min(100, Math.round((taken / capacity) * 100)) : 0

  let tone = ''
  if (remaining <= 0) {
    tone = ' stock-bar--full'
  } else if (capacity > 0 && remaining <= capacity * 0.15) {
    tone = ' stock-bar--low'
  }

  return (
    <div className={`stock-bar${tone}`}>
      <div className="stock-bar__track">
        <i style={{ width: `${percent}%` }} />
      </div>
      <span className="stock-bar__text">
        {remaining} / {capacity}
      </span>
    </div>
  )
}
