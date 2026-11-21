import { formatCredits } from '../../utils/format'
import type { AgentOffering } from '../../api/agent'
import './index.css'

const WEEKDAY = ['', '周一', '周二', '周三', '周四', '周五', '周六', '周日']

/**
 * 卡片的可操作回调。
 *
 * 点「选课」不是直接调接口，而是替用户发一句话给 Agent ——
 * 写操作仍然必须经过 Agent 的判断 + 审批闸门。
 */
export interface CardAction {
  action: 'enroll' | 'drop'
  offering: AgentOffering
}

interface CardProps {
  card: string
  payload: unknown
  onAction?: (action: CardAction) => void
}

/**
 * Agent 返回的业务卡片。
 *
 * 为什么不用 markdown 表格：
 *   - 卡片可以被点（选课），表格不能
 *   - 数字用等宽字体对齐，比表格文本好读
 *
 * 为什么渲染成 React 元素而不是 HTML：
 *   模型输出是不可信内容，不用 innerHTML 就天然没有 XSS 面。
 */
export function AgentCard({ card, payload, onAction }: CardProps) {
  switch (card) {
    case 'offering_list':
      return <OfferingList payload={payload as OfferingListPayload} onAction={onAction} />
    case 'offering_detail':
      return (
        <OfferingDetail
          payload={payload as AgentOffering & { found?: boolean }}
          onAction={onAction}
        />
      )
    case 'schedule':
      return <ScheduleCard payload={payload as SchedulePayload} />
    case 'credit_summary':
      return <CreditSummary payload={payload as CreditPayload} />
    case 'conflict_report':
      return <ConflictCard payload={payload as ConflictPayload} />
    case 'enrollment_result':
      return <EnrollResult payload={payload as EnrollPayload} />
    case 'notice':
      return <NoticeCard payload={payload as NoticePayload} />
    case 'round':
      return <RoundCard payload={payload as RoundPayload} />
    case 'reminder_list':
      return <ReminderList payload={payload as ReminderPayload} />
    case 'term':
      return <TermCard payload={payload as TermPayload} />
    default:
      return <FallbackCard card={card} payload={payload} />
  }
}

// ---------------------------------------------------------------------------
// 统一外壳：标题行 + 内容
// ---------------------------------------------------------------------------
function Shell({
  title,
  meta,
  tone,
  children,
}: {
  title: string
  meta?: string
  tone?: 'ok' | 'error' | 'warn'
  children: React.ReactNode
}) {
  return (
    <div className={`card${tone ? ` card--${tone}` : ''}`}>
      <div className="card__head">
        <span className="card__title">{title}</span>
        {meta && <span className="card__meta">{meta}</span>}
      </div>
      {children}
    </div>
  )
}

/** 余量胶囊：三种状态一种视觉语言 */
function RemainPill({ remaining, capacity }: { remaining: number; capacity: number }) {
  const tone = remaining === 0 ? 'full' : remaining <= 5 ? 'low' : 'ok'
  return (
    <span className={`pill pill--${tone}`}>
      {remaining === 0 ? '已满' : `${remaining}`}
      {remaining > 0 && <i>/ {capacity}</i>}
    </span>
  )
}

// ---------------------------------------------------------------------------
// 课程列表
// ---------------------------------------------------------------------------
interface OfferingListPayload {
  term_name?: string
  count?: number
  offerings?: AgentOffering[]
  reason?: string
}

