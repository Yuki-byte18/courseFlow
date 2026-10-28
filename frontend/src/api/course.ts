import { request } from '../request/client'
import type {
  CourseVO,
  CreateCoursePayload,
  CreateOfferingPayload,
  CreateRoundPayload,
  CreateTermPayload,
  ImportResult,
  ImportRowPayload,
  OfferingPage,
  OfferingQuery,
  OfferingVO,
  RosterVO,
  RoundVO,
  TermVO,
  UpdateCoursePayload,
  UpdateOfferingPayload,
} from './types'
/**
 * 课程与开课域接口。
 *
 * 对应后端 internal/course/controller.go：
 *   GET  /terms                       学期列表
 *   GET  /offerings                   班次列表（选课台）
 *   GET  /offerings/:id               班次详情
 *   GET  /offerings/:id/roster        选课名单
 *   GET  /courses                     课程主数据
 *   POST /admin/terms                 新建学期
 *   POST /admin/courses               新建课程
 *   POST /admin/offerings             开班
 */
export const courseApi = {
  /** 学期列表。 */
  terms(): Promise<{ items: TermVO[] }> {
    return request<{ items: TermVO[] }>('/terms')
  },

  /** 班次列表（支持学期 / 院系 / 关键词 / 分页）。 */
  offerings(query: OfferingQuery): Promise<OfferingPage> {
    return request<OfferingPage>('/offerings', {
      query: {
        term_id: query.term_id,
        department: query.department,
        keyword: query.keyword,
        page: query.page,
        page_size: query.page_size,
      },
    })
  },

  /** 班次详情。 */
  offering(id: number): Promise<OfferingVO> {
    return request<OfferingVO>(`/offerings/${id}`)
  },

  /** 班次选课名单。教师只能看自己开的班，越权返回 404。 */
  roster(id: number): Promise<RosterVO> {
    return request<RosterVO>(`/offerings/${id}/roster`)
  },

  /** 课程主数据列表。 */
  courses(keyword?: string): Promise<{ items: CourseVO[] }> {
    return request<{ items: CourseVO[] }>('/courses', { query: { keyword } })
  },

  /** 新建学期（管理员）。 */
  createTerm(body: CreateTermPayload): Promise<TermVO> {
    return request<TermVO>('/admin/terms', { method: 'POST', body })
  },

  /**
   * 把某个学期设为当前学期（管理员）。
   *
   * 全库只能有一个当前学期，服务端会清掉其它的。
   */
  setCurrentTerm(termId: number): Promise<TermVO> {
    return request<TermVO>(`/admin/terms/${termId}/current`, { method: 'POST' })
  },

  /** 新建课程（管理员）。 */
  createCourse(body: CreateCoursePayload): Promise<CourseVO> {
    return request<CourseVO>('/admin/courses', { method: 'POST', body })
  },

  /** 开班（管理员）。 */
  createOffering(body: CreateOfferingPayload): Promise<OfferingVO> {
    return request<OfferingVO>('/admin/offerings', { method: 'POST', body })
  },

  /** 编辑课程（管理员）。只传要改的字段。 */
  patchCourse(courseId: number, body: UpdateCoursePayload): Promise<CourseVO> {
    return request<CourseVO>(`/admin/courses/${courseId}`, { method: 'PATCH', body })
  },

  /**
   * 编辑班次（管理员）。
   *
   * 传 slots 会整体替换时间段；容量不能改到比已选人数还小（后端会拒绝）。
   */
  patchOffering(offeringId: number, body: UpdateOfferingPayload): Promise<OfferingVO> {
    return request<OfferingVO>(`/admin/offerings/${offeringId}`, { method: 'PATCH', body })
  },

  /** 新建选课轮次（管理员）。 */
  createRound(body: CreateRoundPayload): Promise<RoundVO> {
    return request<RoundVO>('/admin/rounds', { method: 'POST', body })
  },

  // -------------------------------------------------------------------------
  // 教师端
  // -------------------------------------------------------------------------

  /**
   * 我开设的班次。
   *
   * 后端按 token 里的 userId 过滤，前端传不了也别传 teacher_id。
   */
  myOfferings(query: OfferingQuery): Promise<OfferingPage> {
    return request<OfferingPage>('/my/offerings', {
      query: {
        term_id: query.term_id,
        keyword: query.keyword,
        page: query.page,
        page_size: query.page_size,
      },
    })
  },

  /**
   * 一键导入课表。
   *
   * 每一行的 teacher_id 都由服务端强制设为当前登录用户 ——
   * 即使请求体里伪造也不生效，所以教师只能导自己的课。
   */
  importOfferings(body: {
    term_id: number
    rows: ImportRowPayload[]
  }): Promise<ImportResult> {
    return request<ImportResult>('/my/offerings/import', { method: 'POST', body })
  },
}
