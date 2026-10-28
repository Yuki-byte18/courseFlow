import { Button, Input, Popconfirm, Select, Table } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import type { OfferingVO } from '../../api/types'
import { PageHeader } from '../../components/PageHeader'
import { StockBar } from '../../components/StockBar'
import { formatCredits, formatSlots } from '../../utils/format'
import { useOfferingBoard } from './useOfferingBoard'
import './index.css'

/**
 * 学生端 · 选课台。
 *
 * 顶部一条轮次状态（能不能选、学分用了多少），
 * 中间筛选，下面班次列表 —— 列表每行就是一个可抢的名额。
 */
export function StudentHomePage() {
  const {
    terms,
    activeTermId,
    onTermChange,
    keywordInput,
    setKeywordInput,
    onSearch,
    page,
    setPage,
    pageSize,
    round,
    offerings,
    total,
    offeringsLoading,
    enrolledIds,
    onEnroll,
    onDrop,
    enrollingId,
    droppingId,
  } = useOfferingBoard()

  const columns: ColumnsType<OfferingVO> = [
    {
      title: '课程',
      key: 'course',
      render: (_, row) => (
        <div className="cell-course">
          <span className="cell-course__name">{row.course_name}</span>
          <span className="cell-course__meta">
            {row.course_code} · {row.section_no} 班 · {row.department || '未指定院系'}
          </span>
        </div>
      ),
    },
    {
      title: '学分',
      dataIndex: 'credits',
      width: 78,
      render: (value: number) => <span className="cell-mono">{formatCredits(value)}</span>,
    },
    {
      title: '教师',
      dataIndex: 'teacher_name',
      width: 96,
    },
    {
      title: '时间',
      key: 'slots',
      width: 190,
      render: (_, row) => <span className="cell-mono">{formatSlots(row.slots)}</span>,
    },
    {
      title: '地点',
      dataIndex: 'location',
      width: 120,
      render: (value: string) => value || '—',
    },
    {
      title: '余量',
      key: 'stock',
      width: 168,
      render: (_, row) => (
        <StockBar taken={row.taken} capacity={row.capacity} remaining={row.remaining} />
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 104,
      fixed: 'right',
      render: (_, row) => {
        if (enrolledIds.has(row.offering_id)) {
          return (
            <Popconfirm
              title="确定退课？"
              description="退课后名额会立刻释放给其他同学"
              okText="退课"
              cancelText="取消"
              onConfirm={() => onDrop(row.offering_id)}
            >
              <Button size="small" loading={droppingId === row.offering_id}>
                退课
              </Button>
            </Popconfirm>
          )
        }

        if (row.remaining <= 0) {
          return (
            <Button size="small" disabled>
              已满
            </Button>
          )
        }

        return (
          <Button
            size="small"
            type="primary"
            loading={enrollingId === row.offering_id}
            onClick={() => onEnroll(row.offering_id)}
          >
            选课
          </Button>
        )
      },
    },
  ]

  return (
    <div className="board">
      <PageHeader
        title="选课台"
        subtitle="按课程名或课程号搜索，点「选课」即占一个名额。"
      />

      {round && (
        <div className={`board__round${round.open ? '' : ' board__round--closed'}`}>
          <i className="board__round-dot" aria-hidden="true" />
          <span className="board__round-name">{round.name || '选课轮次'}</span>
          <span className="board__round-sep">·</span>
          <span className="board__round-credits">
            已选 {formatCredits(round.used_credits)} / {formatCredits(round.max_credits)} 学分
          </span>
          {!round.open && <span className="board__round-reason">{round.reason}</span>}
        </div>
      )}

      <div className="board__filters">
        <Select
          className="board__term"
          value={activeTermId || undefined}
          onChange={onTermChange}
          placeholder="选择学期"
          options={terms.map((term) => ({
            value: term.term_id,
            label: term.is_current ? `${term.name}（当前）` : term.name,
          }))}
        />

        <Input.Search
          className="board__search"
          allowClear
          placeholder="课程名 / 课程号"
          value={keywordInput}
          onChange={(event) => setKeywordInput(event.target.value)}
          onSearch={onSearch}
        />
      </div>

      <Table<OfferingVO>
        rowKey="offering_id"
        className="board__table"
        columns={columns}
        dataSource={offerings}
        loading={offeringsLoading}
        scroll={{ x: 1020 }}
        locale={{ emptyText: '没有符合条件的班次' }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: false,
          showTotal: (count) => `共 ${count} 个班次`,
          onChange: setPage,
        }}
      />
    </div>
  )
}
