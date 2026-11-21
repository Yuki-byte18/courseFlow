import { request, streamSSE, type SseHandler } from '../request/client'

// ---------------------------------------------------------------------------
// Key 校验
// ---------------------------------------------------------------------------
export interface VerifyKeyResult {
  ok: boolean
  models: string[]
  default: string
}

// ---------------------------------------------------------------------------
// AG-UI 状态树
//
// 前端不需要理解业务事件，只需要消费这棵树：
//   STATE_SNAPSHOT 给初始值，STATE_DELTA 用 JSON Patch 打补丁。
// ---------------------------------------------------------------------------
export interface AgentSlot {
  weekday: number
  start_period: number
  end_period: number
}

export interface AgentOffering {
  offering_id: number
  course_code: string
  course_name: string
  credits: number
  department: string
  teacher_name: string
  section_no: string
  location: string
  capacity: number
  taken: number
  remaining: number
  status: number
  slots: AgentSlot[]
  slots_text: string
}

export type GraphNodeStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

export interface GraphNode {
  id: string
  agent: string
  goal: string
  deps: string[]
  status: GraphNodeStatus
  dynamic: boolean
  summary: string
  error: string
}

export interface TraceItem {
  id: string
  tool: string
  channel: 'direct' | 'code'
  status: 'running' | 'ok' | 'failed'
  detail: string
  durationMs?: number
}

export interface AgentStepState {
  agent: string
  goal: string
  status: GraphNodeStatus
  summary: string
  items: TraceItem[]
}

export interface PendingApproval {
  approval_id: string
  agent: string
  tool: string
  summary: string
  args: Record<string, unknown>
  /** 多少秒后自动取消（超时一律按"拒绝"处理） */
  timeout_s: number
  /** 队列里还有几个待确认。一次选两门课会有两次审批。 */
  pending?: number
}

/**
 * 意图澄清请求：路由层没听准，弹窗问一句。
 *
 * 注意这是**弹窗**而不是聊天消息 —— 本轮任务会挂起等回答，
 * 答完同一个 run 继续，不需要用户重新提问。
 */
export interface ClarifyRequest {
  clarification_id: string
  question: string
  /** 可点的候选；每个选项带 dimension + value，点了直接落位到对应维度 */
  options: Array<{ intent?: string; label?: string; dimension?: string; value?: string }>
  /** 第几轮澄清 */
  round: number
  /** 多少秒后自动放弃 */
  timeout_s: number
  /** 这一轮澄清的是哪个维度：intent / action / course / confirm */
  dimension?: string
  /** 问题形态：confirm（确认句）/ option（选项式）/ followup（追问式） */
  kind?: string
}

export interface AgentCardItem {
  card: string
  payload: unknown
}

export interface IntentInfo {
  intent: 'query_compute' | 'write' | 'meta' | 'unknown'
  confidence: number
  reason: string
  needs_clarify: boolean
  /** 意图路由层门控档位：direct / confirm / option / reject / regress / exhausted */
  band?: string
  /** 路由结果：pass / clarify / reject */
  route?: string
  /** 第几轮澄清（0 表示没在澄清） */
  clarify_round?: number
  /** 已抽到的槽位（跨轮增量累积） */
  slots?: {
    action?: string
    course_codes?: string[]
    weekday?: number | null
    count?: number | null
  }
}

/** 与 Python 侧 AgUiEncoder.state 完全对应 */
export interface AgentState {
  intent: IntentInfo | null
  nodes: GraphNode[]
  agents: Record<string, AgentStepState>
  approval: PendingApproval | null
  cards: AgentCardItem[]
  budget: { dimension: string; snapshot: Record<string, number> } | null
}

export const EMPTY_AGENT_STATE: AgentState = {
  intent: null,
  nodes: [],
  agents: {},
  approval: null,
  cards: [],
  budget: null,
}

// ---------------------------------------------------------------------------
// AG-UI 事件
// ---------------------------------------------------------------------------
export interface JsonPatchOp {
  op: 'add' | 'remove' | 'replace'
  path: string
  value?: unknown
}

export type AgUiEvent =
  | { type: 'RUN_STARTED'; threadId: string; runId: string; timestamp: number }
  | {
      type: 'RUN_FINISHED'
      threadId: string
      runId: string
      result?: { status?: string; usage?: Record<string, unknown> }
      timestamp: number
    }
  | { type: 'RUN_ERROR'; message: string; code?: string }
  | { type: 'STEP_STARTED'; stepName: string }
  | { type: 'STEP_FINISHED'; stepName: string }
  | { type: 'TEXT_MESSAGE_START'; messageId: string }
  | { type: 'TEXT_MESSAGE_CONTENT'; messageId: string; delta: string }
  | { type: 'TEXT_MESSAGE_END'; messageId: string }
  | { type: 'TOOL_CALL_START'; toolCallId: string; toolCallName: string; parentMessageId?: string }
  | { type: 'TOOL_CALL_ARGS'; toolCallId: string; delta: string }
  | { type: 'TOOL_CALL_END'; toolCallId: string }
  | { type: 'STATE_SNAPSHOT'; snapshot: AgentState }
  | { type: 'STATE_DELTA'; delta: JsonPatchOp[] }
  | { type: 'CUSTOM'; name: string; value: unknown }

