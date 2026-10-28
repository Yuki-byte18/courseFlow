import type { Slot } from '../api/types'

const WEEKDAY_NAMES = ['', '一', '二', '三', '四', '五', '六', '日']

/**
 * 把时间段数组格式化成一行可读文本。
 *
 * 例："周一 1-2节 / 周三 5-6节"
 */
export function formatSlots(slots?: Slot[] | null): string {
  if (!slots || slots.length === 0) return '—'
  return slots
    .map((s) => `周${WEEKDAY_NAMES[s.weekday] ?? '?'} ${s.start_period}-${s.end_period}节`)
    .join(' / ')
}

/** 学分统一保留一位小数。后端是 DECIMAL(3,1)，直接打印会出现 3 或 3.5 两种形态。 */
export function formatCredits(value: number): string {
  return value.toFixed(1)
}

/** 周一..周日的中文简称。 */
export function weekdayName(weekday: number): string {
  return `周${WEEKDAY_NAMES[weekday] ?? '?'}`
}

/**
 * 把后端返回的时间串渲染成 "MM-DD HH:mm"。
 *
 * 解析失败时原样返回：宁可显示一个丑字符串，也不要显示 Invalid Date。
 */
export function formatDateTime(input?: string | null): string {
  if (!input) return '—'
  const date = new Date(input)
  if (Number.isNaN(date.getTime())) return input

  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/**
 * 只显示日期部分 "YYYY-MM-DD"。
 *
 * 学期的起止是"日期"语义，显示到分钟反而是噪音。
 */
export function formatDate(input?: string | null): string {
  if (!input) return '—'
  const date = new Date(input)
  if (Number.isNaN(date.getTime())) return input

  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}
