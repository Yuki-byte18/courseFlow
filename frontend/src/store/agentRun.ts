import { create } from 'zustand'

import {
  agentApi,
  applyPatch,
  EMPTY_AGENT_STATE,
  type AgentCardItem,
  type AgentState,
  type AgUiEvent,
  type ClarifyRequest,
  type PendingApproval,
} from '../api/agent'
import { useLlmStore } from './llm'
import { selectCards } from '../utils/cards'

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  /** 这条回复附带的业务卡片。思考过程中的卡片不渲染，只跟着回答走。 */
  cards?: AgentCardItem[]
}

/** 一个会话的运行态。按 threadId 分开存，切走再切回来能原样恢复。 */
export interface ThreadRuntime {
  messages: ChatMessage[]
  agentState: AgentState
  streaming: string
  running: boolean
  runId: string
  error: string | null
  /** 后端上报的当前阶段 */
  stage: string
  /** 距离上一次后端事件的秒数 */
  idleS: number
  /**
   * 待确认的写操作。
   *
   * **刻意和 agentState.approval 分开存一份。**
   * 审批走两条通道：STATE_DELTA 的 /approval 补丁，和一条 CUSTOM 事件。
   * 任何一条先到都能把面板弹出来 —— 只依赖其中一条，
   * 一旦那条链路上出问题，用户就会看到"审批卡片不弹、任务卡在等待"。
   */
  pendingApproval: PendingApproval | null
  /**
   * 待回答的意图澄清。
   *
   * 这是**弹窗**状态而不是消息：后端那个 run 正挂起等回答，
   * 答完同一个 run 继续跑。所以它既不能变成气泡，也不能让
   * `running` 变成 false —— 否则界面会显示"完成"，其实是"在等你"。
   */
  pendingClarify: ClarifyRequest | null
}

export const EMPTY_RUNTIME: ThreadRuntime = {
  messages: [],
  agentState: EMPTY_AGENT_STATE,
  streaming: '',
  running: false,
  runId: '',
  error: null,
  stage: '',
  idleS: 0,
  pendingApproval: null,
  pendingClarify: null,
}

let seqSeed = 0
const nextId = (prefix: string) => `${prefix}${(seqSeed += 1)}`

/** 前端自己生成 threadId：首条消息就要带上，不能等服务端分配 */
export function newThreadId(): string {
  const ts = Date.now().toString(36)
  const rand = Math.random().toString(36).slice(2, 10)
  return `t${ts}${rand}`.slice(0, 32)
}

/** 每条会话的流式缓冲，跟着会话活，不跟着组件活 */
const streamBuffers = new Map<string, string>()

/** 每条会话的中断句柄。多会话可以同时各跑一条流。 */
const aborts = new Map<string, AbortController>()

interface AgentRunStore {
  /** 当前正在看的会话 */
  threadId: string
  input: string
  /** 所有会话的运行态，键是 threadId */
  runs: Record<string, ThreadRuntime>

  setInput: (value: string) => void
  fillInput: (value: string) => void

  send: (override?: string) => Promise<void>
  sendText: (text: string) => void
  stop: () => void
  decide: (approved: boolean) => Promise<void>
  /** 回答意图澄清（弹窗里点选项或自己写一句） */
  answerClarify: (text: string) => Promise<void>
  newThread: () => void
  switchThread: (threadId: string) => Promise<void>
  /** 删除会话（连带它的消息）。删当前会话时会自动新开一个 */
  deleteThread: (threadId: string) => Promise<void>
}

/**
 * Agent 运行状态 —— **全局 store，按会话分片**。
 *
 * 两个必须做对的点：
 *   1. 状态不能放组件里：路由切换会卸载，过程就没了
 *   2. 状态要按会话分开：会话 1 在跑，切到会话 2 再切回来，过程必须还在，
 *      而且**不能因为切走就把会话 1 的运行打断**
 */
