import { useState } from 'react'
import { App, Form } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { courseApi } from '../../../api/course'
import { toErrorMessage } from '../../../request/errors'

/**
 * 学期表单的字段。
 *
 * starts_on / ends_on 运行时是 dayjs 对象（DatePicker 的 value）。
 * 用结构化类型声明它，只为调用 `.format()`，不引入 dayjs 依赖。
 */
export interface TermFormValues {
  code: string
  name: string
  starts_on: { format: (template: string) => string }
  ends_on: { format: (template: string) => string }
  is_current?: boolean
}

/** 后端 CreateTermRequest 接受 YYYY-MM-DD */
const DATE_FORMAT = 'YYYY-MM-DD'

/**
 * 管理端 · 学期的逻辑。
 *
 * 学期是全系统的地基：班次挂在学期上，轮次也挂在学期上，
 * 而「当前学期」决定学生打开选课台时默认看到哪一批课。
 */
export function useTerms() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()

  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm<TermFormValues>()
  const [switchingId, setSwitchingId] = useState(0)

  const query = useQuery({ queryKey: ['terms'], queryFn: courseApi.terms })

  const createMutation = useMutation({ mutationFn: courseApi.createTerm })
  const switchMutation = useMutation({ mutationFn: (id: number) => courseApi.setCurrentTerm(id) })

  /**
   * 学期一变，这些数据都跟着变：班次列表、当前轮次、我的班次、轮次列表。
   * 全部失效重取，避免出现"切了学期但列表还是旧的"。
   */
  const invalidateAll = () => {
    void queryClient.invalidateQueries({ queryKey: ['terms'] })
    void queryClient.invalidateQueries({ queryKey: ['offerings'] })
    void queryClient.invalidateQueries({ queryKey: ['my-offerings'] })
    void queryClient.invalidateQueries({ queryKey: ['admin-rounds'] })
    void queryClient.invalidateQueries({ queryKey: ['my-round'] })
  }

  const openModal = () => {
    form.resetFields()
    form.setFieldsValue({ is_current: true })
    setModalOpen(true)
  }

  const closeModal = () => setModalOpen(false)

  const submit = async () => {
    let values: TermFormValues
    try {
      values = await form.validateFields()
    } catch {
      return
    }

    const startsOn = values.starts_on.format(DATE_FORMAT)
    const endsOn = values.ends_on.format(DATE_FORMAT)
    if (endsOn <= startsOn) {
      message.warning('结束日期必须晚于开始日期')
      return
    }

    try {
      const created = await createMutation.mutateAsync({
        code: values.code.trim(),
        name: values.name.trim(),
        starts_on: startsOn,
        ends_on: endsOn,
        is_current: Boolean(values.is_current),
      })
      message.success(`已创建学期「${created.name}」`)
      invalidateAll()
      setModalOpen(false)
      form.resetFields()
    } catch (error) {
      // 学期码重复会返回 409，直接把后端的话显示出来
      message.error(toErrorMessage(error, '创建学期失败'))
    }
  }

  const setCurrent = async (termId: number) => {
    setSwitchingId(termId)
    try {
      const term = await switchMutation.mutateAsync(termId)
      message.success(`当前学期已切换为「${term.name}」`)
      invalidateAll()
    } catch (error) {
      message.error(toErrorMessage(error, '切换当前学期失败'))
    } finally {
      setSwitchingId(0)
    }
  }

  return {
    terms: query.data?.items ?? [],
    loading: query.isLoading,

    modalOpen,
    openModal,
    closeModal,
    form,
    submit,
    submitting: createMutation.isPending,

    switchingId,
    setCurrent,
  }
}
