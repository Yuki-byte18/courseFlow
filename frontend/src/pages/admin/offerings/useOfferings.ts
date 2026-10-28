import { useState } from 'react'
import { App, Form } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { courseApi } from '../../../api/course'
import { enrollmentApi } from '../../../api/enrollment'
import type { OfferingVO } from '../../../api/types'
import { downloadFile } from '../../../request/client'
import { toErrorMessage } from '../../../request/errors'

/** 班次列表每页条数 */
const PAGE_SIZE = 15

/** 表单里的一个时间段 */
export interface SlotFormValue {
  weekday: number
  start_period: number
  end_period: number
}

/** 开班 / 编辑班次表单 */
export interface OfferingFormValues {
  term_id: number
  course_id: number
  teacher_id: number
  section_no?: string
  capacity: number
  location?: string
  slots: SlotFormValue[]
}

/**
 * 管理端 · 班次管理的逻辑。
 *
 * 开班与编辑共用同一个弹窗（editing 区分）。
 * 时间段用 Form.List 支持多条 —— 一门课一周上两次是常态。
 */
export function useOfferings() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()

  const [page, setPage] = useState(1)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<OfferingVO | null>(null)
  const [form] = Form.useForm<OfferingFormValues>()

  const [rosterId, setRosterId] = useState<number | null>(null)

  const termsQuery = useQuery({ queryKey: ['terms'], queryFn: courseApi.terms })
  const coursesQuery = useQuery({ queryKey: ['courses', ''], queryFn: () => courseApi.courses() })

  const offeringsQuery = useQuery({
    queryKey: ['admin-offerings', page],
    queryFn: () => courseApi.offerings({ page, page_size: PAGE_SIZE }),
  })

  const rosterQuery = useQuery({
    queryKey: ['roster', rosterId],
    queryFn: () => courseApi.roster(rosterId as number),
    enabled: rosterId !== null,
    retry: false,
  })

  const createMutation = useMutation({ mutationFn: courseApi.createOffering })
  const patchMutation = useMutation({
    mutationFn: (args: { id: number; body: Parameters<typeof courseApi.patchOffering>[1] }) =>
      courseApi.patchOffering(args.id, args.body),
  })
  const forceDropMutation = useMutation({ mutationFn: enrollmentApi.forceDrop })
  const supplementMutation = useMutation({ mutationFn: enrollmentApi.supplement })

  const invalidateOfferings = () => {
    void queryClient.invalidateQueries({ queryKey: ['admin-offerings'] })
    void queryClient.invalidateQueries({ queryKey: ['offerings'] })
    void queryClient.invalidateQueries({ queryKey: ['roster'] })
  }

  // ---------- 开班 / 编辑 ----------

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    const current = (termsQuery.data?.items ?? []).find((term) => term.is_current)
    form.setFieldsValue({
      term_id: current?.term_id,
      capacity: 60,
      slots: [{ weekday: 1, start_period: 1, end_period: 2 }],
    })
    setModalOpen(true)
  }

  const openEdit = (row: OfferingVO) => {
    setEditing(row)
    form.resetFields()
    form.setFieldsValue({
      term_id: row.term_id,
      course_id: row.course_id,
      teacher_id: row.teacher_id,
      section_no: row.section_no,
      capacity: row.capacity,
      location: row.location,
      slots: row.slots.map((slot) => ({
        weekday: slot.weekday,
        start_period: slot.start_period,
        end_period: slot.end_period,
      })),
    })
    setModalOpen(true)
  }

  const closeModal = () => setModalOpen(false)

  const submit = async () => {
    let values: OfferingFormValues
    try {
      values = await form.validateFields()
    } catch {
      return
    }

    const slots = (values.slots ?? []).map((slot) => ({
      weekday: slot.weekday,
      start_period: slot.start_period,
      end_period: slot.end_period,
    }))

    try {
      if (editing) {
        await patchMutation.mutateAsync({
          id: editing.offering_id,
          body: {
            capacity: values.capacity,
            location: values.location?.trim() ?? '',
            slots,
          },
        })
        message.success('班次已更新')
      } else {
        const created = await createMutation.mutateAsync({
          term_id: values.term_id,
          course_id: values.course_id,
          teacher_id: values.teacher_id,
          section_no: values.section_no?.trim() || undefined,
          capacity: values.capacity,
          location: values.location?.trim() || undefined,
          slots,
        })
        message.success(`已开班：${created.course_name} ${created.section_no} 班`)
      }
      invalidateOfferings()
      setModalOpen(false)
      form.resetFields()
    } catch (error) {
      message.error(toErrorMessage(error, editing ? '更新班次失败' : '开班失败'))
    }
  }

  /** 停开 / 开放 */
  const toggleStatus = async (row: OfferingVO, open: boolean) => {
    try {
      await patchMutation.mutateAsync({
        id: row.offering_id,
        body: { status: open ? 1 : 2 },
      })
      message.success(open ? '班次已开放' : '班次已停开')
      invalidateOfferings()
    } catch (error) {
      message.error(toErrorMessage(error, '操作失败'))
    }
  }

  // ---------- 名单 ----------

  const openRoster = (offeringId: number) => setRosterId(offeringId)
  const closeRoster = () => setRosterId(null)

  const exportRoster = async () => {
    if (rosterId === null) return
    try {
      await downloadFile(`/offerings/${rosterId}/roster/export`)
    } catch (error) {
      message.error(toErrorMessage(error, '导出失败'))
    }
  }

  const forceDrop = async (studentNo: string) => {
    if (rosterId === null) return
    try {
      await forceDropMutation.mutateAsync({ student_no: studentNo, offering_id: rosterId })
      message.success(`已为 ${studentNo} 强制退课`)
      invalidateOfferings()
    } catch (error) {
      message.error(toErrorMessage(error, '强制退课失败'))
    }
  }

  const supplement = async (studentNo: string) => {
    if (rosterId === null) return
    if (!studentNo.trim()) {
      message.warning('请输入学号')
      return
    }
    try {
      const result = await supplementMutation.mutateAsync({
        student_no: studentNo.trim(),
        offering_id: rosterId,
      })
      message.success(`已为 ${studentNo} 补录「${result.course_name}」，剩余 ${result.remaining} 个名额`)
      invalidateOfferings()
    } catch (error) {
      message.error(toErrorMessage(error, '补录失败'))
    }
  }

  return {
    terms: termsQuery.data?.items ?? [],
    courses: coursesQuery.data?.items ?? [],

    offerings: offeringsQuery.data?.items ?? [],
    total: offeringsQuery.data?.total ?? 0,
    loading: offeringsQuery.isLoading,
    page,
    setPage,
    pageSize: PAGE_SIZE,

    modalOpen,
    editing,
    openCreate,
    openEdit,
    closeModal,
    form,
    submit,
    submitting: createMutation.isPending || patchMutation.isPending,
    toggleStatus,

    rosterId,
    roster: rosterQuery.data,
    rosterLoading: rosterQuery.isLoading,
    rosterError: rosterQuery.isError,
    openRoster,
    closeRoster,
    exportRoster,
    forceDrop,
    supplement,
  }
}
