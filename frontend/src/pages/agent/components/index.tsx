import { useEffect, useRef, useState } from 'react'
import { Button, Input, Tag } from 'antd'

import type {
  AgentStepState,
  ClarifyRequest,
  GraphNode,
  IntentInfo,
  PendingApproval,
} from '../../../api/agent'
import './index.css'

export const AGENT_LABELS: Record<string, string> = {
  query: '查询',
  compute: '计算',
  execute: '执行',
  notify: '通知',
}

export const INTENT_LABELS: Record<string, string> = {
  query_compute: '查询 / 计算',
  write: '写操作',
  meta: '闲聊 / 元问题',
}

/** 工具名 → 人话。执行过程要显示"在做什么"，不能显示裸函数名。 */
const TOOL_LABELS: Record<string, string> = {
  search_offerings: '查询班次',
  get_offering: '查看班次详情',
  my_schedule: '读取课表',
  my_enrollments: '读取已选课程',
  get_current_round: '读取选课轮次',
  get_current_term: '读取当前学期',
  list_terms: '读取学期列表',
  compute_credit_summary: '核算学分',
  compute_conflicts: '检测冲突',
  compute_remaining_ranking: '统计余量',
  compute_free_slots: '计算空档',
  compute_candidates: '筛选候选课程',
  enroll_offering: '选课',
  drop_offering: '退课',
  compose_notice: '生成通知',
  save_reminder: '保存提醒',
  list_reminders: '读取提醒',
  execute_code: '执行代码',
}

function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? tool
}

/**
 * 状态 → 样式类，写成显式映射而不是模板拼接。
 *
 * 两个理由：类名以字面量出现在源码里，CSS 审计脚本能查到；
 * 以后加状态时类型系统会提醒你补一条。
 */
const NODE_CLASS: Record<GraphNode['status'], string> = {
  pending: 'pstep--pending',
  running: 'pstep--running',
  done: 'pstep--done',
  failed: 'pstep--failed',
  skipped: 'pstep--skipped',
}

const ITEM_CLASS: Record<'running' | 'ok' | 'failed', string> = {
  running: 'pstep__item--running',
  ok: 'pstep__item--ok',
  failed: 'pstep__item--failed',
}

// ---------------------------------------------------------------------------
/** 门控档位 → 一句话。让用户看懂"为什么这次要我确认"。 */
const BAND_LABELS: Record<string, string> = {
  direct: '直接执行',
  confirm: '需确认',
  option: '需澄清',
  reject: '听不懂',
  regress: '澄清无进展',
  exhausted: '已放弃澄清',
}

export function IntentChip({ intent }: { intent: IntentInfo | null }) {
  if (!intent) return null

  // 档位比裸置信度更说明问题：0.94 和 0.96 只差 2 个点，
  // 但一个是"要你确认"、一个是"直接执行"。所以有 band 就显示 band。
  const band = intent.band ?? ''
  const label = BAND_LABELS[band] ?? ''
  const tone =
    band === 'direct' ? 'gold' : band === 'reject' || band === 'regress' || band === 'exhausted'
      ? 'red'
      : undefined

  return (
    <Tag color={tone} className="a-chip">
      {INTENT_LABELS[intent.intent] ?? intent.intent}
      {label && <span className="a-chip__band">{label}</span>}
      <span className="a-chip__conf">{(intent.confidence * 100).toFixed(0)}%</span>
    </Tag>
  )
}

// ---------------------------------------------------------------------------
// 执行过程：一条一条往下流，不套框
//
// 参考 dsh 的做法：**过程不是"一个卡片"，而是一串行**。
// 每一行在它的 SSE 事件到达时就出现，没有折叠、没有边框，
// 左侧一条时间轴把它和消息区分开。
// ---------------------------------------------------------------------------
interface ProcessStreamProps {
  nodes: GraphNode[]
  agents: AgentStepState[]
  running: boolean
}

export function ProcessStream({ nodes, agents, running }: ProcessStreamProps) {
  if (nodes.length === 0) return null

  return (
    <div className={`pstream${running ? ' pstream--live' : ''}`}>
      {nodes.map((node) => {
        const card = agents.find(
          (item) => item.agent === node.agent && item.goal === node.goal
        )
        return (
          <div key={node.id} className={`pstep ${NODE_CLASS[node.status]}`}>
            <div className="pstep__head">
              <span className="pstep__mark" />
              <span className="pstep__agent">{AGENT_LABELS[node.agent] ?? node.agent}</span>
              <span className="pstep__goal">{node.goal}</span>
              {node.dynamic && <span className="pstep__tag">动态</span>}
            </div>

            {card?.items.map((item) => (
              <div key={item.id} className={`pstep__item ${ITEM_CLASS[item.status]}`}>
                <span className="pstep__mark" />
                <span className="pstep__tool">
                  {item.channel === 'code' ? '执行代码' : toolLabel(item.tool)}
                </span>
                <span className="pstep__chan">{item.channel === 'code' ? '沙箱' : '直连'}</span>
                {item.durationMs != null && (
                  <span className="pstep__ms">{item.durationMs}ms</span>
                )}
                {item.status === 'failed' && <span className="pstep__fail">失败</span>}
              </div>
            ))}
          </div>
        )
      })}
    </div>
  )
}

