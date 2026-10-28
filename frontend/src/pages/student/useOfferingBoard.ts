import { useMemo, useState } from 'react'
import { App } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { courseApi } from '../../api/course'
import { enrollmentApi } from '../../api/enrollment'
import { toErrorMessage } from '../../request/errors'

/** 选课台每页条数 */
const PAGE_SIZE = 10

/**
 * 选课台的逻辑。
 *
 * 数据关系：学期 → 当前轮次（能不能选）→ 班次列表（有什么）→ 我的选课（哪些已选）。
 * 四份数据用 TanStack Query 分别缓存，选课/退课后统一失效重取。
 */
export function useOfferingBoard() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()

  const [keyword, setKeyword] = useState('')
  const [keywordInput, setKeywordInput] = useState('')
  const [termId, setTermId] = useState<number | null>(null)
  const [page, setPage] = useState(1)

  const termsQuery = useQuery({ queryKey: ['terms'], queryFn: courseApi.terms })
  const terms = termsQuery.data?.items ?? []

  /** 没手动切学期时，用后端的"当前学期" */
  const defaultTermId = useMemo(() => {
    const current = terms.find((t) => t.is_current)
    return current?.term_id ?? terms[0]?.term_id ?? 0
  }, [terms])

  const activeTermId = termId ?? defaultTermId

  const roundQuery = useQuery({
    queryKey: ['my-round'],
    queryFn: enrollmentApi.currentRound,
  })

  const mineQuery = useQuery({
    queryKey: ['my-enrollments'],
    queryFn: () => enrollmentApi.myEnrollments(),
  })

  const offeringsQuery = useQuery({
    queryKey: ['offerings', activeTermId, keyword, page],
    queryFn: () =>
      courseApi.offerings({
        term_id: activeTermId || undefined,
        keyword: keyword || undefined,
        page,
        page_size: PAGE_SIZE,
      }),
    // 学期还没取到时不发请求：否则会先打一次不带 term_id 的全量查询
    enabled: termsQuery.isFetched,
  })

  /** 已选班次集合：表格里据此把按钮切成"退课" */
  const enrolledIds = useMemo(() => {
    const items = mineQuery.data?.items ?? []
    return new Set(items.map((item) => item.offering_id))
  }, [mineQuery.data])

  /** 选课 / 退课后，列表、余量、课表、学分全部要重取 */
  const invalidateAll = () => {
    void queryClient.invalidateQueries({ queryKey: ['offerings'] })
    void queryClient.invalidateQueries({ queryKey: ['my-enrollments'] })
    void queryClient.invalidateQueries({ queryKey: ['my-round'] })
    void queryClient.invalidateQueries({ queryKey: ['my-schedule'] })
  }

  const enrollMutation = useMutation({
    mutationFn: (offeringId: number) => enrollmentApi.enroll(offeringId),
    onSuccess: (result) => {
      message.success(`已选上「${result.course_name}」，剩余 ${result.remaining} 个名额`)
      invalidateAll()
    },
    onError: (error) => {
      // 后端已经给出了人话（名额已满 / 已选过 / 超出学分上限），直接展示
      message.error(toErrorMessage(error, '选课失败'))
    },
  })

  const dropMutation = useMutation({
    mutationFn: (offeringId: number) => enrollmentApi.drop(offeringId),
    onSuccess: () => {
      message.success('已退课')
      invalidateAll()
    },
    onError: (error) => {
      message.error(toErrorMessage(error, '退课失败'))
    },
  })

  const onSearch = (value: string) => {
    setKeyword(value.trim())
    setKeywordInput(value)
    setPage(1)
  }

  const onTermChange = (value: number) => {
    setTermId(value)
    setPage(1)
  }

  return {
    terms,
    activeTermId,
    onTermChange,

    keywordInput,
    setKeywordInput,
    onSearch,

    page,
    setPage,
    pageSize: PAGE_SIZE,

    round: roundQuery.data,
    roundLoading: roundQuery.isLoading,

    offerings: offeringsQuery.data?.items ?? [],
    total: offeringsQuery.data?.total ?? 0,
    offeringsLoading: offeringsQuery.isLoading,

    enrolledIds,

    onEnroll: (offeringId: number) => enrollMutation.mutate(offeringId),
    onDrop: (offeringId: number) => dropMutation.mutate(offeringId),
    enrollingId: enrollMutation.isPending ? (enrollMutation.variables ?? 0) : 0,
    droppingId: dropMutation.isPending ? (dropMutation.variables ?? 0) : 0,
  }
}
