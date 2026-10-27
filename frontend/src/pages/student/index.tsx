import { PagePlaceholder } from '../../components/PagePlaceholder'

/**
 * 学生端首页（占位）。
 *
 * 属于动态路由：只有 roles 含 student 的用户能看到这条路由，
 * 未登录或教师/管理员访问 /student 会落到 404。
 */
export function StudentHomePage() {
  return (
    <PagePlaceholder
      title="学生端"
      description="选课台、我的课表、Agent 对话将在这里展开。"
      todos={[
        '选课台：课程列表 + 组合筛选（学分/教学楼/教师/时段）+ 抢课按钮',
        '我的课表：周视图',
        '余量实时刷新',
        'Agent 对话入口（M5）',
      ]}
    />
  )
}
