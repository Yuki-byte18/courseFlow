import { useMemo, useState } from 'react'
import { App } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { courseApi } from '../../api/course'
import type { ImportResult, ImportRowPayload } from '../../api/types'
import { toErrorMessage } from '../../request/errors'

/** 我的班次每页条数 */
const PAGE_SIZE = 15

/** 示例数据：课程号对应 cmd/seeddemo 生成的 D001…D050 */
const SAMPLE_CSV = `# 课程号,班号,容量,地点,星期(1-7),起始节,结束节
D001,01,60,一教 301,1,1,2
D002,01,60,一教 405,3,5,6
D003,01,40,二教 210,2,3,4`

export interface ParseResult {
  rows: ImportRowPayload[]
  errors: string[]
}

/**
 * 解析粘贴进来的课表。
 *
 * 格式：每行 `课程号,班号,容量,地点,星期,起始节,结束节`。
 * 兼容中文逗号和 Tab；空行与 # 开头的注释行会被跳过。
 *
 * 一行出错不影响其它行：错误单独收集，最后统一展示行号。
 */
export function parseScheduleCsv(text: string): ParseResult {
  const rows: ImportRowPayload[] = []
  const errors: string[] = []

  text.split(/\r?\n/).forEach((rawLine, index) => {
    const line = rawLine.trim()
    if (!line || line.startsWith('#')) return

    const lineNo = index + 1
    const cells = line.split(/[,，\t]/).map((cell) => cell.trim())
    const [courseCode, sectionNo, capacityText, location, weekdayText, startText, endText] = cells

    if (!courseCode) {
      errors.push(`第 ${lineNo} 行：缺少课程号`)
      return
    }

    const capacity = Number(capacityText)
    if (!Number.isFinite(capacity) || capacity <= 0) {
      errors.push(`第 ${lineNo} 行：容量「${capacityText ?? ''}」不是正整数`)
      return
    }

    const weekday = weekdayText ? Number(weekdayText) : undefined
    if (weekday !== undefined && (!Number.isInteger(weekday) || weekday < 1 || weekday > 7)) {
      errors.push(`第 ${lineNo} 行：星期应为 1-7`)
      return
    }

    const startPeriod = startText ? Number(startText) : undefined
    const endPeriod = endText ? Number(endText) : undefined
    if (
      startPeriod !== undefined &&
      endPeriod !== undefined &&
      (!Number.isInteger(startPeriod) || !Number.isInteger(endPeriod) || endPeriod < startPeriod)
    ) {
      errors.push(`第 ${lineNo} 行：节次不合法（结束节不能早于起始节）`)
      return
    }

    rows.push({
      course_code: courseCode,
      section_no: sectionNo || undefined,
      capacity,
      location: location || undefined,
      weekday,
      start_period: startPeriod,
      end_period: endPeriod,
    })
  })

  return { rows, errors }
}

/**
 * 教师端 · 我的班次的逻辑。
 *
 * 三件事：看自己开的班、看某个班的名单、一键导入课表。
 */
export function useTeacherDesk() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()

  const [page, setPage] = useState(1)
  const [rosterId, setRosterId] = useState<number | null>(null)

  const [importOpen, setImportOpen] = useState(false)
  const [termId, setTermId] = useState<number | null>(null)
  const [csv, setCsv] = useState('')
  const [result, setResult] = useState<ImportResult | null>(null)

  const termsQuery = useQuery({ queryKey: ['terms'], queryFn: courseApi.terms })
  const terms = termsQuery.data?.items ?? []

  const defaultTermId = useMemo(() => {
    const current = terms.find((term) => term.is_current)
    return current?.term_id ?? terms[0]?.term_id ?? 0
  }, [terms])

  const activeTermId = termId ?? defaultTermId

  const offeringsQuery = useQuery({
    queryKey: ['my-offerings', activeTermId, page],
    queryFn: () =>
      courseApi.myOfferings({
        term_id: activeTermId || undefined,
        page,
        page_size: PAGE_SIZE,
      }),
    enabled: termsQuery.isFetched,
  })

  const rosterQuery = useQuery({
    queryKey: ['roster', rosterId],
    queryFn: () => courseApi.roster(rosterId as number),
    enabled: rosterId !== null,
    retry: false,
  })

  const parsed = useMemo(() => parseScheduleCsv(csv), [csv])

  const importMutation = useMutation({
    mutationFn: (payload: { term_id: number; rows: ImportRowPayload[] }) =>
      courseApi.importOfferings(payload),
  })

  const openImport = () => {
    setCsv('')
    setResult(null)
    setImportOpen(true)
  }

  const closeImport = () => setImportOpen(false)

  const fillSample = () => {
    setCsv(SAMPLE_CSV)
    setResult(null)
  }

  const submitImport = async () => {
    if (!activeTermId) {
      message.warning('还没有可用学期，请先让管理员创建学期')
      return
    }
    if (parsed.rows.length === 0) {
      message.warning('没有可导入的行')
      return
    }

    try {
      const res = await importMutation.mutateAsync({ term_id: activeTermId, rows: parsed.rows })
      setResult(res)
      if (res.failed > 0) {
        message.warning(`导入完成：新增 ${res.created}，跳过 ${res.skipped}，失败 ${res.failed}`)
      } else {
        message.success(`导入完成：新增 ${res.created}，跳过 ${res.skipped}`)
      }
      void queryClient.invalidateQueries({ queryKey: ['my-offerings'] })
      void queryClient.invalidateQueries({ queryKey: ['offerings'] })
    } catch (error) {
      message.error(toErrorMessage(error, '导入失败'))
    }
  }

  return {
    terms,
    activeTermId,
    onTermChange: (value: number) => {
      setTermId(value)
      setPage(1)
    },

    offerings: offeringsQuery.data?.items ?? [],
    total: offeringsQuery.data?.total ?? 0,
    loading: offeringsQuery.isLoading,
    page,
    setPage,
    pageSize: PAGE_SIZE,

    rosterId,
    roster: rosterQuery.data,
    rosterLoading: rosterQuery.isLoading,
    rosterError: rosterQuery.isError,
    openRoster: (id: number) => setRosterId(id),
    closeRoster: () => setRosterId(null),

    importOpen,
    openImport,
    closeImport,
    csv,
    setCsv,
    fillSample,
    parsed,
    submitImport,
    importing: importMutation.isPending,
    result,
  }
}
