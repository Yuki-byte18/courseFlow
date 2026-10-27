/**
 * 统一的 API 错误类型。
 *
 * 后端所有失败响应都是这个形状：
 *
 *   {"error": {"code": "invalid_credential", "message": "用户名或密码错误", "request_id": "..."}}
 *
 * 前端据此做分支：code 用于逻辑判断，message 用于展示，request_id 用于排查。
 */
export class ApiError extends Error {
  /** 机器可读的错误码，例如 invalid_credential / token_expired */
  readonly code: string
  /** HTTP 状态码；网络层失败时为 0 */
  readonly status: number
  /** 与响应头 X-Request-ID 一致，报障时把它给后端 */
  readonly requestId?: string

  constructor(code: string, message: string, status: number, requestId?: string) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.requestId = requestId
  }
}

/** 类型守卫：判断一个 unknown 是不是我们的 ApiError。 */
export function isApiError(err: unknown): err is ApiError {
  return err instanceof ApiError
}

/**
 * 把任意异常转成可以展示给用户的一句话。
 *
 * 规则：ApiError 用后端给的 message；其它情况给兜底文案。
 * 绝不把 JS 的原始异常信息（比如 "Cannot read property of undefined"）暴露给用户。
 */
export function toErrorMessage(err: unknown, fallback = '操作失败，请稍后重试'): string {
  if (isApiError(err)) {
    return err.message || fallback
  }
  return fallback
}
