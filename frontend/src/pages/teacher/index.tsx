import { PagePlaceholder } from '../../components/PagePlaceholder'

/**
 * 教师端首页（占位）。
 *
 * 教师端是**只读**的：查自己开的课、看选课名单、导出点名册。
 * 教师不能发布抢课，也不能替学生选课——这是业务约束，权限表里也没给教师写权限。
 */
export function TeacherHomePage() {
  return (
    <PagePlaceholder
      title="教师端"
      description="只读：查看自己开设的班次与选课名单。"
      todos={[
        '我的开课：我负责的班次列表',
        '选课名单：分页查看 + 导出 CSV',
        '（不提供任何写操作）',
      ]}
    />
  )
}
