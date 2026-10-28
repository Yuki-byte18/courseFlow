import { request } from '../request/client'
import type {
  EnrollVO,
  MyEnrollmentVO,
  RoundVO,
  ScheduleVO,
  StudentOfferingPayload,
  UpdateRoundPayload,
} from './types'

/**
 * 选课域接口。
 *
 * 对应后端 internal/enrollment/controller.go：
 *   GET    /my/round                当前轮次 + 已用学分
 *   POST   /my/enrollments          抢课
 *   DELETE /my/enrollments/:id      退课
 *   GET    /my/enrollments          我的选课
 *   GET    /my/schedule             我的课表
 *   GET    /admin/rounds            轮次列表
 *
 * 注意所有学生端路径都是 /my 前缀：身份从 token 推导，
 * 前端不传、也传不了 student_id。
 */
export const enrollmentApi = {
  /** 当前开放中的轮次；没开放时 open=false 且 reason 说明原因。 */
  currentRound(): Promise<RoundVO> {
    return request<RoundVO>('/my/round')
  },

  /** 抢课。 */
  enroll(offeringId: number): Promise<EnrollVO> {
    return request<EnrollVO>('/my/enrollments', {
      method: 'POST',
      body: { offering_id: offeringId },
    })
  },

  /** 退课。成功返回 204，没有响应体。 */
  drop(offeringId: number): Promise<void> {
    return request<void>(`/my/enrollments/${offeringId}`, { method: 'DELETE' })
  },

  /** 我的选课。 */
  myEnrollments(query?: { term_id?: number }): Promise<{ items: MyEnrollmentVO[] }> {
    return request<{ items: MyEnrollmentVO[] }>('/my/enrollments', {
      query: { term_id: query?.term_id },
    })
  },

  /** 我的课表（按星期几分组）。 */
  schedule(termId?: number): Promise<ScheduleVO> {
    return request<ScheduleVO>('/my/schedule', { query: { term_id: termId } })
  },

  /** 轮次列表（管理员）。 */
  rounds(termId?: number): Promise<{ items: RoundVO[] }> {
    return request<{ items: RoundVO[] }>('/admin/rounds', {
      query: { term_id: termId },
    })
  },

  /** 编辑轮次（管理员）。只传要改的字段。 */
  patchRound(roundId: number, body: UpdateRoundPayload): Promise<RoundVO> {
    return request<RoundVO>(`/admin/rounds/${roundId}`, { method: 'PATCH', body })
  },

  /** 强制退课（管理员）。学生退不了时的兜底。 */
  forceDrop(body: StudentOfferingPayload): Promise<void> {
    return request<void>('/admin/enrollments/force-drop', { method: 'POST', body })
  },

  /**
   * 补录（管理员）。
   *
   * 绕过轮次时间窗、学分上限、年级限制，但**不绕过**容量与时间冲突。
   */
  supplement(body: StudentOfferingPayload): Promise<EnrollVO> {
    return request<EnrollVO>('/admin/enrollments/supplement', { method: 'POST', body })
  },
}