// ---------------------------------------------------------------------------
interface ActivityLineProps {
  agents: AgentStepState[]
  nodes: GraphNode[]
  running: boolean
  streaming: boolean
  approval: PendingApproval | null
  error: string | null
  stage?: string
  idleS?: number
}

export function ActivityLine({
  agents,
  nodes,
  running,
  streaming,
  approval,
  error,
  stage = '',
  idleS = 0,
}: ActivityLineProps) {
  const [elapsed, setElapsed] = useState(0)
  const startedAt = useRef<number>(0)

  useEffect(() => {
    if (running && startedAt.current === 0) {
      startedAt.current = Date.now()
    }
    if (!running) {
      startedAt.current = 0
      setElapsed(0)
      return
    }
    const timer = window.setInterval(() => {
      if (startedAt.current) {
        setElapsed(Date.now() - startedAt.current)
      }
    }, 200)
    return () => window.clearInterval(timer)
  }, [running])

  if (error) {
    return (
      <div className="actbar actbar--error">
        <span className="actbar__dot" />
        <span className="actbar__text">{error}</span>
      </div>
    )
  }

  if (approval) {
    return (
      <div className="actbar actbar--wait">
        <span className="actbar__dot" />
        <span className="actbar__text">等待你确认「{approval.summary}」</span>
      </div>
    )
  }

  if (!running) {
    if (nodes.length === 0) return null
    return (
      <div className="actbar actbar--idle">
        <span className="actbar__dot" />
        <span className="actbar__text">完成 · {nodes.length} 个步骤</span>
      </div>
    )
  }

  const current = [...agents].reverse().find((card) => card.status === 'running')
  const lastItem = current?.items[current.items.length - 1]

  let text = stage || '正在理解你的问题…'
  if (streaming) {
    text = '正在生成回答…'
  } else if (current && lastItem) {
    const who = AGENT_LABELS[current.agent] ?? current.agent
    const verb = lastItem.channel === 'code' ? '执行代码' : toolLabel(lastItem.tool)
    text = `${who} · ${verb}${lastItem.status === 'running' ? '…' : ' ✓'}`
  }

  const stalled = running && idleS >= 8
  if (stalled) {
    text = `${text} · 已等待 ${idleS.toFixed(0)}s`
  }

  return (
    <div className={`actbar${stalled ? ' actbar--stalled' : ''}`}>
      <span className="actbar__pulse">
        <i />
        <i />
      </span>
      <span className="actbar__text">{text}</span>
      <span className="actbar__time">{(elapsed / 1000).toFixed(1)}s</span>
    </div>
  )
}

// ---------------------------------------------------------------------------
/** 澄清维度 → 人话。让用户知道"系统在确认哪一件事"。 */
const DIMENSION_LABELS: Record<string, string> = {
  intent: '你的意图',
  action: '具体动作',
  course: '哪门课程',
  confirm: '整体确认',
}

/**
 * 意图澄清弹窗。
 *
 * 为什么是弹窗而不是聊天气泡：
 *   澄清意味着"这一轮还没真正开始"，后端那个 run 正挂起等回答。
 *   如果做成一条助手消息，看起来就像"任务已经结束了、请你重新提问"，
 *   而实际上答完之后同一个 run 会接着往下跑。弹窗能准确表达"在等你"。
 *
 * 一次只问一个维度：弹窗给的是**一个**问题 + 该维度的几个选项，
 * 不会一次抛三个问题让用户不知道先答哪个。
 */
