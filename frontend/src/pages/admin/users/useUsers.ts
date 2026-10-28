import { useState } from 'react'
import { App, Form } from 'antd'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { adminApi } from '../../../api/admin'
import type { UpdateUserPayload, UserAdminVO } from '../../../api/types'
import { toErrorMessage } from '../../../request/errors'

/** 账号列表每页条数 */
const PAGE_SIZE = 15

/** 新建表单 */
export interface UserFormValues {
  username: string
  password: string
  real_name: string
  email?: string
  roles: string[]
}

/** 编辑表单（账号名不可改，它是登录凭据） */
export interface UserEditFormValues {
  real_name: string
  email?: string
  grade: number
  status: number
}

/**
 * 管理端 · 账号管理的逻辑。
 *
 * 建号是唯一能指定角色的入口，所以默认给 teacher ——
 * 管理员最常做的事就是开教师账号，学生自己注册。
 */
export function useUsers() {
  const { message } = App.useApp()
  const queryClient = useQueryClient()

  const [role, setRole] = useState<string | undefined>(undefined)
  const [keywordInput, setKeywordInput] = useState('')
  const [keyword, setKeyword] = useState('')
  const [page, setPage] = useState(1)

  const [createOpen, setCreateOpen] = useState(false)
  const [createForm] = Form.useForm<UserFormValues>()

  const [editing, setEditing] = useState<UserAdminVO | null>(null)
  const [editForm] = Form.useForm<UserEditFormValues>()

  const query = useQuery({
    queryKey: ['admin-users', role, keyword, page],
    queryFn: () =>
      adminApi.users({
        role,
        keyword: keyword || undefined,
        page,
        page_size: PAGE_SIZE,
      }),
  })

  const createMutation = useMutation({ mutationFn: adminApi.createUser })
  const updateMutation = useMutation({
    mutationFn: (args: { id: number; body: UpdateUserPayload }) =>
      adminApi.updateUser(args.id, args.body),
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['admin-users'] })
  }

  // ---------- 新建 ----------

  const openCreate = () => {
    createForm.resetFields()
    createForm.setFieldsValue({ roles: ['teacher'] })
    setCreateOpen(true)
  }

  const submitCreate = async () => {
    let values: UserFormValues
    try {
      values = await createForm.validateFields()
    } catch {
      return
    }

    try {
      const created = await createMutation.mutateAsync(values)
      message.success(`已创建账号「${created.username}」`)
      invalidate()
      setCreateOpen(false)
      createForm.resetFields()
    } catch (error) {
      message.error(toErrorMessage(error, '创建账号失败'))
    }
  }

  // ---------- 编辑 ----------

  const openEdit = (row: UserAdminVO) => {
    setEditing(row)
    editForm.resetFields()
    editForm.setFieldsValue({
      real_name: row.real_name,
      email: row.email,
      grade: row.grade,
      status: row.status,
    })
  }

  const closeEdit = () => setEditing(null)

  const submitEdit = async () => {
    if (!editing) return

    let values: UserEditFormValues
    try {
      values = await editForm.validateFields()
    } catch {
      return
    }

    try {
      await updateMutation.mutateAsync({
        id: editing.user_id,
        body: {
          real_name: values.real_name.trim(),
          email: values.email?.trim() ?? '',
          grade: values.grade,
          status: values.status,
        },
      })
      message.success('账号已更新')
      invalidate()
      setEditing(null)
    } catch (error) {
      message.error(toErrorMessage(error, '更新账号失败'))
    }
  }

  /** 列表里的快捷启用/禁用 */
  const toggleStatus = async (row: UserAdminVO) => {
    const next = row.status === 1 ? 2 : 1
    try {
      await updateMutation.mutateAsync({ id: row.user_id, body: { status: next } })
      message.success(next === 1 ? '账号已启用' : '账号已禁用')
      invalidate()
    } catch (error) {
      message.error(toErrorMessage(error, '操作失败'))
    }
  }

  const onSearch = (value: string) => {
    setKeyword(value.trim())
    setKeywordInput(value)
    setPage(1)
  }

  const onRoleChange = (value?: string) => {
    setRole(value)
    setPage(1)
  }

  return {
    users: query.data?.items ?? [],
    total: query.data?.total ?? 0,
    loading: query.isLoading,

    role,
    onRoleChange,
    keywordInput,
    setKeywordInput,
    onSearch,
    page,
    setPage,
    pageSize: PAGE_SIZE,

    createOpen,
    openCreate,
    closeCreate: () => setCreateOpen(false),
    createForm,
    submitCreate,
    creating: createMutation.isPending,

    editing,
    openEdit,
    closeEdit,
    editForm,
    submitEdit,
    updating: updateMutation.isPending,
    toggleStatus,
  }
}
