import type { AgentCardItem } from '../api/agent'

/**
 * 卡片优先级：数字越小越靠前。
 *
 * 划分依据是"这张卡是不是结论"：
 *   - 写操作结果、冲突、学分核算 —— 用户真正要的答案
 *   - 通知、提醒 —— 也是结论
 *   - 课程列表、课表、轮次 —— 大多只是查询过程中的中间产物
 */
const PRIORITY: Record<string, number> = {
  enrollment_batch: 0,
  enrollment_result: 0,
  conflict_report: 1,
  credit_summary: 2,
  notice: 3,
  reminder_list: 3,
  offering_detail: 5,
  offering_list: 6,
  schedule: 7,
  round: 8,
  term: 9,
}

/** 这些卡是"过程性"的：有了正式的结论卡就没必要再摆出来 */
const EXPLORATORY = new Set(['offering_list', 'offering_detail', 'schedule', 'round', 'term'])

/** 结论卡：写操作结果 */
const CONCLUSION = new Set(['enrollment_batch', 'enrollment_result'])

/** 一次回答最多摆几张卡。超过这个数，用户就开始看不懂了。 */
const MAX_CARDS = 3

/**
 * 结论卡之外，最多再补几张"辅助卡"。
 *
 * 设成 1 是为了满足"我要什么就给什么"：问"我选了哪些课"，
 * 就应该只有一张已选课程清单，而不是清单 + 课表 + 轮次三张。
 */
const MAX_OTHERS = 1

/** 结论卡最多保留几张（选很多门课时不至于铺满整屏） */
const MAX_CONCLUSIONS = 5

/**
 * 从一次运行产生的所有卡片里，挑出**该给用户看的那几张**。
 *
 * 为什么需要它：后端是"每次工具调用发一张卡"，
 * 而一次选课会查好几次班次 —— 用户最后看到 6~10 个长得差不多的框，
 * 根本分不清哪个是结论。
 *
 * 规则（从粗到细）：
 *   1. 完全相同的卡片只留一张
 *   2. 有写操作结论时，丢掉所有过程性的课程列表/详情
 *   3. 两张课程列表如果一张是另一张的子集，只留大的
 *   4. 按优先级排序，最多留 3 张
 */
export function selectCards(cards: AgentCardItem[]): AgentCardItem[] {
  if (cards.length === 0) {
    return []
  }

  // 1. 去重：卡片类型 + 规范化后的内容
  const seen = new Set<string>()
  const unique: AgentCardItem[] = []
  for (const item of cards) {
    const key = `${item.card}|${stableStringify(item.payload)}`
    if (seen.has(key)) {
      continue
    }
    seen.add(key)
    unique.push(item)
  }

  // 2. 去掉"什么都没说"的卡。
  //    典型例子：冲突报告里 conflicts 是空数组 —— 摆一个框告诉用户"没有冲突"，
  //    不如让回答里的文字直接说。空框只会增加视觉噪音。
  const informative = unique.filter((item) => !isEmptyCard(item))

  // 3. 有结论卡时，过程性的课程列表没有存在价值
  const hasConclusion = informative.some((item) => CONCLUSION.has(item.card))
  const relevant = hasConclusion
    ? informative.filter((item) => !EXPLORATORY.has(item.card))
    : informative

  // 4. 课程列表做子集收敛：查 CS109 得到 1 条、查 CS110 得到 2 条，
  //    两张都留用户就得自己对照 —— 留大的那张就够了
  const lists = relevant.filter((item) => item.card === 'offering_list')
  const others = relevant.filter((item) => item.card !== 'offering_list')
  const keptLists = lists.filter((candidate) => {
    const ids = offeringIds(candidate)
    if (ids.size === 0) {
      return true
    }
    // 存在另一张卡的 id 集合**真包含**它 → 这张可以丢
    return !lists.some((other) => {
      if (other === candidate) {
        return false
      }
      const otherIds = offeringIds(other)
      if (otherIds.size <= ids.size) {
        return false
      }
      return [...ids].every((id) => otherIds.has(id))
    })
  })

  // 4. 排序 + 限量
  //
  // 结论卡（选课结果）**一张都不能少** —— 选两门课就有两条结果，
  // 用户要看到两门都成功。其余卡最多再补 MAX_OTHERS 张：
  // 于是"我选了哪些课"只会给一张清单，"帮我选两门"会给两条结果 + 一张补充。
  const sorted = [...others, ...keptLists].sort(
    (left, right) => (PRIORITY[left.card] ?? 9) - (PRIORITY[right.card] ?? 9)
  )
  const conclusions = sorted.filter((item) => CONCLUSION.has(item.card)).slice(0, MAX_CONCLUSIONS)
  const rest = sorted.filter((item) => !CONCLUSION.has(item.card)).slice(0, MAX_OTHERS)
  return [...conclusions, ...rest].slice(0, Math.max(MAX_CARDS, conclusions.length))
}

/** 这张卡是不是"空的"（有结构但没有任何信息量） */
function isEmptyCard(card: AgentCardItem): boolean {
  const payload = card.payload as Record<string, unknown> | undefined
  if (!payload) {
    return true
  }
  if (card.card === 'conflict_report') {
    const conflicts = payload.conflicts
    return Array.isArray(conflicts) && conflicts.length === 0
  }
  return false
}

/** 从课程列表卡里取出 offering_id 集合 */function offeringIds(card: AgentCardItem): Set<number> {
  const payload = card.payload as { offerings?: unknown } | undefined
  const rows = Array.isArray(payload?.offerings) ? payload?.offerings : []
  const ids = new Set<number>()
  for (const row of rows as Array<Record<string, unknown>>) {
    const id = Number(row?.offering_id)
    if (Number.isFinite(id)) {
      ids.add(id)
    }
  }
  return ids
}

/** 键排序后的 JSON：同样的内容一定得到同样的字符串，用于去重 */
function stableStringify(value: unknown): string {
  try {
    return JSON.stringify(value, (_key, val) => {
      if (val && typeof val === 'object' && !Array.isArray(val)) {
        const sorted: Record<string, unknown> = {}
        for (const key of Object.keys(val as Record<string, unknown>).sort()) {
          sorted[key] = (val as Record<string, unknown>)[key]
        }
        return sorted
      }
      return val
    })
  } catch {
    return String(value)
  }
}