function OfferingList({
  payload,
  onAction,
}: {
  payload: OfferingListPayload
  onAction?: (action: CardAction) => void
}) {
  const offerings = payload?.offerings ?? []
  if (!offerings.length) {
    return <div className="card card--empty">{payload?.reason ?? '没有符合条件的课程'}</div>
  }

  return (
    <div className="card">
      <div className="card__head">
        <span className="card__title">课程</span>
        <span className="card__meta">
          {[payload.term_name, `${offerings.length} 门`].filter(Boolean).join(' · ')}
        </span>
      </div>

      <div className="olist">
        {offerings.map((offering) => (
          <div className="olist__row" key={offering.offering_id}>
            <div className="olist__main">
              <div className="olist__line1">
                <span className="olist__name">{offering.course_name}</span>
                <span className="olist__credits">{formatCredits(offering.credits)} 学分</span>
              </div>
              <div className="olist__line2">
                <span className="olist__code">
                  {offering.course_code} · {offering.section_no} 班
                </span>
                {offering.teacher_name && (
                  <span className="olist__teacher">{offering.teacher_name}</span>
                )}
              </div>
              <div className="olist__line3">
                <span>{offering.slots_text}</span>
                {offering.location && <span className="olist__dot">·</span>}
                {offering.location && <span>{offering.location}</span>}
              </div>
            </div>

            <div className="olist__side">
              <RemainPill remaining={offering.remaining} capacity={offering.capacity} />
              {onAction && (
                <button
                  type="button"
                  className="card__btn"
                  disabled={offering.remaining === 0}
                  onClick={() => onAction({ action: 'enroll', offering })}
                  title="让 Agent 帮你选这门课（会先弹审批）"
                >
                  选课
                </button>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 班次详情
// ---------------------------------------------------------------------------
function OfferingDetail({
  payload,
  onAction,
}: {
  payload: AgentOffering & { found?: boolean }
  onAction?: (action: CardAction) => void
}) {
  if (!payload || payload.found === false) {
    return <div className="card card--empty">没有找到这个班次</div>
  }

  return (
    <Shell
      title={payload.course_name}
      meta={`${payload.course_code} · ${payload.section_no} 班`}
    >
      <div className="detail">
        <div className="detail__grid">
          <span className="detail__k">教师</span>
          <span className="detail__v">{payload.teacher_name || '待定'}</span>
          <span className="detail__k">时间</span>
          <span className="detail__v">{payload.slots_text}</span>
          <span className="detail__k">地点</span>
          <span className="detail__v">{payload.location || '待定'}</span>
          <span className="detail__k">学分</span>
          <span className="detail__v">{formatCredits(payload.credits)}</span>
        </div>

        <div className="detail__foot">
          <RemainPill remaining={payload.remaining} capacity={payload.capacity} />
          {onAction && payload.remaining > 0 && (
            <button
              type="button"
              className="card__btn"
              onClick={() => onAction({ action: 'enroll', offering: payload })}
            >
              选这门课
            </button>
          )}
        </div>
      </div>
    </Shell>
  )
}

// ---------------------------------------------------------------------------
// 周课表
// ---------------------------------------------------------------------------
interface SchedulePayload {
  term_name?: string
  total_credits?: number
  items?: Array<{
    offering_id: number
    course_name: string
    course_code: string
    weekday: number
    start_period: number
    end_period: number
    location: string
  }>
}

function ScheduleCard({ payload }: { payload: SchedulePayload }) {
  const items = payload?.items ?? []
  if (!items.length) {
    return <div className="card card--empty">这个学期还没有选课</div>
  }

  return (
    <Shell
      title="周课表"
      meta={`${payload.term_name ?? ''} · ${formatCredits(payload.total_credits ?? 0)} 学分`}
    >
      <div className="olist">
        {items.map((item) => (
          <div className="olist__row olist__row--tight" key={`${item.offering_id}-${item.weekday}-${item.start_period}`}>
            <div className="olist__main">
              <div className="olist__line1">
                <span className="olist__name">{item.course_name}</span>
                <span className="olist__code">{item.course_code}</span>
              </div>
              <div className="olist__line3">
                {WEEKDAY[item.weekday] ?? ''} 第 {item.start_period}-{item.end_period} 节
                {item.location ? ` · ${item.location}` : ''}
              </div>
            </div>
          </div>
        ))}
      </div>
    </Shell>
  )
}

// ---------------------------------------------------------------------------
// 学分
// ---------------------------------------------------------------------------
interface CreditPayload {
  term_name?: string
  used_credits?: number
  max_credits?: number
  remaining_credits?: number | null
  course_count?: number
  reason?: string
}

function CreditSummary({ payload }: { payload: CreditPayload }) {
  if (payload?.reason) {
    return <div className="card card--empty">{payload.reason}</div>
  }
  const used = payload?.used_credits ?? 0
  const max = payload?.max_credits ?? 0
  const percent = max > 0 ? Math.min(100, Math.round((used / max) * 100)) : 0

  return (
    <Shell title="学分" meta={payload.term_name}>
      <div className="credit">
        <span className="credit__value">{formatCredits(used)}</span>
        <span className="credit__max">/ {max > 0 ? formatCredits(max) : '未设置'}</span>
        <span className="credit__count">{payload.course_count ?? 0} 门</span>
      </div>
      <div className="credit__bar">
        <i style={{ width: `${percent}%` }} />
      </div>
      {payload.remaining_credits != null && (
        <div className="credit__tip">还可选 {formatCredits(payload.remaining_credits)} 学分</div>
      )}
    </Shell>
  )
}

// ---------------------------------------------------------------------------
// 冲突
// ---------------------------------------------------------------------------
interface ConflictPayload {
  has_conflict?: boolean
  conflicts?: Array<{
    weekday_name: string
    overlap: [number, number]
    left_name: string
    right_name: string
  }>
}

function ConflictCard({ payload }: { payload: ConflictPayload }) {
  const conflicts = payload?.conflicts ?? []
  if (!conflicts.length) {
    return <div className="card card--ok">没有时间冲突</div>
  }

  return (
    <Shell title="时间冲突" meta={`${conflicts.length} 处`} tone="warn">
      <div className="olist">
        {conflicts.map((item, index) => (
          <div className="olist__row olist__row--tight" key={index}>
            <div className="olist__main">
              <div className="olist__line1">
                <span className="olist__name">
                  {item.left_name} ↔ {item.right_name}
                </span>
              </div>
              <div className="olist__line3">
                {item.weekday_name} 第 {item.overlap[0]}-{item.overlap[1]} 节重叠
              </div>
            </div>
          </div>
        ))}
      </div>
    </Shell>
  )
}

// ---------------------------------------------------------------------------
// 选课结果
// ---------------------------------------------------------------------------
interface EnrollPayload {
  action?: 'enroll' | 'drop'
  ok?: boolean
  message?: string
  remaining?: number | null
  offering?: {
    course_name?: string
    course_code?: string
    section_no?: string
    slots_text?: string
  } | null
}

function EnrollResult({ payload }: { payload: EnrollPayload }) {
  const ok = Boolean(payload?.ok)
  return (
    <Shell
      title={`${payload.action === 'drop' ? '退课' : '选课'}${ok ? '成功' : '失败'}`}
      meta={ok ? undefined : '未生效'}
      tone={ok ? 'ok' : 'error'}
    >
      <div className="result">
        {payload.offering && (
          <div className="result__line">
            {payload.offering.course_name} {payload.offering.course_code}{' '}
            {payload.offering.section_no} 班
          </div>
        )}
        {payload.offering?.slots_text && (
          <div className="result__sub">{payload.offering.slots_text}</div>
        )}
        <div className="result__msg">{payload?.message}</div>
        {ok && payload.remaining != null && (
          <div className="result__sub">该班剩余名额 {payload.remaining}</div>
        )}
      </div>
    </Shell>
  )
}

// ---------------------------------------------------------------------------
// 通知
// ---------------------------------------------------------------------------
interface NoticePayload {
  title?: string
  body?: string
  level?: 'info' | 'warning' | 'urgent'
  ok?: boolean
}

function NoticeCard({ payload }: { payload: NoticePayload }) {
  const tone =
    payload?.level === 'urgent' ? 'error' : payload?.level === 'warning' ? 'warn' : undefined
  return (
    <Shell title={payload?.title ?? '通知'} tone={tone}>
      <div className="notice">{payload?.body}</div>
    </Shell>
  )
}

// ---------------------------------------------------------------------------
// 轮次
// ---------------------------------------------------------------------------
interface RoundPayload {
  open?: boolean
  name?: string
  opens_at?: string
  closes_at?: string
  max_credits?: number
  used_credits?: number
  remaining_credits?: number
  reason?: string
}

function RoundCard({ payload }: { payload: RoundPayload }) {
  if (payload?.reason) {
    return <div className="card card--empty">{payload.reason}</div>
  }
  return (
    <Shell
      title={payload.name ?? '选课轮次'}
      meta={payload.open ? '开放中' : '未开放'}
      tone={payload.open ? 'ok' : undefined}
    >
      <div className="detail__grid detail__grid--dense">
        <span className="detail__k">开放</span>
        <span className="detail__v">{payload.opens_at}</span>
        <span className="detail__k">关闭</span>
        <span className="detail__v">{payload.closes_at}</span>
        <span className="detail__k">上限</span>
        <span className="detail__v">{formatCredits(payload.max_credits ?? 0)}</span>
        <span className="detail__k">已用</span>
        <span className="detail__v">{formatCredits(payload.used_credits ?? 0)}</span>
      </div>
    </Shell>
  )
}

// ---------------------------------------------------------------------------
// 提醒
// ---------------------------------------------------------------------------
interface ReminderPayload {
  count?: number
  items?: Array<{ reminder_id: number; title: string; remind_at: string; note: string }>
}

function ReminderList({ payload }: { payload: ReminderPayload }) {
  const items = payload?.items ?? []
  if (!items.length) {
    return <div className="card card--empty">还没有保存提醒</div>
  }
  return (
    <Shell title="提醒" meta={`${items.length} 条`}>
      <div className="olist">
        {items.map((item) => (
          <div className="olist__row olist__row--tight" key={item.reminder_id}>
            <div className="olist__main">
              <div className="olist__line1">
                <span className="olist__name">{item.title}</span>
              </div>
              <div className="olist__line3">
                {item.remind_at}
                {item.note ? ` · ${item.note}` : ''}
              </div>
            </div>
          </div>
        ))}
      </div>
    </Shell>
  )
}

// ---------------------------------------------------------------------------
// 学期
// ---------------------------------------------------------------------------
interface TermPayload {
  found?: boolean
  code?: string
  name?: string
  starts_on?: string
  ends_on?: string
}

function TermCard({ payload }: { payload: TermPayload }) {
  if (!payload?.found) {
    return <div className="card card--empty">系统里还没有设置当前学期</div>
  }
  return (
    <Shell title={payload.name ?? '当前学期'} meta={payload.code}>
      <div className="olist__line3">
        {payload.starts_on} ~ {payload.ends_on}
      </div>
    </Shell>
  )
}

// ---------------------------------------------------------------------------
function FallbackCard({ card, payload }: { card: string; payload: unknown }) {
  return (
    <Shell title={card} meta="未识别的卡片类型">
      <pre className="card__json">{JSON.stringify(payload, null, 2)}</pre>
    </Shell>
  )
}
