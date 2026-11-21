import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Empty, Spin } from 'antd'
import { useQuery } from '@tanstack/react-query'

import {
  agentApi,
  type AgentMetrics,
  type AgentRunRow,
  type AgentStepRow,
} from '../../../api/agent'
import { PageHeader } from '../../../components/PageHeader'
import { formatDateTime } from '../../../utils/format'
import './index.css'

/** 运行状态 → 圆点颜色 + 文案 */
const RUN_STATUS: Record<number, { text: string; tone: string }> = {
  1: { text: '进行中', tone: 'running' },
  2: { text: '完成', tone: 'ok' },
  3: { text: '失败', tone: 'failed' },
  4: { text: '已取消', tone: 'muted' },
}

/** 意图 → 展示 */
const INTENT_LABELS: Record<string, string> = {
  query_compute: '查询 / 计算',
  write: '写操作',
  meta: '闲聊 / 元问题',
}

/** 步骤类型 → 展示。tone 决定时间轴圆点颜色。 */
const STEP_KINDS: Record<string, { label: string; tone: string }> = {
  intent: { label: '意图识别', tone: 'gold' },
  clarify: { label: '澄清', tone: 'muted' },
  // 澄清越澄清越差 → 回流治理层。用 failed 色标出来，
  // 因为这是"路由层质量有问题"的信号，不该淹没在普通步骤里。
  intent_regression: { label: '澄清回归', tone: 'failed' },
  graph: { label: '任务图', tone: 'muted' },
  node_status: { label: '节点状态', tone: 'muted' },
  agent_started: { label: '子 agent 启动', tone: 'muted' },
  agent_finished: { label: '子 agent 结束', tone: 'muted' },
  tool_call: { label: '工具调用', tone: 'ok' },
  tool_result: { label: '工具结果', tone: 'ok' },
  code_run: { label: '代码执行', tone: 'gold' },
  code_output: { label: '代码输出', tone: 'gold' },
  approval_required: { label: '待审批', tone: 'gold' },
  approval_result: { label: '审批结果', tone: 'ok' },
  budget: { label: '预算告警', tone: 'failed' },
  card: { label: '结果卡片', tone: 'muted' },
  error: { label: '错误', tone: 'failed' },
}

const AGENT_LABELS: Record<string, string> = {
  query: '查询',
  compute: '计算',
  execute: '执行',
  notify: '通知',
}

/**
 * Trace 观测面板：左边运行列表，右边该运行的步骤时间轴。
 *
 * 为什么改成主从布局而不是"表格 + 下方详情"：
 *   原来的表头有 7 列，每列都很窄，标题被 ellipsis 截掉大半；
 *   点"详情"之后还要往下滚才能看到步骤，来回对照很累。
 *   现在列表只保留"够你认出是哪次运行"的信息（状态、标题、时间、耗时），
 *   细节全部放右边，切换运行不用滚动。
 */
