import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Input, Modal, Popconfirm, Tooltip } from 'antd'
import { Link } from 'react-router-dom'

import { AgentCard, type CardAction } from '../../components/AgentCards'
import { LlmKeyPanel } from '../../components/LlmKeyPanel'
import { Markdown } from '../../components/Markdown'
import { useLlmStore } from '../../store/llm'
import { ActivityLine, ApprovalPanel, ClarifyModal, IntentChip, ProcessStream } from './components'
import { AGENT_SUGGESTIONS, useAgentChat, useThreadList } from './useAgentChat'
import './index.css'

/**
 * Agent 对话页。
 *
 * 布局（固定高度，内容区独立滚动）：
 *
 *   ┌──────────────────────────────────────────────┐
 *   │ 顶栏：标题 · 模型 · Trace · 设置              │ 固定
 *   ├────────┬─────────────────────────────────────┤
 *   │ 会话   │  消息流（Markdown 流式）             │
 *   │ 列表   │  过程（默认折叠）                    │ 滚动
 *   │        │  业务卡片                            │
 *   ├────────┴─────────────────────────────────────┤
 *   │ 活动条（单行，原地更新）                       │ 固定
 *   │ 审批面板（需要时出现）                         │
 *   │ 输入框                                        │ 固定
 *   └──────────────────────────────────────────────┘
 *
 * 过程只有一行 —— 这是"不要一堆一堆"的关键：子 agent 再多，
 * 布局也不会因为卡片增多而抖动。
 */
