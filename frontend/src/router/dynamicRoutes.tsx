import { BasicLayout } from '../layouts/BasicLayout'
import { AdminHomePage } from '../pages/admin'
import { AdminOfferingsPage } from '../pages/admin/offerings'
import { AdminRoundsPage } from '../pages/admin/rounds'
import { AdminTermsPage } from '../pages/admin/terms'
import { AdminUsersPage } from '../pages/admin/users'
import { StudentHomePage } from '../pages/student'
import { StudentSchedulePage } from '../pages/student/schedule'
import { TeacherHomePage } from '../pages/teacher'
import type { AppRoute } from './types'

/**
 * 动态路由：**登录后按权限决定挂不挂**的路由。
 *
 * 每条顶层路由都带 meta.roles。未登录时整张表不会被挂载，
 * 登录后只把当前用户有权访问的那些挂上去。
 *
 * 子路由不需要重复写 meta：父路由的入口权限已经决定了整棵子树可不可见。
 * 若某个子页面需要更细的权限（比如管理端的"账号管理"），单独给它加 meta.perms。
 *
 * 与后端的对应关系：
 *   学生  /student          选课台   GET  /offerings · POST /my/enrollments
 *         /student/schedule 我的课表 GET  /my/schedule
 *   管理  /admin             课程     GET/POST /courses
 *         /admin/offerings   班次     GET/POST /offerings · GET /offerings/:id/roster
 *         /admin/rounds      轮次     GET/POST /rounds
 */
export const dynamicRoutes: AppRoute[] = [
  {
    path: '/student',
    element: <BasicLayout />,
    meta: { title: '学生端', roles: ['student'] },
    children: [
      { index: true, element: <StudentHomePage /> },
      { path: 'schedule', element: <StudentSchedulePage /> },
    ],
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
    children: [
      { index: true, element: <AdminHomePage /> },
      { path: 'terms', element: <AdminTermsPage /> },
      { path: 'offerings', element: <AdminOfferingsPage /> },
      { path: 'rounds', element: <AdminRoundsPage /> },
      { path: 'users', element: <AdminUsersPage /> },
    ],
  },
]