// ---------------------------------------------------------------------------
// 对话记录 / 轨迹 / 指标
// ---------------------------------------------------------------------------
export interface AgentThread {
  thread_id: string
  title: string
  message_count: number
  last_message_at: string | null
  created_at: string
}

export interface AgentMessage {
  id: number
  run_id: string
  role: 'user' | 'assistant'
  content: string
  cards_json: string
  created_at: string
}

export interface AgentRunRow {
  run_id: string
  title: string
  intent: string
  confidence: number
  status: number
  model: string
  prompt_tokens: number
  completion_tokens: number
  started_at: string
  finished_at: string | null
  step_count: number
}

export interface AgentStepRow {
  seq: number
  kind: string
  agent: string
  name: string
  status: number
  input_json: string
  output_json: string
  error: string
  latency_ms: number | null
  created_at: string
}

export interface AgentRunDetail {
  run: AgentRunRow
  steps: AgentStepRow[]
}

/** 评测层的聚合指标（观测看单次，评估看一批） */
export interface AgentMetrics {
  window_days: number
  runs: number
  succeeded: number
  failed: number
  success_rate: number
  clarify_rate: number
  avg_steps: number
  avg_tokens: number
  tool_calls: number
  code_runs: number
  direct_calls: number
  write_ops: number
  approvals: number
  approved_count: number
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
export const agentApi = {
  verifyKey(apiKey: string): Promise<VerifyKeyResult> {
    return request<VerifyKeyResult>('/agent/verify-key', {
      method: 'POST',
      headers: { 'X-LLM-Api-Key': apiKey },
    })
  },

  /**
   * 发起一次对话，事件按 AG-UI 协议逐条回调。
   *
   * 走 SSE：Agent 的每一步都要实时渲染成界面状态，
   * 只给最后一段文本是不够的。
   */
  chatStream(
    payload: { message: string; model: string; threadId?: string },
    apiKey: string,
    onEvent: SseHandler,
    signal?: AbortSignal
  ): Promise<void> {
    return streamSSE(
      '/agent/chat',
      { body: payload, headers: { 'X-LLM-Api-Key': apiKey }, signal },
      onEvent
    )
  },

  decideApproval(approvalId: string, approved: boolean): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/agent/approvals/${approvalId}/decide`, {
      method: 'POST',
      body: { approved },
    })
  },

  /**
   * 回答意图澄清。
   *
   * 和审批一样是"闸门"：后端那个运行正挂起等这个回答，
   * 回答一提交，**同一个 run 会继续往下跑**，前端不需要重新发 chat。
   */
  answerClarification(clarificationId: string, text: string): Promise<{ ok: boolean }> {
    return request<{ ok: boolean }>(`/agent/clarifications/${clarificationId}/answer`, {
      method: 'POST',
      body: { text },
    })
  },

  threads(): Promise<{ items: AgentThread[]; count: number }> {
    return request<{ items: AgentThread[]; count: number }>('/agent/threads')
  },

  messages(threadId: string): Promise<{ thread_id: string; items: AgentMessage[]; count: number }> {
    return request<{ thread_id: string; items: AgentMessage[]; count: number }>(
      `/agent/threads/${threadId}/messages`
    )
  },

  deleteThread(threadId: string): Promise<void> {
    return request<void>(`/agent/threads/${threadId}`, { method: 'DELETE' })
  },

  runs(): Promise<{ items: AgentRunRow[]; count: number }> {
    // 不提供"看别人的"参数：运行记录里的 title 就是用户原话，属于对话内容。
    // 管理员只能看 /agent/metrics 的聚合数字，看不到内容。
    return request<{ items: AgentRunRow[]; count: number }>('/agent/runs')
  },

  runDetail(runId: string): Promise<AgentRunDetail> {
    return request<AgentRunDetail>(`/agent/runs/${runId}`)
  },

  metrics(days = 30): Promise<AgentMetrics> {
    return request<AgentMetrics>('/agent/metrics', { query: { days } })
  },

  health(): Promise<Record<string, unknown>> {
    return request<Record<string, unknown>>('/agent/health')
  },
}

// ---------------------------------------------------------------------------
// JSON Patch（只支持 replace，够用且好懂）
// ---------------------------------------------------------------------------
export function applyPatch(state: AgentState, ops: JsonPatchOp[]): AgentState {
  let next: AgentState = {
    intent: state.intent,
    nodes: state.nodes,
    agents: { ...state.agents },
    approval: state.approval,
    cards: state.cards,
    budget: state.budget,
  }

  for (const op of ops) {
    if (op.op !== 'replace') continue
    const parts = op.path.split('/').filter(Boolean)
    if (parts.length === 1) {
      const key = parts[0] as keyof AgentState
      next = { ...next, [key]: op.value } as AgentState
    }
  }

  return next
}
