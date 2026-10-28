import { Button, Form, Input, Modal, Popconfirm, Select, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import type { UserAdminVO } from '../../../api/types'
import { PageHeader } from '../../../components/PageHeader'
import { formatDateTime } from '../../../utils/format'
import { useUsers } from './useUsers'
import '../shared.css'

/** 角色码 → 中文名 + 标签配色 */
const ROLE_META: Record<string, { label: string; color?: string }> = {
  student: { label: '学生' },
  teacher: { label: '教师', color: 'gold' },
  admin: { label: '管理员', color: 'red' },
}

const ROLE_OPTIONS = [
  { value: 'student', label: '学生' },
  { value: 'teacher', label: '教师' },
  { value: 'admin', label: '管理员' },
]

/** 年级：0 = 未设置（不参与选课限制） */
const GRADE_OPTIONS = [
  { value: 0, label: '未设置' },
  { value: 1, label: '大一' },
  { value: 2, label: '大二' },
  { value: 3, label: '大三' },
  { value: 4, label: '大四' },
  { value: 5, label: '大五 / 延毕' },
]

const GRADE_LABELS: Record<number, string> = {
  1: '大一',
  2: '大二',
  3: '大三',
  4: '大四',
  5: '大五',
}

/**
 * 管理端 · 账号管理。
 *
 * 这里是系统里唯一能指定角色的地方：学生自助注册只能是 student，
 * 教师和管理员必须由这里创建。
 */
export function AdminUsersPage() {
  const {
    users,
    total,
    loading,
    role,
    onRoleChange,
    keywordInput,
    setKeywordInput,
    onSearch,
    page,
    setPage,
    pageSize,
    createOpen,
    openCreate,
    closeCreate,
    createForm,
    submitCreate,
    creating,
    editing,
    openEdit,
    closeEdit,
    editForm,
    submitEdit,
    updating,
    toggleStatus,
  } = useUsers()

  const columns: ColumnsType<UserAdminVO> = [
    {
      title: '账号',
      dataIndex: 'username',
      width: 140,
      render: (value: string) => <span className="admin-mono">{value}</span>,
    },
    { title: '姓名', dataIndex: 'real_name', width: 110 },
    {
      title: '角色',
      dataIndex: 'roles',
      width: 170,
      render: (roles: string[]) =>
        roles.length === 0 ? (
          <Tag>无角色</Tag>
        ) : (
          roles.map((code) => (
            <Tag key={code} color={ROLE_META[code]?.color}>
              {ROLE_META[code]?.label ?? code}
            </Tag>
          ))
        ),
    },
    {
      title: '年级',
      dataIndex: 'grade',
      width: 90,
      render: (value: number) =>
        value > 0 ? <Tag>{GRADE_LABELS[value] ?? value}</Tag> : <span className="admin-mono">—</span>,
    },
    {
      title: '邮箱',
      dataIndex: 'email',
      render: (value: string) => value || '—',
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 90,
      render: (value: number) =>
        value === 1 ? <Tag color="green">正常</Tag> : <Tag>已禁用</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 120,
      render: (value: string) => <span className="admin-mono">{formatDateTime(value)}</span>,
    },
    {
      title: '操作',
      key: 'action',
      width: 150,
      fixed: 'right',
      render: (_, row) => (
        <>
          <Button size="small" type="text" onClick={() => openEdit(row)}>
            编辑
          </Button>
          <Popconfirm
            title={row.status === 1 ? '禁用这个账号？' : '启用这个账号？'}
            description={row.status === 1 ? '禁用后该账号无法登录' : ''}
            okText="确定"
            cancelText="取消"
            onConfirm={() => toggleStatus(row)}
          >
            <Button size="small" type="text" danger={row.status === 1}>
              {row.status === 1 ? '禁用' : '启用'}
            </Button>
          </Popconfirm>
        </>
      ),
    },
  ]

  return (
    <div className="admin-page">
      <PageHeader
        title="账号"
        subtitle="学生自助注册；教师与管理员账号在这里创建，也可以在这里禁用 / 设年级。"
        extra={
          <Button type="primary" onClick={openCreate}>
            新建账号
          </Button>
        }
      />

      <div className="admin-toolbar">
        <Select
          style={{ minWidth: 140 }}
          allowClear
          placeholder="全部角色"
          value={role}
          onChange={onRoleChange}
          options={ROLE_OPTIONS}
        />
        <Input.Search
          className="admin-toolbar__search"
          allowClear
          placeholder="账号 / 姓名"
          value={keywordInput}
          onChange={(event) => setKeywordInput(event.target.value)}
          onSearch={onSearch}
        />
      </div>

      <Table<UserAdminVO>
        rowKey="user_id"
        className="admin-table"
        columns={columns}
        dataSource={users}
        loading={loading}
        scroll={{ x: 1080 }}
        locale={{ emptyText: '没有符合条件的账号' }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: false,
          showTotal: (count) => `共 ${count} 个账号`,
          onChange: setPage,
        }}
      />

      {/* ---------- 新建 ---------- */}
      <Modal
        title="新建账号"
        open={createOpen}
        onCancel={closeCreate}
        onOk={submitCreate}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
      >
        <Form form={createForm} layout="vertical" requiredMark={false}>
          <div className="admin-form-grid">
            <Form.Item
              label="账号"
              name="username"
              rules={[
                { required: true, message: '请输入账号' },
                { min: 4, max: 64, message: '账号长度 4-64' },
              ]}
            >
              <Input placeholder="T1001" />
            </Form.Item>

            <Form.Item
              label="初始密码"
              name="password"
              rules={[
                { required: true, message: '请输入初始密码' },
                { min: 6, max: 72, message: '密码长度 6-72' },
              ]}
            >
              <Input.Password placeholder="至少 6 位" />
            </Form.Item>

            <Form.Item
              label="姓名"
              name="real_name"
              rules={[{ required: true, message: '请输入姓名' }]}
            >
              <Input placeholder="张老师" />
            </Form.Item>

            <Form.Item label="邮箱" name="email" rules={[{ type: 'email', message: '邮箱格式不正确' }]}>
              <Input placeholder="选填" />
            </Form.Item>

            <Form.Item
              label="角色"
              name="roles"
              className="admin-form-grid--full"
              rules={[{ required: true, message: '请选择至少一个角色' }]}
            >
              <Select mode="multiple" placeholder="选择角色" options={ROLE_OPTIONS} />
            </Form.Item>
          </div>
        </Form>

        <div className="admin-hint">
          建出的账号是「初始密码」，应由本人首次登录后修改。系统目前还没有改密码接口。
        </div>
      </Modal>

      {/* ---------- 编辑 ---------- */}
      <Modal
        title={editing ? `编辑账号 ${editing.username}` : '编辑账号'}
        open={editing !== null}
        onCancel={closeEdit}
        onOk={submitEdit}
        confirmLoading={updating}
        okText="保存"
        cancelText="取消"
      >
        <Form form={editForm} layout="vertical" requiredMark={false}>
          <div className="admin-form-grid">
            <Form.Item
              label="姓名"
              name="real_name"
              rules={[{ required: true, message: '请输入姓名' }]}
            >
              <Input />
            </Form.Item>

            <Form.Item label="邮箱" name="email" rules={[{ type: 'email', message: '邮箱格式不正确' }]}>
              <Input placeholder="选填" />
            </Form.Item>

            <Form.Item
              label="年级"
              name="grade"
              extra="只在选课轮次配了「开放年级」时才起作用"
            >
              <Select options={GRADE_OPTIONS} />
            </Form.Item>

            <Form.Item label="状态" name="status">
              <Select
                options={[
                  { value: 1, label: '正常' },
                  { value: 2, label: '禁用' },
                ]}
              />
            </Form.Item>
          </div>
        </Form>

        <div className="admin-hint">账号名是登录凭据，不支持修改。</div>
      </Modal>
    </div>
  )
}
