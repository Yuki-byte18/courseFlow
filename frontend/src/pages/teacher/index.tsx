import { Alert, Button, Drawer, Input, Modal, Select, Table, Tag, Tooltip } from 'antd'
import type { ColumnsType } from 'antd/es/table'

import { PERMS } from '../../api/perms'
import type { OfferingVO, RosterEntry } from '../../api/types'
import { PageHeader } from '../../components/PageHeader'
import { StockBar } from '../../components/StockBar'
import { useHasPerm } from '../../hooks/usePermission'
import { formatCredits, formatDateTime, formatSlots } from '../../utils/format'
import { useTeacherDesk } from './useTeacherDesk'
import './index.css'

const STATUS_LABEL: Record<string, { text: string; color?: string }> = {
  created: { text: '已创建', color: 'green' },
  skipped: { text: '已跳过' },
  failed: { text: '失败', color: 'red' },
}

/**
 * 教师端 · 我的班次。
 *
 * 三块：我的开课列表、某个班的选课名单、一键导入课表。
 *
 * 导入的每一行都由服务端强制把 teacher_id 设成当前登录用户，
 * 所以这里不需要、也不能指定教师。
 */
export function TeacherHomePage() {
  const {
    terms,
    activeTermId,
    onTermChange,
    offerings,
    total,
    loading,
    page,
    setPage,
    pageSize,
    rosterId,
    roster,
    rosterLoading,
    rosterError,
    openRoster,
    closeRoster,
    importOpen,
    openImport,
    closeImport,
    csv,
    setCsv,
    fillSample,
    parsed,
    submitImport,
    importing,
    result,
  } = useTeacherDesk()

  // 没有导入权限时直接禁用按钮，而不是让人点了再吃一个 403。
  // 注意这只是体验层：真正的拦截在后端接口上。
  const canImport = useHasPerm(PERMS.offeringImport)

  const columns: ColumnsType<OfferingVO> = [
    {
      title: '课程',
      key: 'course',
      render: (_, row) => (
        <div>
          {row.course_name}
          <span className="teacher-sub">
            {row.course_code} · {formatCredits(row.credits)} 学分
          </span>
        </div>
      ),
    },
    {
      title: '班号',
      dataIndex: 'section_no',
      width: 76,
      render: (value: string) => <span className="teacher-mono">{value}</span>,
    },
    {
      title: '时间',
      key: 'slots',
      width: 180,
      render: (_, row) => <span className="teacher-mono">{formatSlots(row.slots)}</span>,
    },
    {
      title: '地点',
      dataIndex: 'location',
      width: 120,
      render: (value: string) => value || '—',
    },
    {
      title: '选课情况',
      key: 'stock',
      width: 168,
      render: (_, row) => (
        <StockBar taken={row.taken} capacity={row.capacity} remaining={row.remaining} />
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: 96,
      fixed: 'right',
      render: (_, row) => (
        <Button size="small" type="text" onClick={() => openRoster(row.offering_id)}>
          名单
        </Button>
      ),
    },
  ]

  const rosterColumns: ColumnsType<RosterEntry> = [
    {
      title: '学号',
      dataIndex: 'student_no',
      width: 140,
      render: (value: string) => <span className="teacher-mono">{value}</span>,
    },
    { title: '姓名', dataIndex: 'real_name' },
    {
      title: '选课时间',
      dataIndex: 'enrolled_at',
      width: 130,
      render: (value: string) => <span className="teacher-mono">{formatDateTime(value)}</span>,
    },
  ]

  return (
    <div className="teacher">
      <PageHeader
        title="我的班次"
        subtitle="你开设的班次与选课名单都在这里；也可以用一键导入批量建课。"
        extra={
          canImport ? (
            <Button type="primary" onClick={openImport}>
              一键导入课表
            </Button>
          ) : (
            <Tooltip title="当前账号缺少 offering:import 权限。如果是刚授予的，请退出重新登录让权限生效。">
              <Button type="primary" disabled>
                一键导入课表
              </Button>
            </Tooltip>
          )
        }
      />

      <div className="teacher__toolbar">
        <Select
          style={{ minWidth: 200 }}
          value={activeTermId || undefined}
          onChange={onTermChange}
          placeholder="选择学期"
          options={terms.map((term) => ({
            value: term.term_id,
            label: term.is_current ? `${term.name}（当前）` : term.name,
          }))}
        />
      </div>

      {/*
        没权限时给出可执行的排查顺序，而不是只把一个按钮置灰。
        权限码来自 JWT，所以最常见的原因就是「迁移没跑」或「token 是旧的」。
      */}
      {!canImport && (
        <Alert
          type="warning"
          showIcon
          message="当前账号没有 offering:import 权限，无法导入课表"
          description={
            <ol className="teacher__lack">
              <li>
                确认数据库已应用迁移 <code>0005_offering_import_permission.sql</code>
                （在 backend 目录执行 <code>goose ... status</code>，版本 5 应为 Applied）。
              </li>
              <li>
                迁移刚跑的话，请<strong>退出登录再重新登录</strong> ——
                权限快照在 JWT 里，旧 token 不会自动获得新权限。
              </li>
              <li>重登后仍然没有，请在「账号」页确认该账号的角色是「教师」。</li>
            </ol>
          }
        />
      )}

      <Table<OfferingVO>
        rowKey="offering_id"
        className="teacher__table"
        columns={columns}
        dataSource={offerings}
        loading={loading}
        scroll={{ x: 900 }}
        locale={{ emptyText: '这个学期你还没有开班，可以用「一键导入课表」批量创建' }}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: false,
          showTotal: (count) => `共 ${count} 个班次`,
          onChange: setPage,
        }}
      />

      {/* ---------- 选课名单 ---------- */}
      <Drawer
        title={roster ? `${roster.course_name} ${roster.section_no} 班 · 名单` : '选课名单'}
        open={rosterId !== null}
        onClose={closeRoster}
        width={520}
      >
        {rosterError && (
          <Alert
            type="warning"
            showIcon
            message="没拿到名单"
            description="这个班次不存在，或者不是你开设的（越权访问统一返回 404，不泄露班次是否存在）。"
          />
        )}

        {roster && (
          <>
            <div className="teacher__roster-meta">
              已选 {roster.taken} / 容量 {roster.capacity}
            </div>
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

      {/* ---------- 一键导入 ---------- */}
      <Modal
        title="一键导入课表"
        open={importOpen}
        onCancel={closeImport}
        onOk={submitImport}
        confirmLoading={importing}
        okText={parsed.rows.length > 0 ? `导入 ${parsed.rows.length} 行` : '导入'}
        cancelText="关闭"
        width={640}
      >
        <div className="import">
          <p className="import__hint">
            每行格式：<code>课程号,班号,容量,地点,星期(1-7),起始节,结束节</code>
            <br />
            空行与 <code>#</code> 开头的行会被忽略；一行出错不影响其它行。
          </p>

          <div className="import__actions">
            <Button size="small" onClick={fillSample}>
              填入示例
            </Button>
            <span className="import__stat">
              已解析 <strong>{parsed.rows.length}</strong> 行
              {parsed.errors.length > 0 && (
                <>
                  ，<span className="import__stat-error">{parsed.errors.length} 行有问题</span>
                </>
              )}
            </span>
          </div>

          <Input.TextArea
            rows={8}
            value={csv}
            onChange={(event) => setCsv(event.target.value)}
            placeholder={'D001,01,60,一教 301,1,1,2\nD002,01,60,一教 405,3,5,6'}
            className="import__input"
          />

          {parsed.errors.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message="部分行无法解析，这些行不会被提交"
              description={
                <ul className="import__errors">
                  {parsed.errors.slice(0, 8).map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                  {parsed.errors.length > 8 && <li>…… 还有 {parsed.errors.length - 8} 条</li>}
                </ul>
              }
            />
          )}

          {result && (
            <div className="import__result">
              <div className="import__summary">
                <Tag color="green">新增 {result.created}</Tag>
                <Tag>跳过 {result.skipped}</Tag>
                <Tag color={result.failed > 0 ? 'red' : undefined}>失败 {result.failed}</Tag>
              </div>

              {result.results.some((row) => row.status !== 'created') && (
                <ul className="import__lines">
                  {result.results
                    .filter((row) => row.status !== 'created')
                    .map((row) => (
                      <li key={row.index}>
                        第 {row.index + 1} 行 · {STATUS_LABEL[row.status]?.text ?? row.status} ·{' '}
                        {row.message}
                      </li>
                    ))}
                </ul>
              )}
            </div>
          )}
        </div>
      </Modal>
    </div>
  )
}
