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
