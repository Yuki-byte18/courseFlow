/**
 * 本地存储的唯一入口。
 *
 * 为什么单独抽出来：
 * 1. request 层和 store 层都要读 token，直接互相 import 会形成循环依赖；
 * 2. 隐私模式 / 禁用存储时 localStorage 会抛异常，这里统一兜住。
 */

const TOKEN_KEY = 'courseflow.token'

/** 读取 token，读不到返回 null。 */
export function getToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

/** 写入 token。写入失败（如隐私模式）不影响内存态，只是刷新后会掉登录。 */
export function saveToken(token: string): void {
  try {
    window.localStorage.setItem(TOKEN_KEY, token)
  } catch {
    /* 忽略：无法持久化时退化为"仅本次会话有效" */
  }
}

/** 清除 token。 */
export function removeToken(): void {
  try {
    window.localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* 忽略 */
  }
}

// ---------------------------------------------------------------------------
// 用户自备的 DeepSeek API Key
//
// 刻意用 **sessionStorage** 而不是 localStorage：
//   - 关闭标签页即失效，密钥暴露的时间窗最短
//   - 不同标签页互不共享，减少误用
//
// 更关键的是：这个 Key **只存在于浏览器**，永不发往我们的后端存储。
// 服务端只在校验和调用时通过请求头临时接收，用完即弃。
// ---------------------------------------------------------------------------

const LLM_KEY_KEY = 'courseflow.llm.apikey'
const LLM_MODEL_KEY = 'courseflow.llm.model'
const LLM_MODELS_KEY = 'courseflow.llm.models'

/** 读取本地保存的 LLM 配置。没配过时返回空值。 */
export function getLlmConfig(): {
  apiKey: string | null
  model: string | null
  models: string[]
} {
  try {
    const raw = window.sessionStorage.getItem(LLM_MODELS_KEY)
    let models: string[] = []
    if (raw) {
      const parsed: unknown = JSON.parse(raw)
      if (Array.isArray(parsed)) {
        models = parsed.filter((item): item is string => typeof item === 'string')
      }
    }
    return {
      apiKey: window.sessionStorage.getItem(LLM_KEY_KEY),
      model: window.sessionStorage.getItem(LLM_MODEL_KEY),
      models,
    }
  } catch {
    return { apiKey: null, model: null, models: [] }
  }
}

/**
 * 保存 LLM 配置。
 *
 * models 一起存：刷新页面后模型下拉框要能立刻用，
 * 否则用户得重新校验一次 Key 才知道有哪些模型可选。
 */
export function saveLlmConfig(apiKey: string, model: string, models: string[]): void {
  try {
    window.sessionStorage.setItem(LLM_KEY_KEY, apiKey)
    window.sessionStorage.setItem(LLM_MODEL_KEY, model)
    window.sessionStorage.setItem(LLM_MODELS_KEY, JSON.stringify(models))
  } catch {
    /* 忽略：无法持久化时退化为"仅本次内存有效" */
  }
}

/** 只更新模型选择，不动 Key 与模型列表。 */
export function saveLlmModel(model: string): void {
  try {
    window.sessionStorage.setItem(LLM_MODEL_KEY, model)
  } catch {
    /* 忽略 */
  }
}

/** 清除 LLM 配置。 */
export function removeLlmConfig(): void {
  try {
    window.sessionStorage.removeItem(LLM_KEY_KEY)
    window.sessionStorage.removeItem(LLM_MODEL_KEY)
    window.sessionStorage.removeItem(LLM_MODELS_KEY)
  } catch {
    /* 忽略 */
  }
}
