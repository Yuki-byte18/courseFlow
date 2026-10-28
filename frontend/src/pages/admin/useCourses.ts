import { useState } from 'react'
import { App, Form } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { courseApi } from '../../api/course'
import type { CourseVO } from '../../api/types'
import { toErrorMessage } from '../../request/errors'

/** 课程表单字段。code 只在新建时可填 —— 它是唯一键，不允许改。 */
export interface CourseFormValues {
  code: string
  name: string
  credits: number
  department?: string
  description?: string
}

/**
 * 管理端 · 课程管理的逻辑。
 *
 * 新建与编辑共用一个弹窗，靠 editing 区分：
 * 两个几乎一样的弹窗会让表单校验和字段同步变成两份维护成本。
 */
export function useCourses() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()

  const [keyword, setKeyword] = useState('')
  const [keywordInput, setKeywordInput] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<CourseVO | null>(null)
  const [form] = Form.useForm<CourseFormValues>()

  const query = useQuery({
    queryKey: ['courses', keyword],
    queryFn: () => courseApi.courses(keyword || undefined),
  })

  const createMutation = useMutation({ mutationFn: courseApi.createCourse })
  const updateMutation = useMutation({
    mutationFn: (args: { id: number; body: Parameters<typeof courseApi.patchCourse>[1] }) =>
      courseApi.patchCourse(args.id, args.body),
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['courses'] })
  }

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ credits: 2 })
    setModalOpen(true)
  }

  const openEdit = (row: CourseVO) => {
    setEditing(row)
    form.resetFields()
    form.setFieldsValue({
      code: row.code,
      name: row.name,
      credits: row.credits,
      department: row.department,
      description: row.description,
    })
    setModalOpen(true)
  }

  const closeModal = () => setModalOpen(false)

  const submit = async () => {
    let values: CourseFormValues
    try {
      values = await form.validateFields()
    } catch {
      return
    }

    try {
      if (editing) {
        await updateMutation.mutateAsync({
          id: editing.course_id,
          body: {
            name: values.name.trim(),
            credits: values.credits,
            department: values.department?.trim() ?? '',
            description: values.description?.trim() ?? '',
          },
        })
        message.success(`课程「${values.name}」已更新`)
      } else {
        const created = await createMutation.mutateAsync({
          code: values.code.trim(),
          name: values.name.trim(),
          credits: values.credits,
          department: values.department?.trim(),
          description: values.description?.trim(),
        })
        message.success(`已创建课程「${created.name}」`)
      }
      invalidate()
      setModalOpen(false)
      form.resetFields()
    } catch (error) {
      message.error(toErrorMessage(error, editing ? '更新课程失败' : '创建课程失败'))
    }
  }

  const onSearch = (value: string) => {
    setKeyword(value.trim())
    setKeywordInput(value)
  }

  return {
    courses: query.data?.items ?? [],
    loading: query.isLoading,
    keywordInput,
    setKeywordInput,
    onSearch,

    modalOpen,
    editing,
    openCreate,
    openEdit,
    closeModal,
    form,
    submit,
    submitting: createMutation.isPending || updateMutation.isPending,
  }
}
