import { useMemo } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { agentApi } from '../../api/agent'
import { EMPTY_RUNTIME, newThreadId, useAgentRunStore } from '../../store/agentRun'
import { useLlmStore } from '../../store/llm'

export const AGENT_SUGGESTIONS = [
  '还有哪些三学分的课？',
  '周三下午有余量、又不和我课表冲突的课有哪些？',
  '我这学期选了多少学分，离上限还差多少？',
  '帮我看看哪些班的余量最紧张。',
]

export { newThreadId }

/**
 * Agent 对话页的数据入口。
 *
 * 真正的状态在 `store/agentRun.ts` 里，**按会话分片存放**。
 * 这个 hook 只负责：取当前会话那一片、把 agents 字典转成数组。
 */
export function useAgentChat() {
  const queryClient = useQueryClient()
  const configured = useLlmStore((state) => state.configured)

  const threadId = useAgentRunStore((state) => state.threadId)
  const runtime = useAgentRunStore((state) => state.runs[state.threadId]) ?? EMPTY_RUNTIME

  const input = useAgentRunStore((state) => state.input)
  const setInput = useAgentRunStore((state) => state.setInput)
  const fillInput = useAgentRunStore((state) => state.fillInput)

  const send = useAgentRunStore((state) => state.send)
  const sendText = useAgentRunStore((state) => state.sendText)
  const stop = useAgentRunStore((state) => state.stop)
  const decideRaw = useAgentRunStore((state) => state.decide)
  const newThread = useAgentRunStore((state) => state.newThread)
  const switchThread = useAgentRunStore((state) => state.switchThread)
  const removeThreadRaw = useAgentRunStore((state) => state.deleteThread)
  const answerClarifyRaw = useAgentRunStore((state) => state.answerClarify)

  const agents = useMemo(
    () => Object.values(runtime.agentState.agents),
    [runtime.agentState.agents]
  )

  const decide = async (approved: boolean) => {
    await decideRaw(approved)
    void queryClient.invalidateQueries({ queryKey: ['agent-threads'] })
  }

  /**
   * 回答澄清。
   *
   * 后端那个运行一直挂着等这个回答，提交后**同一个 run 继续**，
   * 后续事件仍旧从原来那条 SSE 流里过来 —— 所以这里不做任何重新发起。
   */
  const answerClarify = async (text: string) => {
    await answerClarifyRaw(text)
  }

  /** 删除会话：删完刷新左侧列表 */
  const removeThread = async (threadId: string) => {
    await removeThreadRaw(threadId)
    void queryClient.invalidateQueries({ queryKey: ['agent-threads'] })
  }

  return {
    configured,
    threadId,
    input,
    setInput,
    fillInput,
    messages: runtime.messages,
    streaming: runtime.streaming,
    running: runtime.running,
    runId: runtime.runId,
    error: runtime.error,
    stage: runtime.stage,
    idleS: runtime.idleS,
    intent: runtime.agentState.intent,
    nodes: runtime.agentState.nodes,
    agents,
    // 两条通道取其一：独立字段优先，再退回 agentState
    approval: runtime.pendingApproval ?? runtime.agentState.approval,
    /** 意图澄清弹窗的内容；null 表示没有待澄清 */
    clarify: runtime.pendingClarify,
    send,
    sendText,
    stop,
    decide,
    answerClarify,
    newThread,
    switchThread,
    removeThread,
  }
}

/** 会话列表：走 react-query，带缓存与轮询 */
export function useThreadList() {
  const { data, isLoading } = useQuery({
    queryKey: ['agent-threads'],
    queryFn: () => agentApi.threads(),
    refetchInterval: 15000,
    retry: false,
  })

  return { threads: data?.items ?? [], loading: isLoading }
}
