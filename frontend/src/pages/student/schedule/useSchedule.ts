import { useMemo } from 'react'
import { App } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { enrollmentApi } from '../../../api/enrollment'
import type { MyEnrollmentVO, ScheduleItemVO } from '../../../api/types'
import { toErrorMessage } from '../../../request/errors'

/** 一天的节次数 */
export const PERIODS = Array.from({ length: 12 }, (_, index) => index + 1)

/** 一到周日 */
export const DAYS = [1, 2, 3, 4, 5, 6, 7]

/**
 * 我的课表的逻辑。
 *
 * 后端的 schedule 返回的是「按星期几分组的扁平时间段」，
 * 这里再摊平成 `星期-节次 → 课程` 的查表，渲染时 O(1) 命中。
 */
export function useSchedule() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()

  const scheduleQuery = useQuery({
    queryKey: ['my-schedule'],
    queryFn: () => enrollmentApi.schedule(),
  })

  const mineQuery = useQuery({
    queryKey: ['my-enrollments'],
    queryFn: () => enrollmentApi.myEnrollments(),
  })

  const schedule = scheduleQuery.data

  /** `weekday-period` → 该节要显示的课程 */
  const cellMap = useMemo(() => {
    const map = new Map<string, ScheduleItemVO>()
    const days = schedule?.days ?? {}
    for (const key of Object.keys(days)) {
      for (const item of days[key]) {
        for (let period = item.start_period; period <= item.end_period; period += 1) {
          map.set(`${item.weekday}-${period}`, item)
        }
      }
    }
    return map
  }, [schedule])

  const dropMutation = useMutation({
    mutationFn: (offeringId: number) => enrollmentApi.drop(offeringId),
    onSuccess: () => {
      message.success('已退课')
      void queryClient.invalidateQueries({ queryKey: ['my-schedule'] })
      void queryClient.invalidateQueries({ queryKey: ['my-enrollments'] })
      void queryClient.invalidateQueries({ queryKey: ['my-round'] })
      void queryClient.invalidateQueries({ queryKey: ['offerings'] })
    },
    onError: (error) => {
      message.error(toErrorMessage(error, '退课失败'))
    },
  })

  return {
    schedule,
    loading: scheduleQuery.isLoading,
    cellMap,
    enrollments: (mineQuery.data?.items ?? []) as MyEnrollmentVO[],
    onDrop: (offeringId: number) => dropMutation.mutate(offeringId),
    droppingId: dropMutation.isPending ? (dropMutation.variables ?? 0) : 0,
  }
}
