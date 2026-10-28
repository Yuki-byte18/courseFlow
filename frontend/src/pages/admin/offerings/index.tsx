import {
  Alert,
  Button,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Table,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'

import type { OfferingVO, RosterEntry } from '../../../api/types'
import { PageHeader } from '../../../components/PageHeader'
import { StockBar } from '../../../components/StockBar'
import { formatCredits, formatDateTime, formatSlots } from '../../../utils/format'
import { useOfferings } from './useOfferings'
import '../shared.css'

const WEEKDAY_OPTIONS = [
  { value: 1, label: '周一' },
  { value: 2, label: '周二' },
  { value: 3, label: '周三' },
  { value: 4, label: '周四' },
  { value: 5, label: '周五' },
  { value: 6, label: '周六' },
  { value: 7, label: '周日' },
]

/**
 * 管理端 · 班次管理。
 *
 * 一个班次 = 课程 × 教师 × 学期 × 容量 × 时间段。
 * 名单抽屉里还能导出 CSV、强制退课、补录。
 */
export function AdminOfferingsPage() {
  const {
    terms,
    courses,
    offerings,
    total,
    loading,
    page,
    setPage,
    pageSize,
    modalOpen,
    editing,
    openCreate,
    openEdit,
    closeModal,
    form,
    submit,
    submitting,
    toggleStatus,
    rosterId,
    roster,
    rosterLoading,
    rosterError,
    openRoster,
    closeRoster,
    exportRoster,
    forceDrop,
    supplement,
  } = useOfferings()

  const columns: ColumnsType<OfferingVO> = [
    {
      title: '课程',
      key: 'course',
      render: (_, row) => (
        <div>
          {row.course_name}
          <span className="admin-sub">
            {row.course_code} · {formatCredits(row.credits)} 学分 · {row.department || '未指定'}
          </span>
        </div>
      ),
    },
    {
      title: '班号',
      dataIndex: 'section_no',
      width: 76,
      render: (value: string) => <span className="admin-mono">{value}</span>,
    },
    { title: '教师', dataIndex: 'teacher_name', width: 92 },
    {
      title: '时间',
      key: 'slots',
      width: 170,
      render: (_, row) => <span className="admin-mono">{formatSlots(row.slots)}</span>,
    },
    {
      title: '地点',
      dataIndex: 'location',
      width: 100,
      render: (value: string) => value || '—',
    },
    {
      title: '余量',
      key: 'stock',
      width: 160,
      render: (_, row) => (
        <StockBar taken={row.taken} capacity={row.capacity} remaining={row.remaining} />
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 170,
      fixed: 'right',
      render: (_, row) => (
        <>
          <Button size="small" type="text" onClick={() => openRoster(row.offering_id)}>
            名单
          </Button>
          <Button size="small" type="text" onClick={() => openEdit(row)}>
            编辑
          </Button>
          <Popconfirm
            title="停开这个班次？"
            description="停开后学生看不到它，已选的学生不受影响"
            okText="停开"
            cancelText="取消"
            onConfirm={() => toggleStatus(row, false)}
          >
            <Button size="small" type="text" danger>
              停开
            </Button>
          </Popconfirm>
        </>
      ),
    },
  ]

  const rosterColumns: ColumnsType<RosterEntry> = [
    {
      title: '学号',
      dataIndex: 'student_no',
      width: 130,
      render: (value: string) => <span className="admin-mono">{value}</span>,
    },
    { title: '姓名', dataIndex: 'real_name' },
    {
      title: '选课时间',
      dataIndex: 'enrolled_at',
      width: 120,
      render: (value: string) => <span className="admin-mono">{formatDateTime(value)}</span>,
    },
    {
      title: '操作',
      key: 'action',
      width: 90,
      render: (_, row) => (
        <Popconfirm
          title={`为 ${row.student_no} 强制退课？`}
          description="会立刻释放一个名额"
          okText="退课"
          cancelText="取消"
          onConfirm={() => forceDrop(row.student_no)}
        >
          <Button size="small" type="text" danger>
            强制退课
          </Button>
        </Popconfirm>
      ),
    },
  ]

  return (
    <div className="admin-page">
      <PageHeader
        title="班次"
        subtitle="开班 = 课程 × 教师 × 学期 × 容量。开了班学生才能选。"
        extra={
          <Button type="primary" onClick={openCreate}>
            开班
          </Button>
        }
      />

      <Table<OfferingVO>
        rowKey="offering_id"
        className="admin-table"
        columns={columns}
        dataSource={offerings}
        loading={loading}
        scroll={{ x: 1080 }}
        locale={{ emptyText: '还没有班次' }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: false,
          showTotal: (count) => `共 ${count} 个班次`,
          onChange: setPage,
        }}
      />

      {/* ---------- 开班 / 编辑 ---------- */}
      <Modal
        title={editing ? `编辑 ${editing.course_name} ${editing.section_no} 班` : '开班'}
        open={modalOpen}
        onCancel={closeModal}
        onOk={submit}
        confirmLoading={submitting}
        okText={editing ? '保存' : '开班'}
        cancelText="取消"
        width={680}
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
              label="课程"
              name="course_id"
              rules={[{ required: true, message: '请选择课程' }]}
            >
              <Select
                showSearch
                disabled={Boolean(editing)}
                placeholder="选择课程"
                optionFilterProp="label"
                options={courses.map((course) => ({
                  value: course.course_id,
                  label: `${course.code} ${course.name}`,
                }))}
              />
            </Form.Item>

            <Form.Item
              label="授课教师 user_id"
              name="teacher_id"
              rules={[{ required: true, message: '请输入教师 user_id' }]}
            >
              <InputNumber min={1} disabled={Boolean(editing)} style={{ width: '100%' }} />
            </Form.Item>

            <Form.Item label="班号" name="section_no">
              <Input placeholder="留空默认 01" disabled={Boolean(editing)} />
            </Form.Item>

            <Form.Item
              label="容量"
              name="capacity"
              rules={[{ required: true, message: '请输入容量' }]}
              extra={editing ? `已选 ${editing.taken} 人，容量不能小于它` : undefined}
            >
              <InputNumber min={1} max={10000} style={{ width: '100%' }} />
            </Form.Item>

            <Form.Item label="上课地点" name="location">
              <Input placeholder="一教 301" />
            </Form.Item>
          </div>

          {/* 时间段：支持多条（一门课一周上两次是常态） */}
          <Form.List name="slots">
            {(fields, { add, remove }) => (
              <div className="slot-list">
                <div className="slot-list__head">
                  <span>上课时间</span>
                  <Button size="small" type="text" onClick={() => add({ weekday: 1, start_period: 1, end_period: 2 })}>
                    + 添加时段
                  </Button>
                </div>

                {fields.length === 0 && (
                  <div className="slot-list__empty">没有时间段的话，这个班次不会出现在学生课表上</div>
                )}

                {fields.map((field) => (
                  <div className="slot-row" key={field.key}>
                    <Form.Item name={[field.name, 'weekday']} noStyle>
                      <Select options={WEEKDAY_OPTIONS} style={{ width: 96 }} />
                    </Form.Item>
                    <Form.Item name={[field.name, 'start_period']} noStyle>
                      <InputNumber min={1} max={12} style={{ width: 76 }} placeholder="起" />
                    </Form.Item>
                    <span className="slot-row__sep">—</span>
                    <Form.Item name={[field.name, 'end_period']} noStyle>
                      <InputNumber min={1} max={12} style={{ width: 76 }} placeholder="止" />
                    </Form.Item>
                    <Button size="small" type="text" danger onClick={() => remove(field.name)}>
                      删除
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </Form.List>
        </Form>

        {editing && (
          <div className="admin-hint">改动容量和地点会立刻对学生生效；时间段会整体替换。</div>
        )}
      </Modal>

      {/* ---------- 名单 ---------- */}
      <Drawer
        title={roster ? `${roster.course_name} ${roster.section_no} 班 · 名单` : '选课名单'}
        open={rosterId !== null}
        onClose={closeRoster}
        width={640}
      >
        {rosterError && (
          <Alert
            type="warning"
            showIcon
            message="没拿到名单"
            description="班次不存在，或者不是你开设的（越权访问统一返回 404）。"
            style={{ marginBottom: 16 }}
          />
        )}

        {roster && (
          <>
            <div className="roster-tools">
              <span className="roster-tools__meta">
                已选 {roster.taken} / 容量 {roster.capacity}
              </span>
              <Button size="small" onClick={exportRoster}>
                导出 CSV
              </Button>
            </div>

            <Input.Search
              className="roster-tools__supplement"
              allowClear
              placeholder="输入学号补录（绕过轮次窗口与学分上限）"
              enterButton="补录"
              onSearch={supplement}
            />

            <Table<RosterEntry>
              rowKey="enrollment_id"
              size="small"
              columns={rosterColumns}
              dataSource={roster.entries}
              loading={rosterLoading}
              pagination={false}
              locale={{ emptyText: '还没有人选这个班' }}
            />
          </>
        )}
      </Drawer>
    </div>
  )
}
