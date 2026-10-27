import { getToken } from '../utils/storage'
import { ApiError } from './errors'

/**
 * API 基础路径。
 *
 * 开发期值是 `/api/v1`，由 Vite 的 server.proxy 转发到 http://127.0.0.1:8080，
 * 因此浏览器看到的是同源请求，后端不需要为开发环境开放 CORS。
 */
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

/** 401 时的全局回调。由 App 注册，用来清空登录态并跳转登录页。 */
let unauthorizedHandler: (() => void) | null = null

/** 注册 401 处理器。传 null 表示取消。 */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  /** 请求体，会被 JSON 序列化 */
  body?: unknown
  /** 查询参数，值为 undefined / null / '' 的会被跳过 */
  query?: Record<string, string | number | boolean | undefined | null>
  /** 是否携带 Authorization 头，默认 true；登录/注册要传 false */
  auth?: boolean
  /** 用于取消请求 */
  signal?: AbortSignal
}

/** 后端错误响应体 */
interface ErrorPayload {
  error?: {
    code?: string
    message?: string
    request_id?: string
  }
}

/**
 * 发起一次 API 请求。
 *
 * 职责边界：只负责拼 URL、加 header、序列化、反序列化、把失败转成 ApiError。
 * **不负责**跳转、弹提示、改全局状态——那些由调用方决定。
 * 唯一的例外是 401：它清掉本地 token 并触发全局回调，因为"凭据失效"是全局事件。
 */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, query, auth = true, signal } = options

  const url = buildUrl(path, query)

  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }
  if (auth) {
    const token = getToken()
    if (token) {
      headers.Authorization = `Bearer ${token}`
    }
  }

  let response: Response
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    })
  } catch (err) {
    // 被调用方主动取消，不该当成错误
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw err
    }
    throw new ApiError('network_error', '网络异常，请检查网络或后端服务是否启动', 0)
  }

  // 204 No Content（例如退出登录）没有响应体
  if (response.status === 204) {
    return undefined as T
  }

  const raw = await response.text()
  const payload = parseJson(raw)

  if (!response.ok) {
    const parsed = extractError(payload)
    if (response.status === 401) {
      unauthorizedHandler?.()
    }
    throw new ApiError(parsed.code, parsed.message, response.status, parsed.requestId)
  }

  return payload as T
}

/** 拼接 base + path + 查询串。 */
function buildUrl(
  path: string,
  query?: Record<string, string | number | boolean | undefined | null>,
): string {
  let url = `${API_BASE}${path}`
  if (!query) return url

  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    search.append(key, String(value))
  }
  const qs = search.toString()
  if (qs) url += `?${qs}`
  return url
}

/** 宽松解析 JSON：不是合法 JSON 就返回 undefined，不抛异常。 */
function parseJson(raw: string): unknown {
  if (!raw) return undefined
  try {
    return JSON.parse(raw)
  } catch {
    return undefined
  }
}

/** 从错误响应体里抽出 code / message / request_id，缺什么补默认值。 */
function extractError(payload: unknown): {
  code: string
  message: string
  requestId?: string
} {
  const body = payload as ErrorPayload | undefined
  const error = body?.error

  return {
    code: error?.code ?? 'unknown_error',
    message: error?.message ?? '请求失败，请稍后重试',
    requestId: error?.request_id,
  }
}