export function AgentChatPage() {
  const model = useLlmStore((state) => state.model)

  const {
    configured,
    threadId,
    messages,
    input,
    setInput,
    send,
    sendText,
    stop,
    newThread,
    switchThread,
    removeThread,
    fillInput,
    running,
    runId,
    intent,
    nodes,
    agents,
    approval,
    decide,
    clarify,
    answerClarify,
    streaming,
    error,
    stage,
    idleS,
  } = useAgentChat()

  // 会话列表由 react-query 轮询维护，切页面回来也会自动刷新
  const { threads } = useThreadList()
  const [keyModalOpen, setKeyModalOpen] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  // 贴底滚动：内容变高、流式追加、切换会话时都要跟到底部
  useEffect(() => {
    const node = scrollRef.current
    if (node) {
      node.scrollTop = node.scrollHeight
    }
  }, [messages, nodes, streaming, approval])

  const handleCardAction = useCallback(
    (action: CardAction) => {
      if (action.action !== 'enroll') return
      const { offering } = action
      void sendText(
        `帮我选这门课：${offering.course_name}（${offering.course_code} ${offering.section_no} 班，offering_id=${offering.offering_id}）`
      )
    },
    [sendText]
  )

  // 未配置 Key：仍然给一个完整的页面骨架，只是内容换成引导
  if (!configured) {
    return (
      <div className="agent agent--setup">
        <div className="agent__top">
          <div className="agent__top-left">
            <h1 className="agent__title">Agent</h1>
            <span className="agent__subtitle">用自然语言查课、算冲突、选课退课</span>
          </div>
        </div>
        <div className="agent__setup-body">
          <LlmKeyPanel />
        </div>
      </div>
    )
  }

  const busy = running || Boolean(approval)

  return (
    <div className="agent">
      {/* ---------- 顶栏 ---------- */}
      <div className="agent__top">
        <div className="agent__top-left">
          <h1 className="agent__title">Agent</h1>
          <IntentChip intent={intent} />
        </div>

        <div className="agent__top-right">
          <span className="agent__model">
            <span className="agent__model-dot" />
            {model}
          </span>
          <Link to="/agent/trace" className="agent__link">
            Trace
          </Link>
          <Button size="small" type="text" onClick={() => setKeyModalOpen(true)}>
            设置
          </Button>
        </div>
      </div>

      {/* ---------- 主体 ---------- */}
      <div className="agent__body">
        <aside className="agent__sidebar">
          <button type="button" className="agent__new" onClick={newThread}>
            <span className="agent__new-plus">+</span>
            新对话
          </button>

          <div className="agent__threads">
            {threads.length === 0 && <div className="agent__threads-empty">暂无历史</div>}
            {threads.map((thread) => {
              const active = thread.thread_id === threadId
              return (
                <div
                  key={thread.thread_id}
                  className={`agent__thread-row${active ? ' agent__thread-row--active' : ''}`}
                >
                  <button
                    type="button"
                    className={`agent__thread${active ? ' agent__thread--active' : ''}`}
                    onClick={() => void switchThread(thread.thread_id)}
                    title={thread.title}
                  >
                    <span className="agent__thread-title">{thread.title || '空会话'}</span>
                    <span className="agent__thread-meta">{thread.message_count}</span>
                  </button>

                  {/* 删除：需要二次确认，避免误点把对话删掉 */}
                  <Popconfirm
                    title="删除这个会话？"
                    description="对话记录会一起删除，无法恢复。"
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => void removeThread(thread.thread_id)}
                  >
                    <button
                      type="button"
                      className="agent__thread-del"
                      title="删除会话"
                      aria-label="删除会话"
                      onClick={(event) => event.stopPropagation()}
                    >
                      ×
                    </button>
                  </Popconfirm>
                </div>
              )
            })}
          </div>
        </aside>

        <div className="agent__main">
          <div className="agent__scroll" ref={scrollRef}>
            <div className="agent__stream">
              {messages.length === 0 && !running && (
                <div className="agent__empty">
                  <div className="agent__empty-title">想问点什么？</div>
                  <div className="agent__empty-sub">
                    我能查课程与余量、算时间冲突、帮你选课退课。
                  </div>
                  <div className="agent__suggestions">
                    {AGENT_SUGGESTIONS.map((text) => (
                      <button
                        key={text}
                        type="button"
                        className="agent__suggestion"
                        onClick={() => fillInput(text)}
                      >
                        {text}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {messages.map((message) => (
                <div key={message.id} className={`msg msg--${message.role}`}>
                  {message.role === 'assistant' && <div className="msg__avatar">AI</div>}
                  <div className="msg__body">
                    {message.role === 'assistant' ? (
                      <Markdown content={message.content} />
                    ) : (
                      <div className="msg__text">{message.content}</div>
                    )}

                    {message.cards && message.cards.length > 0 && (
                      <div className="msg__cards">
                        {message.cards.map((item, index) => (
                          <AgentCard
                            key={index}
                            card={item.card}
                            payload={item.payload}
                            onAction={handleCardAction}
                          />
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}

              {/* 本次运行的过程：一行一行往下流，不套框 */}
              <ProcessStream nodes={nodes} agents={agents} running={running} />

              {/* 流式回答 */}
              {streaming && (
                <div className="msg msg--assistant">
                  <div className="msg__avatar">AI</div>
                  <div className="msg__body">
                    <Markdown content={streaming} streaming />
                  </div>
                </div>
              )}

              {error && <Alert type="error" showIcon message={error} className="agent__error" />}
            </div>
          </div>

          {/* ---------- 固定区 ---------- */}
          <div className="agent__dock">
            <ActivityLine
              agents={agents}
              nodes={nodes}
              running={running}
              streaming={Boolean(streaming)}
              approval={approval}
              error={error}
              stage={stage}
              idleS={idleS}
            />

            {approval && <ApprovalPanel approval={approval} onDecide={decide} />}

            <div className="composer">
              <Input.TextArea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                placeholder="问点什么…  Enter 发送 / Shift + Enter 换行"
                autoSize={{ minRows: 1, maxRows: 6 }}
                variant="borderless"
                disabled={busy}
                className="composer__input"
                onPressEnter={(event) => {
                  if (event.shiftKey) {
                    return
                  }
                  event.preventDefault()
                  void send()
                }}
              />

              <div className="composer__bar">
                <span className="composer__hint">
                  {runId ? (
                    <>
                      run <code>{runId}</code>
                    </>
                  ) : (
                    '写操作会先弹审批'
                  )}
                </span>

                {running ? (
                  <Tooltip title={approval ? '等待审批，可先中止' : '中止本次运行'}>
                    <Button size="small" danger onClick={stop}>
                      停止
                    </Button>
                  </Tooltip>
                ) : (
                  <Button
                    size="small"
                    type="primary"
                    onClick={() => void send()}
                    disabled={!input.trim()}
                  >
                    发送
                  </Button>
                )}
              </div>
            </div>
          </div>
        </div>
      </div>

      <Modal
        title="AI 设置"
        open={keyModalOpen}
        onCancel={() => setKeyModalOpen(false)}
        footer={null}
        width={620}
      >
        <LlmKeyPanel compact onConfigured={() => setKeyModalOpen(false)} />
      </Modal>

      {/* 意图澄清：弹窗问一句，答完**同一轮继续跑**，不用重新提问 */}
      {clarify && <ClarifyModal clarify={clarify} onAnswer={answerClarify} />}
    </div>
  )
}
