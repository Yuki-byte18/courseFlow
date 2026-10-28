import { Button, Checkbox, DatePicker, Form, Input, Modal, Popconfirm, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import type { TermVO } from '../../../api/types'
import { PageHeader } from '../../../components/PageHeader'
import { formatDate } from '../../../utils/format'
import { useTerms } from './useTerms'
import '../shared.css'

/**
 * 管理端 · 学期。
 *
 * 学期是全系统的地基：班次、选课轮次都挂在学期上；
 * 「当前学期」决定学生打开选课台时默认看到哪一批课。
 */
export function AdminTermsPage() {
  const {
    terms,
    loading,
    modalOpen,
    openModal,
    closeModal,
    form,
    submit,
    submitting,
    switchingId,
    setCurrent,
  } = useTerms()

  const columns: ColumnsType<TermVO> = [
    {
      title: '学期码',
      dataIndex: 'code',
      width: 130,
      render: (value: string) => <span className="admin-mono">{value}</span>,
    },
    {
      title: '名称',
      dataIndex: 'name',
      render: (value: string, row) => (
        <div>
          {value}
          {row.is_current && (
            <span className="admin-sub">
              <Tag color="gold">当前学期</Tag>
            </span>
          )}
        </div>
      ),
    },
    {
      title: '开始',
      dataIndex: 'starts_on',
      width: 130,
      render: (value: string) => <span className="admin-mono">{formatDate(value)}</span>,
    },
    {
      title: '结束',
      dataIndex: 'ends_on',
      width: 130,
      render: (value: string) => <span className="admin-mono">{formatDate(value)}</span>,
    },
    {
      title: '操作',
      key: 'action',
      width: 130,
      fixed: 'right',
      render: (_, row) => {
        if (row.is_current) {
          return <span className="admin-mono">—</span>
        }
        return (
          <Popconfirm
            title="设为当前学期？"
            description="学生选课台、轮次默认值都会切换到这一学期"
            okText="设为当前"
            cancelText="取消"
            onConfirm={() => setCurrent(row.term_id)}
          >
            <Button size="small" type="text" loading={switchingId === row.term_id}>
              设为当前
            </Button>
          </Popconfirm>
        )
      },
    },
  ]

  return (
    <div className="admin-page">
      <PageHeader
        title="学期"
        subtitle="全系统的地基：班次和选课轮次都挂在学期上，当前学期决定学生默认看到哪批课。"
        extra={
          <Button type="primary" onClick={openModal}>
            新建学期
          </Button>
        }
      />

      <Table<TermVO>
        rowKey="term_id"
        className="admin-table"
        columns={columns}
        dataSource={terms}
        loading={loading}
        scroll={{ x: 760 }}
        locale={{ emptyText: '还没有学期。先建一个学期，才能开班、配轮次。' }}
        pagination={false}
      />

      <Modal
        title="新建学期"
        open={modalOpen}
        onCancel={closeModal}
        onOk={submit}
        confirmLoading={submitting}
        okText="创建"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" requiredMark={false}>
          <div className="admin-form-grid">
            <Form.Item
              label="学期码"
              name="code"
              rules={[{ required: true, message: '请输入学期码' }]}
            >
              <Input placeholder="2026S" />
            </Form.Item>

            <Form.Item
              label="名称"
              name="name"
              rules={[{ required: true, message: '请输入名称' }]}
            >
              <Input placeholder="2026 春季学期" />
            </Form.Item>

            <Form.Item
              label="开始日期"
              name="starts_on"
              rules={[{ required: true, message: '请选择开始日期' }]}
            >
              <DatePicker style={{ width: '100%' }} placeholder="选择日期" />
            </Form.Item>

            <Form.Item
              label="结束日期"
              name="ends_on"
              rules={[{ required: true, message: '请选择结束日期' }]}
            >
              <DatePicker style={{ width: '100%' }} placeholder="选择日期" />
            </Form.Item>

            <Form.Item name="is_current" valuePropName="checked" className="admin-form-grid--full">
              <Checkbox>设为当前学期（会取消其它学期的「当前」标记）</Checkbox>
            </Form.Item>
          </div>
        </Form>
      </Modal>
    </div>
  )
}
