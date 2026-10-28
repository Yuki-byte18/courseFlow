import { Button, Empty, Popconfirm } from 'antd'

import { PageHeader } from '../../../components/PageHeader'
import { formatCredits, formatDateTime, formatSlots, weekdayName } from '../../../utils/format'
import { DAYS, PERIODS, useSchedule } from './useSchedule'
import './index.css'

/**
 * 学生端 · 我的课表。
 *
 * 左边是周视图（节次 × 星期），右边是已选课程清单。
 * 周视图用单个 grid 平铺：每个节次一行、每天一列，
 * 跨节的课靠相邻格子拼成一块，所以格子之间不能有 gap。
 */
export function StudentSchedulePage() {
  const { schedule, loading, cellMap, enrollments, onDrop, droppingId } = useSchedule()

  return (
    <div className="schedule">
      <PageHeader
        title="我的课表"
        subtitle="按周次查看已选课程；右侧可以直接退课。"
        extra={
          <span className="schedule__summary">
            {schedule?.total_count ?? 0} 门 · {formatCredits(schedule?.total_credits ?? 0)} 学分
          </span>
        }
      />

      <div className="schedule__layout">
        <div className={`timetable${loading ? ' timetable--loading' : ''}`}>
          <span className="timetable__corner">节次</span>
          {DAYS.map((day) => (
            <span className="timetable__day" key={`head-${day}`}>
              {weekdayName(day)}
            </span>
          ))}

          {PERIODS.map((period) => (
            <div className="timetable__row" key={`row-${period}`} style={{ display: 'contents' }}>
              <span className="timetable__period">{period}</span>
              {DAYS.map((day) => {
                const item = cellMap.get(`${day}-${period}`)
                const isStart = Boolean(item) && item!.start_period === period

                return (
                  <div
                    className={`timetable__cell${item ? ' timetable__cell--filled' : ''}`}
                    key={`${day}-${period}`}
                  >
                    {isStart && (
                      <div className="course-chip">
                        <span className="course-chip__name">{item!.course_name}</span>
                        <span className="course-chip__meta">
                          {item!.location || '—'} · {item!.teacher_name}
                        </span>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          ))}
        </div>

        <aside className="schedule__list">
          <div className="schedule__list-head">
            <span>已选课程</span>
            <span className="schedule__list-count">{enrollments.length}</span>
          </div>

          {enrollments.length === 0 ? (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="还没有选课"
              className="schedule__empty"
            />
          ) : (
            <ul className="enrolled">
              {enrollments.map((item) => (
                <li className="enrolled__item" key={item.enrollment_id}>
                  <div className="enrolled__main">
                    <span className="enrolled__name">{item.course_name}</span>
                    <span className="enrolled__meta">
                      {item.course_code} · {item.teacher_name}
                    </span>
                    <span className="enrolled__slots">{formatSlots(item.slots)}</span>
                    <span className="enrolled__time">
                      {formatCredits(item.credits)} 学分 · 选于 {formatDateTime(item.enrolled_at)}
                    </span>
                  </div>

                  <Popconfirm
                    title="确定退课？"
                    description="退课后名额会立刻释放"
                    okText="退课"
                    cancelText="取消"
                    onConfirm={() => onDrop(item.offering_id)}
                  >
                    <Button size="small" type="text" loading={droppingId === item.offering_id}>
                      退课
                    </Button>
                  </Popconfirm>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>
    </div>
  )
}