export const useAgentRunStore = create<AgentRunStore>((set, get) => {
  /** 更新某个会话的运行态。所有写入都走它，避免各处手拼对象。 */
  const patch = (threadId: string, next: Partial<ThreadRuntime>) => {
    set((state) => {
      const current = state.runs[threadId] ?? EMPTY_RUNTIME
      return { runs: { ...state.runs, [threadId]: { ...current, ...next } } }
    })
  }

  return {
    threadId: newThreadId(),
    input: '',
    runs: {},

    setInput: (value) => set({ input: value }),
    fillInput: (value) => set({ input: value }),

    sendText: (text) => {
      void get().send(text)
    },

    // ----------------------------------------------------------------
    send: async (override) => {
      const state = get()
      const threadId = state.threadId
      const text = (override ?? state.input).trim()
      const current = state.runs[threadId] ?? EMPTY_RUNTIME
      if (!text || current.running) {
        return
      }

      const { apiKey, model, models } = useLlmStore.getState()
      if (!apiKey) {
        patch(threadId, { error: '请先在 AI 设置里配置 DeepSeek API Key' })
        return
      }

      // 只发**这把 Key 真的支持**的模型名。
      //
      // 踩过的坑：模型名存在 sessionStorage 里。如果 DeepSeek 换了模型
      // （或者当初存的是个已经不存在的名字），后端会把无效名字原样转发，
      // 每次调用都 400 —— 看起来就像"一直失败"。这里兜一下。
      const effectiveModel =
        models.length === 0 || models.includes(model) ? model : models[0]

      streamBuffers.set(threadId, '')
      patch(threadId, {
        messages: [...current.messages, { id: nextId('m'), role: 'user', content: text }],
        running: true,
        error: null,
        streaming: '',
        runId: '',
        stage: '启动中',
        idleS: 0,
        pendingApproval: null,
        pendingClarify: null,
        agentState: EMPTY_AGENT_STATE,
      })
      set({ input: '' })

      /** 思考过程中收集的卡片。等回答结束时整体挂到那条消息上。 */
      let pendingCards: AgentCardItem[] = []

      const handle = (raw: Record<string, unknown>) => {
        const event = raw as unknown as AgUiEvent
        const runtime = get().runs[threadId] ?? EMPTY_RUNTIME

        switch (event.type) {
          case 'RUN_STARTED':
            patch(threadId, { runId: event.runId })
            break

          case 'STATE_SNAPSHOT':
            patch(threadId, { agentState: event.snapshot })
            break

          case 'STATE_DELTA': {
            const nextState = applyPatch(runtime.agentState, event.delta)
            patch(threadId, {
              agentState: nextState,
              // STATE_DELTA 里带了 /approval 就顺带同步到独立字段，
              // 两条通道保持一致，谁先到都能弹面板
              pendingApproval: nextState.approval ?? null,
            })
            break
          }

          case 'TEXT_MESSAGE_START':
            streamBuffers.set(threadId, '')
            patch(threadId, { streaming: '' })
            break

          case 'TEXT_MESSAGE_CONTENT': {
            const buffer = (streamBuffers.get(threadId) ?? '') + event.delta
            streamBuffers.set(threadId, buffer)
            patch(threadId, { streaming: buffer })
            break
          }

          case 'TEXT_MESSAGE_END': {
            const content = (streamBuffers.get(threadId) ?? '').trim()
            streamBuffers.set(threadId, '')
            // 一轮里后端会发很多张卡（每次工具调用一张），全挂上去用户看不懂。
            // selectCards 负责去重、收敛、限量 —— 只留真正的结论。
            const cards = selectCards(pendingCards)
            pendingCards = []
            const latest = get().runs[threadId] ?? EMPTY_RUNTIME
            patch(threadId, {
              streaming: '',
              messages: content
                ? [...latest.messages, { id: nextId('m'), role: 'assistant', content, cards }]
                : latest.messages,
            })
            break
          }

          case 'RUN_ERROR':
            patch(threadId, { error: event.message })
            break

          case 'RUN_FINISHED':
            patch(threadId, { running: false, stage: '', idleS: 0 })
            break

          case 'CUSTOM':
            if (event.name === 'courseflow.card') {
              const value = event.value as AgentCardItem | undefined
              if (value) pendingCards = [...pendingCards, value]
            } else if (event.name === 'courseflow.approval_required') {
              // 审批的第二条通道：直接读 CUSTOM 帧。
              // 页面上"审批卡片不弹"就是因为只依赖了 STATE_DELTA 那一条。
              const value = event.value as PendingApproval | undefined
              if (value?.approval_id) {
                console.info('[agent] 收到审批请求', value)
                patch(threadId, {
                  pendingApproval: value,
                  agentState: { ...runtime.agentState, approval: value },
                })
              }
            } else if (event.name === 'courseflow.approval_result') {
              console.info('[agent] 审批结果', event.value)
              patch(threadId, {
                pendingApproval: null,
                agentState: { ...runtime.agentState, approval: null },
              })
            } else if (event.name === 'courseflow.clarify_required') {
              // 澄清：弹窗，不是气泡。运行还挂着等回答，所以 running 保持 true。
              const value = event.value as ClarifyRequest | undefined
              if (value?.clarification_id) {
                console.info('[agent] 需要澄清', value)
                patch(threadId, { pendingClarify: value })
              }
            } else if (event.name === 'courseflow.clarify_answer') {
              patch(threadId, { pendingClarify: null })
            } else if (event.name === 'courseflow.thread') {
              const value = event.value as { threadId?: string } | undefined
              // 服务端可能把非法 threadId 换成新的（跨账号保护）
              if (value?.threadId && value.threadId !== threadId) {
                set((s) => {
                  const migrated = s.runs[threadId] ?? EMPTY_RUNTIME
                  const merged = { ...s.runs }
                  delete merged[threadId]
                  merged[value.threadId as string] = migrated
                  return { runs: merged, threadId: value.threadId as string }
                })
              }
            } else if (event.name === 'courseflow.stage') {
              const value = event.value as { stage?: string } | undefined
              patch(threadId, { stage: value?.stage ?? '', idleS: 0 })
            } else if (event.name === 'courseflow.heartbeat') {
              const value = event.value as { stage?: string; idle_s?: number } | undefined
              patch(threadId, {
                stage: value?.stage ?? runtime.stage,
                idleS: value?.idle_s ?? 0,
              })
            }
            break

          default:
            break
        }
      }

      const controller = new AbortController()
      aborts.set(threadId, controller)

      try {
        await agentApi.chatStream(
          { message: text, model: effectiveModel, threadId },
          apiKey,
          handle,
          controller.signal
        )
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') {
          return
        }
        const detail = err instanceof Error ? err.message : 'Agent 调用失败'
        const latest = get().runs[threadId] ?? EMPTY_RUNTIME
        patch(threadId, {
          error: detail,
          messages: [
            ...latest.messages,
            { id: nextId('m'), role: 'assistant', content: `调用失败：${detail}` },
          ],
        })
      } finally {
        aborts.delete(threadId)
        patch(threadId, { running: false, stage: '', idleS: 0 })
      }
    },

    // ----------------------------------------------------------------
    stop: () => {
      const threadId = get().threadId
      aborts.get(threadId)?.abort()
      aborts.delete(threadId)
      streamBuffers.set(threadId, '')
      patch(threadId, { running: false, streaming: '' })
    },

    decide: async (approved) => {
      const threadId = get().threadId
      const runtime = get().runs[threadId] ?? EMPTY_RUNTIME
      const approval = runtime.pendingApproval ?? runtime.agentState.approval
      if (!approval) return
      await agentApi.decideApproval(approval.approval_id, approved)
    },

    /**
     * 回答澄清。
     *
     * **不往会话里塞消息**：澄清是弹窗交互，不是聊天内容。
     * 后端那个 run 还挂着，答完它自己继续，后续事件照常从原 SSE 流里过来。
     */
    answerClarify: async (text) => {
      const threadId = get().threadId
      const runtime = get().runs[threadId] ?? EMPTY_RUNTIME
      const clarify = runtime.pendingClarify
      if (!clarify) return

      await agentApi.answerClarification(clarify.clarification_id, text)
      patch(threadId, { pendingClarify: null })
    },

    newThread: () => {
      set({ threadId: newThreadId(), input: '' })
    },

    /**
     * 切换会话。
     *
     * **不打断正在跑的会话** —— 每条流有自己的 AbortController，
     * 切走之后它继续更新自己那一份 runtime。切回来就能看到最新的过程。
     * 这正是"会话 1 在跑，切到会话 2 再回来过程还在"的实现方式。
     */
    switchThread: async (threadId) => {
      if (!threadId || threadId === get().threadId) {
        return
      }
      set({ threadId })

      if (get().runs[threadId]) {
        return // 本地已有该会话的状态，直接用
      }

      // 本地没有：从库里补历史消息
      try {
        const history = await agentApi.messages(threadId)
        const existing = get().runs[threadId]
        // 防止竞态：读取期间该会话已经开始运行了
        if (existing && (existing.running || existing.messages.length > 0)) {
          return
        }
        patch(threadId, {
          messages: history.items.map((item) => ({
            id: `h${item.id}`,
            role: item.role,
            content: item.content,
            cards: parseCards(item.cards_json),
          })),
        })
      } catch (err) {
        patch(threadId, {
          error: err instanceof Error ? err.message : '加载历史对话失败',
        })
      }
    },

    /**
     * 删除一个会话。
     *
     * 三件事按顺序做，缺一个都会留下"看起来还在"的残留：
     *   1. 中断它的流（正在跑的话），否则删完还有帧往空 key 里写
     *   2. 调接口删库（后端会连带删掉消息）
     *   3. 从本地 runs 里移除；如果删的正是当前会话，直接开一个新的
     */
    deleteThread: async (threadId) => {
      if (!threadId) {
        return
      }

      aborts.get(threadId)?.abort()
      aborts.delete(threadId)
      streamBuffers.delete(threadId)

      await agentApi.deleteThread(threadId)

      set((state) => {
        const remaining = { ...state.runs }
        delete remaining[threadId]
        // 删的就是当前会话 → 换一个新的空会话，避免界面停在一个已删除的会话上
        const nextThreadId = state.threadId === threadId ? newThreadId() : state.threadId
        return { runs: remaining, threadId: nextThreadId }
      })
    },
  }
})

function parseCards(raw: string): AgentCardItem[] | undefined {
  if (!raw) return undefined
  try {
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as AgentCardItem[]) : undefined
  } catch {
    return undefined
  }
}

/**
 * 清空所有与账号绑定的 Agent 状态。
 *
 * **退出登录 / 换账号时必须调用。** 这个 store 是模块级的，
 * 不随组件卸载而消失 —— 不主动清的话，A 退出后 B 登录还能看到 A 的对话。
 */
export function resetAgentRun(): void {
  for (const controller of aborts.values()) {
    controller.abort()
  }
  aborts.clear()
  streamBuffers.clear()
  useAgentRunStore.setState({ threadId: newThreadId(), input: '', runs: {} })
}
