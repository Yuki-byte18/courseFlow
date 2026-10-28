import { useState } from 'react'
import { App, Form } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { courseApi } from '../../../api/course'
import { enrollmentApi } from '../../../api/enrollment'
import type { RoundVO } from '../../../api/types'
import { toErrorMessage } from '../../../request/errors'

/**
 * 轮次表单字段。
 *
 * opens_at / closes_at 在运行时是 dayjs（DatePicker 的 value）。
 * 用结构化类型声明只为调用 `.format()`，不引入 dayjs 依赖。
 *
 * 编辑时这两个字段允许留空 —— 留空表示"不改时间窗"。
 * 这样可以避开"从 ISO 字符串回填 DatePicker 需要 dayjs"的问题。
 */
export interface RoundFormValues {
  term_id: number
  name: string
  opens_at?: { format: (template: string) => string }
  closes_at?: { format: (template: string) => string }
  max_credits: number
  allow_grade: number
}

const TIME_FORMAT = 'YYYY-MM-DD HH:mm:ss'

/**
 * 管理端 · 选课轮次的逻辑。
 *
 * 轮次决定「什么时候能选」「最多选多少学分」「面向哪一年级」。
 */
export function useRounds() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()

  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<RoundVO | null>(null)
  const [form] = Form.useForm<RoundFormValues>()

  const termsQuery = useQuery({ queryKey: ['terms'], queryFn: courseApi.terms })
  const roundsQuery = useQuery({ queryKey: ['admin-rounds'], queryFn: () => enrollmentApi.rounds() })

  const createMutation = useMutation({ mutationFn: courseApi.createRound })
  const patchMutation = useMutation({
    mutationFn: (args: { id: number; body: Parameters<typeof enrollmentApi.patchRound>[1] }) =>
      enrollmentApi.patchRound(args.id, args.body),
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['admin-rounds'] })
    void queryClient.invalidateQueries({ queryKey: ['my-round'] })
  }

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    const current = (termsQuery.data?.items ?? []).find((term) => term.is_current)
    form.setFieldsValue({
      term_id: current?.term_id,
      max_credits: 30,
      allow_grade: 0,
    })
    setModalOpen(true)
  }

  const openEdit = (row: RoundVO) => {
    setEditing(row)
    form.resetFields()
    // 时间窗刻意不回填：见 RoundFormValues 的注释
    form.setFieldsValue({
      term_id: row.term_id,
      name: row.name,
      max_credits: row.max_credits,
      allow_grade: 0,
    })
    setModalOpen(true)
  }

  const closeModal = () => setModalOpen(false)

  const submit = async () => {
    let values: RoundFormValues
    try {
      values = await form.validateFields()
    } catch {
      return
    }

    const opensAt = values.opens_at?.format(TIME_FORMAT)
    const closesAt = values.closes_at?.format(TIME_FORMAT)

    try {
      if (editing) {
        await patchMutation.mutateAsync({
          id: editing.round_id,
          body: {
            name: values.name.trim(),
            max_credits: values.max_credits,
            allow_grade: values.allow_grade,
            ...(opensAt ? { opens_at: opensAt } : {}),
            ...(closesAt ? { closes_at: closesAt } : {}),
          },
        })
        message.success('轮次已更新')
      } else {
        if (!opensAt || !closesAt) {
          message.warning('新建轮次必须填写开放与关闭时间')
          return
        }
        const created = await createMutation.mutateAsync({
          term_id: values.term_id,
          name: values.name.trim(),
          opens_at: opensAt,
          closes_at: closesAt,
          max_credits: values.max_credits,
          allow_grade: values.allow_grade,
        })
        message.success(`已创建轮次「${created.name}」`)
      }
      invalidate()
      setModalOpen(false)
      form.resetFields()
    } catch (error) {
      message.error(toErrorMessage(error, editing ? '更新轮次失败' : '创建轮次失败'))
    }
  }

  /** 列表里的开启 / 关闭 */
  const toggleStatus = async (row: RoundVO, open: boolean) => {
    try {
      await patchMutation.mutateAsync({ id: row.round_id, body: { status: open ? 1 : 2 } })
      message.success(open ? '轮次已开启' : '轮次已关闭')
      invalidate()
    } catch (error) {
      message.error(toErrorMessage(error, '操作失败'))
    }
  }

  return {
    terms: termsQuery.data?.items ?? [],
    rounds: roundsQuery.data?.items ?? [],
    loading: roundsQuery.isLoading,

    modalOpen,
    editing,
    openCreate,
    openEdit,
    closeModal,
    form,
    submit,
    submitting: createMutation.isPending || patchMutation.isPending,
    toggleStatus,
  }
}
