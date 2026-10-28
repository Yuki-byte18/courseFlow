import { Button, DatePicker, Form, Input, InputNumber, Modal, Popconfirm, Select, Table, Tag } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import type { RoundVO } from '../../../api/types'
import { PageHeader } from '../../../components/PageHeader'
import { formatCredits, formatDateTime } from '../../../utils/format'
import { useRounds } from './useRounds'
import '../shared.css'

const GRADE_OPTIONS = [
  { value: 0, label: '不限年级' },
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
 * 管理端 · 选课轮次。
 *
 * 一轮 = 一个开放时间窗 + 一个学分上限（+ 可选的年级限制）。
 * 学生只有在窗口内才能抢课，超过上限会被后端拒绝。
 */
export function AdminRoundsPage() {
  const {
    terms,
    rounds,
    loading,
    modalOpen,
    editing,
    openCreate,
    openEdit,
    closeModal,
    form,
    submit,
    submitting,
    toggleStatus,
  } = useRounds()

  const columns: ColumnsType<RoundVO> = [
    {
      title: '轮次',
      dataIndex: 'name',
      render: (value: string, row) => (
        <div>
          {value}
          <span className="admin-sub">term_id {row.term_id}</span>
        </div>
      ),
    },
    {
      title: '开放时间',
      dataIndex: 'opens_at',
      width: 140,
      render: (value: string) => <span className="admin-mono">{formatDateTime(value)}</span>,
    },
    {
      title: '关闭时间',
      dataIndex: 'closes_at',
      width: 140,
      render: (value: string) => <span className="admin-mono">{formatDateTime(value)}</span>,
    },
    {
      title: '学分上限',
      dataIndex: 'max_credits',
      width: 100,
      render: (value: number) => <span className="admin-mono">{formatCredits(value)}</span>,
    },
    {
      title: '开放年级',
      dataIndex: 'allow_grade',
      width: 100,
      render: (value: number) =>
        value > 0 ? <Tag>{GRADE_LABELS[value] ?? value}</Tag> : <span className="admin-mono">不限</span>,
    },
    {
      title: '状态',
      key: 'state',
      width: 96,
      render: (_, row) => {
        const now = Date.now()
        const opens = new Date(row.opens_at).getTime()
        const closes = new Date(row.closes_at).getTime()

        // 仅做展示判断；真正能不能选由后端在我的轮次接口里判定
        if (now < opens) return <Tag>未开始</Tag>
        if (now > closes) return <Tag>已结束</Tag>
        return <Tag color="gold">开放中</Tag>
      },
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
            title={Date.now() < new Date(row.closes_at).getTime() ? '提前关闭这个轮次？' : '重新开启这个轮次？'}
            okText="确定"
            cancelText="取消"
            onConfirm={() =>
              toggleStatus(row, Date.now() >= new Date(row.closes_at).getTime())
            }
          >
            <Button size="small" type="text">
              {Date.now() < new Date(row.closes_at).getTime() ? '关闭' : '重开'}
            </Button>
          </Popconfirm>
        </>
      ),
    },
  ]

  return (
    <div className="admin-page">
      <PageHeader
        title="选课轮次"
        subtitle="决定什么时候能选、这一轮最多选多少学分、面向哪一年级。"
        extra={
          <Button type="primary" onClick={openCreate}>
            新建轮次
          </Button>
        }
      />

      <Table<RoundVO>
        rowKey="round_id"
        className="admin-table"
        columns={columns}
        dataSource={rounds}
        loading={loading}
        scroll={{ x: 1000 }}
        locale={{ emptyText: '还没有选课轮次，学生将无法选课' }}
        pagination={false}
      />

      <Modal
        title={editing ? `编辑轮次「${editing.name}」` : '新建轮次'}
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
              label="学期"
              name="term_id"
              rules={[{ required: true, message: '请选择学期' }]}
            >
              <Select
                placeholder="选择学期"
                disabled={Boolean(editing)}
                options={terms.map((term) => ({
                  value: term.term_id,
                  label: term.is_current ? `${term.name}（当前）` : term.name,
                }))}
              />
            </Form.Item>

            <Form.Item
              label="轮次名"
              name="name"
              rules={[{ required: true, message: '请输入轮次名' }]}
            >
              <Input placeholder="第一轮选课" />
            </Form.Item>

            <Form.Item
              label="开放时间"
              name="opens_at"
              rules={editing ? [] : [{ required: true, message: '请选择开放时间' }]}
              extra={editing ? '留空表示不修改' : undefined}
            >
              <DatePicker showTime style={{ width: '100%' }} placeholder="选择开始时间" />
            </Form.Item>

            <Form.Item
              label="关闭时间"
              name="closes_at"
              rules={editing ? [] : [{ required: true, message: '请选择关闭时间' }]}
              extra={editing ? '留空表示不修改' : undefined}
            >
              <DatePicker showTime style={{ width: '100%' }} placeholder="选择结束时间" />
            </Form.Item>

            <Form.Item
              label="学分上限"
              name="max_credits"
              rules={[{ required: true, message: '请输入学分上限' }]}
            >
              <InputNumber min={1} max={60} step={0.5} style={{ width: '100%' }} />
            </Form.Item>

            <Form.Item label="开放年级" name="allow_grade">
              <Select options={GRADE_OPTIONS} />
            </Form.Item>
          </div>
        </Form>

        <div className="admin-hint">
          「开放年级」只在学生账号填了年级时才生效；未设置年级的账号不会被拦截。
        </div>
      </Modal>
    </div>
  )
}
