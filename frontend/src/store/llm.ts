import { create } from 'zustand'

import {
  getLlmConfig,
  removeLlmConfig,
  saveLlmConfig,
  saveLlmModel,
} from '../utils/storage'

interface LlmState {
  /** 用户自备的 DeepSeek API Key，只存在于浏览器 */
  apiKey: string | null
  /** 当前选中的模型 */
  model: string
  /**
   * 这把 Key 实际可用的模型列表。
   *
   * 由后端在校验 Key 时向 DeepSeek 的 /models 取回，
   * 前端只负责展示和选择 —— **不硬编码任何模型名**，
   * 这样 DeepSeek 换版本时不需要改前端代码。
   */
  models: string[]
  /** 是否已配置（apiKey 非空） */
  configured: boolean
  save: (apiKey: string, model: string, models: string[]) => void
  setModel: (model: string) => void
  clear: () => void
}

/** 从 sessionStorage 恢复初始状态 */
function initialState(): Pick<LlmState, 'apiKey' | 'model' | 'models' | 'configured'> {
  const { apiKey, model, models } = getLlmConfig()
  return {
    apiKey,
    model: model ?? '',
    models,
    configured: Boolean(apiKey),
  }
}

/**
 * Agent 配置状态。
 *
 * 这是**全局状态**而不是页面局部状态：Key 在 Agent 页面里配，
 * 但顶栏的「AI 设置」和后续所有 Agent 请求都要读它。
 *
 * 注意：这里不保存「校验过」这个标记。Key 能否用只有服务端知道，
 * 浏览器只负责保存用户填的值 —— 避免出现"本地说可用但实际已过期"的假象。
 */
export const useLlmStore = create<LlmState>((set) => ({
  ...initialState(),

  save: (apiKey: string, model: string, models: string[]) => {
    saveLlmConfig(apiKey, model, models)
    set({ apiKey, model, models, configured: true })
  },

  setModel: (model: string) => {
    saveLlmModel(model)
    set({ model })
  },

  clear: () => {
    removeLlmConfig()
    set({ apiKey: null, model: '', models: [], configured: false })
  },
}))
