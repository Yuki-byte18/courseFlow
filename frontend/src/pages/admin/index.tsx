import { Button, Form, Input, InputNumber, Modal, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import type { CourseVO } from '../../api/types'
import { PageHeader } from '../../components/PageHeader'
import { formatCredits } from '../../utils/format'
import { useCourses } from './useCourses'
import './shared.css'

/**
 * 管理端 · 课程主数据。
 *
 * 课程是"开什么课"，班次才是"谁在什么时间上、有多少名额"。
 * 这里只管前者，开班在「班次」页。
 */
export function AdminHomePage() {
  const {
    courses,
    loading,
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
    submitting,
  } = useCourses()

  const columns: ColumnsType<CourseVO> = [
    {
      title: '课程号',
      dataIndex: 'code',
      width: 120,
      render: (value: string) => <span className="admin-mono">{value}</span>,
    },
    {
      title: '课程名',
      dataIndex: 'name',
      render: (value: string, row) => (
        <span>
          {value}
          {row.description && <span className="admin-sub">{row.description}</span>}
        </span>
      ),
    },
    {
      title: '学分',
      dataIndex: 'credits',
      width: 90,
      render: (value: number) => <span className="admin-mono">{formatCredits(value)}</span>,
    },
    {
      title: '开课院系',
      dataIndex: 'department',
      width: 150,
      render: (value: string) => value || '—',
    },
    {
      title: '操作',
      key: 'action',
      width: 90,
      fixed: 'right',
      render: (_, row) => (
        <Button size="small" type="text" onClick={() => openEdit(row)}>
          编辑
        </Button>
      ),
    },
  ]

  return (
    <div className="admin-page">
      <PageHeader
        title="课程"
        subtitle="课程主数据。要让学生能选，还需要在「班次」里开班。"
        extra={
          <Button type="primary" onClick={openCreate}>
            新建课程
          </Button>
        }
      />

      <div className="admin-toolbar">
        <Input.Search
          className="admin-toolbar__search"
          allowClear
          placeholder="课程名 / 课程号"
          value={keywordInput}
          onChange={(event) => setKeywordInput(event.target.value)}
          onSearch={onSearch}
        />
      </div>

      <Table<CourseVO>
        rowKey="course_id"
        className="admin-table"
        columns={columns}
        dataSource={courses}
        loading={loading}
        scroll={{ x: 760 }}
        locale={{ emptyText: '还没有课程' }}
        pagination={{ pageSize: 15, showSizeChanger: false }}
      />

      <Modal
        title={editing ? `编辑课程 ${editing.code}` : '新建课程'}
        open={modalOpen}
        onCancel={closeModal}
        onOk={submit}
        confirmLoading={submitting}
        okText={editing ? '保存' : '创建'}
        cancelText="取消"
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          <div className="admin-form-grid">
            <Form.Item
              label="课程号"
              name="code"
              rules={[{ required: true, message: '请输入课程号' }]}
            >
              {/* 课程号是唯一键，改它等于换一门课，所以编辑时锁死 */}
              <Input placeholder="CS101" disabled={Boolean(editing)} />
            </Form.Item>

            <Form.Item
              label="学分"
              name="credits"
              rules={[{ required: true, message: '请输入学分' }]}
            >
              <InputNumber min={0.5} max={20} step={0.5} style={{ width: '100%' }} />
            </Form.Item>

            <Form.Item
              label="课程名"
              name="name"
              className="admin-form-grid--full"
              rules={[{ required: true, message: '请输入课程名' }]}
            >
              <Input placeholder="数据结构" />
            </Form.Item>

            <Form.Item label="开课院系" name="department">
              <Input placeholder="计算机学院" />
            </Form.Item>

            <Form.Item label="简介" name="description">
              <Input placeholder="选填" />
            </Form.Item>
          </div>
        </Form>

        {editing && (
          <div className="admin-hint">
            改学分只影响之后的选课统计（历史记录里存的是选课当时的学分快照）。
          </div>
        )}
      </Modal>
    </div>
  )
}
