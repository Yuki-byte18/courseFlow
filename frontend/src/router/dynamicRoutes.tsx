import { BasicLayout } from '../layouts/BasicLayout'
import { AdminHomePage } from '../pages/admin'
import { StudentHomePage } from '../pages/student'
import { TeacherHomePage } from '../pages/teacher'
import type { AppRoute } from './types'

/**
 * 动态路由：**登录后按权限决定挂不挂**的路由。
 *
 * 每条都带 meta.roles。未登录时整张表不会被挂载，
 * 登录后只把当前用户有权访问的那些挂上去。
 *
 * 后续接业务页面时，就在这里按端补充：
 *
 *   {
 *     path: '/student',
 *     element: <BasicLayout />,
 *     meta: { title: '学生端', roles: ['student'] },
 *     children: [
 *       { index: true, element: <StudentHomePage /> },
 *       { path: 'courses', element: <CourseListPage /> },      // 选课台
 *       { path: 'schedule', element: <MySchedulePage /> },     // 我的课表
 *     ],
 *   }
 *
 * 子路由不需要重复写 meta：父路由的入口权限已经决定了整棵子树可不可见。
 * 若某个子页面需要更细的权限（比如管理端的"账号管理"），单独给它加 meta.perms。
 */
export const dynamicRoutes: AppRoute[] = [
  {
    path: '/student',
    element: <BasicLayout />,
    meta: { title: '学生端', roles: ['student'] },
    children: [{ index: true, element: <StudentHomePage /> }],
  },
  {
    path: '/teacher',
    element: <BasicLayout />,
    meta: { title: '教师端', roles: ['teacher'] },
    children: [{ index: true, element: <TeacherHomePage /> }],
  },
  {
    path: '/admin',
    element: <BasicLayout />,
    meta: { title: '管理端', roles: ['admin'] },
    children: [{ index: true, element: <AdminHomePage /> }],
  },
]