export function AgentTracePage() {
  const [selected, setSelected] = useState<string>('')

  // 运行轨迹与对话内容**严格按账号隔离**，管理员也不例外。
  // 能看到的是自己的运行，聚合指标不含任何对话内容。
  const runsQuery = useQuery({
    queryKey: ['agent-runs'],
    queryFn: () => agentApi.runs(),
    refetchInterval: 8000,
  })

  const metricsQuery = useQuery({
    queryKey: ['agent-metrics'],
    queryFn: () => agentApi.metrics(),
    refetchInterval: 15000,
  })

  const detailQuery = useQuery({
    queryKey: ['agent-run', selected],
    queryFn: () => agentApi.runDetail(selected),
    enabled: Boolean(selected),
  })

  const runs = useMemo(() => runsQuery.data?.items ?? [], [runsQuery.data])

  // 首次加载后自动选中最近一次运行 —— 打开页面就有东西看，少一次点击
  useEffect(() => {
    if (!selected && runs.length > 0) {
      setSelected(runs[0].run_id)
    }
  }, [runs, selected])

  const run = detailQuery.data?.run
  const steps = detailQuery.data?.steps ?? []

  return (
    <div className="trace">
      <PageHeader
        title="Agent Trace"
        subtitle="每次运行里，哪个子 agent 调了什么工具、输入输出是什么、花了多久。"
        extra={
          <Button size="small" onClick={() => void runsQuery.refetch()}>
            刷新
          </Button>
        }
      />

      {metricsQuery.data && <MetricsStrip metrics={metricsQuery.data} />}

      <div className="trace__body">
        {/* ---------- 左：运行列表 ---------- */}
        <aside className="trace__list">
          {runsQuery.isLoading && (
            <div className="trace__list-hint">
              <Spin size="small" />
            </div>
          )}

          {!runsQuery.isLoading && runs.length === 0 && (
            <div className="trace__list-hint">还没有运行记录</div>
          )}

          {runs.map((item) => {
            const meta = RUN_STATUS[item.status]
            const active = item.run_id === selected
            return (
              <button
                key={item.run_id}
                type="button"
                className={`trow${active ? ' trow--active' : ''}`}
                onClick={() => setSelected(item.run_id)}
                title={item.title}
              >
                <span className={`trow__dot trow__dot--${meta?.tone ?? 'muted'}`} />
                <span className="trow__main">
                  <span className="trow__title">{item.title || '（空输入）'}</span>
                  <span className="trow__meta">
                    <span>{clockOf(item.started_at)}</span>
                    <span className="trow__sep">·</span>
                    <span>{durationOf(item)}</span>
                    <span className="trow__sep">·</span>
                    <span>{item.step_count} 步</span>
                  </span>
                </span>
              </button>
            )
          })}
        </aside>

        {/* ---------- 右：选中运行的详情 ---------- */}
        <section className="trace__detail">
          {!selected && <Empty description="从左边选一次运行" image={Empty.PRESENTED_IMAGE_SIMPLE} />}

          {selected && detailQuery.isLoading && (
            <div className="trace__detail-hint">
              <Spin size="small" />
            </div>
          )}

          {selected && detailQuery.isError && (
            <Alert
              type="error"
              showIcon
              message="轨迹加载失败"
              description="可能这条记录不存在，或不属于当前账号。"
            />
          )}

          {run && (
            <>
              <header className="trace__head">
                <div className="trace__head-title">
                  <span className={`trow__dot trow__dot--${RUN_STATUS[run.status]?.tone ?? 'muted'}`} />
                  <h2>{run.title || '（空输入）'}</h2>
                </div>
                <div className="trace__head-meta">
                  <Chip label="状态" value={RUN_STATUS[run.status]?.text ?? String(run.status)} />
                  <Chip label="意图" value={INTENT_LABELS[run.intent] ?? run.intent ?? '—'} />
                  <Chip label="置信度" value={`${(run.confidence * 100).toFixed(0)}%`} />
                  <Chip label="耗时" value={durationOf(run)} />
                  <Chip label="模型" value={run.model || '—'} />
                  <Chip
                    label="token"
                    value={String(run.prompt_tokens + run.completion_tokens)}
                  />
                  <Chip label="开始" value={formatDateTime(run.started_at)} />
                  <Chip label="run" value={run.run_id} />
                </div>
              </header>

              {steps.length === 0 ? (
                <Empty
                  description="这次运行还没有落库的步骤（可能仍在进行中）"
                  image={Empty.PRESENTED_IMAGE_SIMPLE}
                />
              ) : (
                <div className="tsteps">
                  {steps.map((step) => (
                    <StepRow key={step.seq} step={step} />
                  ))}
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
function StepRow({ step }: { step: AgentStepRow }) {
  const meta = STEP_KINDS[step.kind] ?? { label: step.kind, tone: 'muted' }
  const payload = step.output_json || step.input_json
  const hasDetail = Boolean(payload) || Boolean(step.error)

  return (
    <div className={`tstep tstep--${meta.tone}`}>
      <span className="tstep__dot" />

      <div className="tstep__body">
        <div className="tstep__line">
          <span className="tstep__seq">{String(step.seq).padStart(2, '0')}</span>
          <span className="tstep__kind">{meta.label}</span>

          {step.agent && (
            <span className="tstep__agent">{AGENT_LABELS[step.agent] ?? step.agent}</span>
          )}

          {step.name && <span className="tstep__name">{step.name}</span>}

          <span className="tstep__spacer" />

          {step.latency_ms != null && (
            <span className="tstep__ms">{step.latency_ms}ms</span>
          )}
        </div>

        {step.error && <div className="tstep__error">{step.error}</div>}

        {hasDetail && !step.error && (
          <details className="tstep__raw">
            <summary>展开原始数据</summary>
            {/* 输入和输出都摆出来：排查时经常是"输入对但输出不对" */}
            {step.input_json && (
              <div className="tstep__raw-block">
                <span className="tstep__raw-label">输入</span>
                <pre>{prettyJson(step.input_json)}</pre>
              </div>
            )}
            {step.output_json && (
              <div className="tstep__raw-block">
                <span className="tstep__raw-label">输出</span>
                <pre>{prettyJson(step.output_json)}</pre>
              </div>
            )}
          </details>
        )}
      </div>
    </div>
  )
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="tchip">
      <span className="tchip__label">{label}</span>
      <span className="tchip__value">{value}</span>
    </span>
  )
}

/**
 * 评估指标条。
 *
 * 观测（右边的时间轴）回答"这一次发生了什么"，
 * 评估（这一条）回答"整体靠不靠谱"：成功率、澄清率、直连与沙箱比例、审批通过率。
 */
function MetricsStrip({ metrics }: { metrics: AgentMetrics }) {
  const items = [
    { label: '运行', value: String(metrics.runs) },
    { label: '成功', value: String(metrics.succeeded) },
    { label: '失败', value: String(metrics.failed) },
    { label: '成功率', value: `${metrics.success_rate}%` },
    { label: '澄清率', value: `${metrics.clarify_rate}%` },
    { label: '平均步数', value: String(metrics.avg_steps) },
    { label: '平均 token', value: String(Math.round(metrics.avg_tokens)) },
    { label: '工具调用', value: String(metrics.tool_calls) },
    { label: '代码执行', value: String(metrics.code_runs) },
    { label: '写操作', value: String(metrics.write_ops) },
    { label: '审批通过', value: `${metrics.approved_count}/${metrics.approvals}` },
  ]

  return (
    <div className="trace__metrics">
      {items.map((item) => (
        <div className="trace__metric" key={item.label}>
          <span className="trace__metric-value">{item.value}</span>
          <span className="trace__metric-label">{item.label}</span>
        </div>
      ))}
      <div className="trace__metric trace__metric--note">
        <span className="trace__metric-label">近 {metrics.window_days} 天</span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
/** 时长：优先用 finished-started，没有结束时间就显示"进行中" */
function durationOf(run: AgentRunRow): string {
  if (!run.finished_at) {
    return run.status === 1 ? '进行中' : '—'
  }
  const started = Date.parse(run.started_at)
  const finished = Date.parse(run.finished_at)
  if (Number.isNaN(started) || Number.isNaN(finished) || finished < started) {
    return '—'
  }
  const seconds = (finished - started) / 1000
  return seconds < 10 ? `${seconds.toFixed(1)}s` : `${Math.round(seconds)}s`
}

/** 只取时分秒 —— 日期在详情里已经有了，列表里省点宽度 */
function clockOf(iso: string): string {
  const text = formatDateTime(iso)
  const parts = text.split(' ')
  return parts.length > 1 ? parts[1] : text
}

/** 把 JSON 字符串格式化；不是 JSON 就原样返回（截断保护） */
function prettyJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2).slice(0, 4000)
  } catch {
    return text.slice(0, 2000)
  }
}
