import { useState } from 'react'
import { Alert, App, Button, Input, Select, Space } from 'antd'

import { agentApi } from '../../api/agent'
import { useLlmStore } from '../../store/llm'
import { toErrorMessage } from '../../request/errors'
import './index.css'

/** DeepSeek 控制台：创建 / 查看 API Key */
export const DEEPSEEK_KEYS_URL = 'https://platform.deepseek.com/api_keys'
/** DeepSeek 充值页 */
export const DEEPSEEK_TOPUP_URL = 'https://platform.deepseek.com/top_up'

interface LlmKeyPanelProps {
  /** 校验通过后的回调 */
  onConfigured?: () => void
  /** 紧凑模式：放在弹窗里时不重复大段引导 */
  compact?: boolean
}

/**
 * 配置用户自备的 DeepSeek API Key。
 *
 * 设计要点：
 *   1. **不托管密钥** —— 只写 sessionStorage，永不上传我们的后端存储
 *   2. **先测再存** —— 点「测试并保存」会真实请求 DeepSeek 一次
 *   3. **模型不硬编码** —— 校验时顺带取回这把 Key 的真实可用模型列表，
 *      用户从列表里选。DeepSeek 换版本时前端不用改代码
 *   4. **官方入口直链** —— 一键跳到 DeepSeek 控制台创建 Key
 */
export function LlmKeyPanel({ onConfigured, compact = false }: LlmKeyPanelProps) {
  const { message } = App.useApp()
  const storedKey = useLlmStore((state) => state.apiKey)
  const model = useLlmStore((state) => state.model)
  const models = useLlmStore((state) => state.models)
  const save = useLlmStore((state) => state.save)
  const setModel = useLlmStore((state) => state.setModel)
  const clear = useLlmStore((state) => state.clear)

  const [input, setInput] = useState(storedKey ?? '')
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState(false)

  const verifyAndSave = async () => {
    const key = input.trim()
    setError(null)

    if (!key) {
      setError('请先粘贴你的 DeepSeek API Key')
      return
    }
    if (!key.startsWith('sk-')) {
      setError('Key 应该以 sk- 开头，请确认复制完整')
      return
    }

    setPending(true)
    try {
      const result = await agentApi.verifyKey(key)
      save(key, result.default, result.models)

      if (result.models.length > 1) {
        message.success(`Key 校验通过 · 可用模型 ${result.models.length} 个，默认 ${result.default}`)
      } else {
        message.success(`Key 校验通过 · ${result.default}`)
      }
      onConfigured?.()
    } catch (err) {
      // 后端把「格式错 / Key 无效 / 余额不足 / 没有可用模型 / 上游不通」
      // 映射成了不同状态码，这里直接展示后端给的文案，不要用统一的一句话盖掉原因。
      setError(toErrorMessage(err, 'Key 校验失败，请稍后再试'))
    } finally {
      setPending(false)
    }
  }

  const handleClear = () => {
    clear()
    setInput('')
    setError(null)
    message.success('已清除本地保存的 Key')
  }

  return (
    <div className={`llm-key${compact ? ' llm-key--compact' : ''}`}>
      {!compact && (
        <>
          <h2 className="llm-key__title">用你自己的 DeepSeek Key 开启 Agent</h2>
          <p className="llm-key__lead">
            courseFlow <strong>不托管任何 API Key</strong>。你需要自己准备一把
            DeepSeek 的 Key，填在这里 —— 它只保存在你的浏览器里，不会上传到我们的服务器。
          </p>
        </>
      )}

      {/* ---------- 获取 Key 的入口 ---------- */}
      <div className="llm-key__steps">
        <div className="llm-key__step">
          <span className="llm-key__step-no">1</span>
          <span>
            打开 DeepSeek 控制台，注册 / 登录后进入 <code>API keys</code> 页面
          </span>
        </div>

        <Space wrap className="llm-key__actions">
          <Button type="primary" href={DEEPSEEK_KEYS_URL} target="_blank" rel="noreferrer">
            去 DeepSeek 创建 API Key ↗
          </Button>
          <Button href={DEEPSEEK_TOPUP_URL} target="_blank" rel="noreferrer" type="text">
            余额不足时去充值
          </Button>
        </Space>

        <div className="llm-key__step">
          <span className="llm-key__step-no">2</span>
          <span>
            点「创建 API key」，复制以 <code>sk-</code> 开头的那串，粘贴到下面
          </span>
        </div>
      </div>

      {/* ---------- 填写 ---------- */}
      <div className="llm-key__form">
        <Input.Password
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onPressEnter={verifyAndSave}
          placeholder="sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
          autoComplete="off"
          spellCheck={false}
          size="large"
          className="llm-key__input"
        />

        <div className="llm-key__row">
          <span className="llm-key__label">模型</span>

          {models.length > 0 ? (
            <>
              <Select
                value={model}
                onChange={setModel}
                style={{ minWidth: 240 }}
                options={models.map((id) => ({ value: id, label: id }))}
              />
              <span className="llm-key__fixed-note">共 {models.length} 个可用</span>
            </>
          ) : (
            <span className="llm-key__fixed-note">校验通过后可选择</span>
          )}

          <Button type="primary" loading={pending} onClick={verifyAndSave}>
            测试并保存
          </Button>

          {storedKey && (
            <Button type="text" danger onClick={handleClear}>
              清除
            </Button>
          )}
        </div>
      </div>

      {error && (
        <Alert
          type="error"
          showIcon
          message={error}
          description="常见原因：Key 复制不完整、Key 已被删除、账户没有余额、或该 Key 还没开通任何对话模型。"
          className="llm-key__error"
        />
      )}

      <div className="llm-key__note">
        <strong>为什么需要你自己提供 Key</strong>
        <span>
          大模型的调用是有成本的。让每个使用者用自己的 Key，既能控制费用，也意味着
          courseFlow 的服务器上不存任何人的密钥 —— 即使数据库泄露，也不会连带泄露
          API Key。
        </span>
      </div>

      <div className="llm-key__note">
        <strong>Key 存在哪里</strong>
        <span>
          只存在于当前标签页的 sessionStorage：<strong>关闭标签页即失效</strong>，
          换浏览器、换标签页都要重新填。我们刻意不做持久化。
        </span>
      </div>

      <div className="llm-key__note">
        <strong>模型列表是怎么来的</strong>
        <span>
          校验时向 DeepSeek 请求一次 <code>GET /models</code>（不消耗 token），
          把你这把 Key 真实可用的模型取回来给你选 —— 所以不会出现选了就跑不通的模型。
        </span>
      </div>
    </div>
  )
}
