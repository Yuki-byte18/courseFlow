import { PagePlaceholder } from '../../components/PagePlaceholder'

/**
 * 管理端首页（占位）。
 *
 * 管理端是唯一有写权限的端：排课、配轮次、预热库存、补录、看大盘。
 */
export function AdminHomePage() {
  return (
    <PagePlaceholder
      title="管理端"
      description="排课、配置选课轮次、预热库存、补录、查看大盘与报表。"
      todos={[
        '课程库：课程主数据维护',
        '开课班次：排课（课程 + 教师 + 时间 + 地点 + 容量）',
        '选课轮次：开放时间、学分上限、开放年级',
        '库存预热：把容量灌进 Redis（开抢前必做）',
        '实时大盘与历史报表（M4）',
        '账号管理：创建教师/管理员账号',
      ]}
    />
  )
}