export function ClarifyModal({
  clarify,
  onAnswer,
}: {
  clarify: ClarifyRequest
  onAnswer: (text: string) => void | Promise<void>
}) {
  const [text, setText] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [remaining, setRemaining] = useState(clarify.timeout_s)

  useEffect(() => {
    setRemaining(clarify.timeout_s)
    const timer = window.setInterval(() => {
      setRemaining((prev) => (prev > 0 ? prev - 1 : 0))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [clarify.clarification_id, clarify.timeout_s])

  const submit = async (value: string) => {
    const answer = value.trim()
    if (!answer || submitting) return
    setSubmitting(true)
    try {
      await onAnswer(answer)
    } finally {
      setSubmitting(false)
    }
  }

  const options = clarify.options ?? []
  // 有选项时只显示问句首行：完整文本里带着 1./2./3. 的列表，
  // 和下面的可点选项重复了。
  const prompt = options.length > 0 ? clarify.question.split('\n')[0] : clarify.question

  return (
    <div className="clarify" role="dialog" aria-modal="true" aria-label="意图澄清">
      <div className="clarify__mask" />
      <div className="clarify__card">
        <div className="clarify__head">
          <span className="clarify__badge">需要确认你的意思</span>
          {clarify.dimension && (
            <span className="clarify__round">{DIMENSION_LABELS[clarify.dimension] ?? clarify.dimension}</span>
          )}
          {clarify.round > 1 && <span className="clarify__round">第 {clarify.round} 轮</span>}
          <span className="clarify__timer">{remaining}s 后自动放弃</span>
        </div>

        <div className="clarify__body">
          <p className="clarify__question">{prompt}</p>

          {options.length > 0 && (
            <div className="clarify__options">
              {options.map((item, index) => (
                <button
                  key={`${index}-${item.label ?? ''}`}
                  type="button"
                  className="clarify__option"
                  disabled={submitting}
                  onClick={() => void submit(item.label ?? String(index + 1))}
                >
                  <span className="clarify__option-no">{index + 1}</span>
                  <span className="clarify__option-text">{item.label ?? ''}</span>
                </button>
              ))}
            </div>
          )}

          <div className="clarify__free">
            <Input
              value={text}
              onChange={(event) => setText(event.target.value)}
              onPressEnter={() => void submit(text)}
              placeholder="也可以直接说一句更具体的…"
              disabled={submitting}
              variant="borderless"
              className="clarify__input"
            />
            <Button
              type="primary"
              size="small"
              loading={submitting}
              disabled={!text.trim()}
              onClick={() => void submit(text)}
            >
              提交
            </Button>
          </div>

          <div className="clarify__hint">这轮任务会等你回答后继续，不用重新提问。</div>
        </div>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
interface ApprovalPanelProps {
  approval: PendingApproval
  onDecide: (approved: boolean) => void | Promise<void>
}

/**
 * 写操作审批。
 *
 * 这是唯一一个**故意做成"框"**的组件：写操作必须显眼、不可忽略。
 * 过程不套框，审批要套框 —— 两者信息性质不同。
 *
 * 特别注意"卡在执行"这个体验问题：审批会阻塞整个运行（最长 90s），
 * 所以这里必须同时给出：做什么、倒计时进度、是否还有后续审批。
 */
export function ApprovalPanel({ approval, onDecide }: ApprovalPanelProps) {
  const [remaining, setRemaining] = useState(approval.timeout_s)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    setRemaining(approval.timeout_s)
    const timer = window.setInterval(() => {
      setRemaining((prev) => (prev > 0 ? prev - 1 : 0))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [approval.approval_id, approval.timeout_s])

  const decide = async (approved: boolean) => {
    setSubmitting(true)
    try {
      await onDecide(approved)
    } finally {
      setSubmitting(false)
    }
  }

  const ratio = approval.timeout_s > 0 ? Math.max(0, remaining / approval.timeout_s) : 0
  const queued = (approval.pending ?? 0) > 0

  return (
    <div className="approval">
      <div className="approval__head">
        <span className="approval__badge">需要你确认</span>
        {queued && (
          <span className="approval__queue">还有 {approval.pending} 个待处理</span>
        )}
        <span className="approval__timer">{remaining}s 后自动取消</span>
      </div>

      {/* 倒计时进度条：让人一眼看到"时间在走"，而不是以为卡住了 */}
      <div className="approval__bar">
        <i style={{ width: `${ratio * 100}%` }} />
      </div>

      <div className="approval__body">
        <div className="approval__summary">{approval.summary}</div>
        <div className="approval__meta">
          由「{AGENT_LABELS[approval.agent] ?? approval.agent}」发起 · {approval.tool}
        </div>
        <pre className="approval__args">{JSON.stringify(approval.args, null, 2)}</pre>
      </div>

      <div className="approval__actions">
        <Button danger loading={submitting} onClick={() => decide(false)}>
          拒绝
        </Button>
        <Button type="primary" loading={submitting} onClick={() => decide(true)}>
          允许执行
        </Button>
      </div>
    </div>
  )
}
