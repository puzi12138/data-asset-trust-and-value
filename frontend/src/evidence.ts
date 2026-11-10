import { request, type Row } from './api'

export interface EvidencePlanResponse { plan_id: string; plan_hash: string; plan: Row }
export interface EvidenceReportResponse { plan_id: string; report_hash: string; report: Row }
export interface JointReportResponse { joint_id: string; report: Row }
export const evidenceApi = {
  plan: (body: Row) => request<EvidencePlanResponse>('/evidence/plans', body),
  evaluate: (id: string) => request<EvidenceReportResponse>(`/evidence/plans/${encodeURIComponent(id)}/evaluate`, {}),
  result: (id: string) => request<EvidenceReportResponse>(`/evidence/plans/${encodeURIComponent(id)}/result`),
  joint: (body: Row) => request<JointReportResponse>('/evidence/joint', body),
  reportUrl: (id: string, format: 'md' | 'json') => `/api/evidence/plans/${encodeURIComponent(id)}/report?format=${format}`,
  jointUrl: (id: string, format: 'md' | 'json') => `/api/evidence/joint/${encodeURIComponent(id)}/report?format=${format}`,
}

/** Display names preserve the saved status and never change a backend decision. */
export function valueStateText(status: unknown): string {
  const labels: Record<string, string> = {
    VALUE_POSITIVE: '正价值证据：在当前任务与条件下支持损失降低',
    POSITIVE_VALUE_EVIDENCE: '正价值证据：在当前任务与条件下支持损失降低',
    VALUE_NEGATIVE: '负价值证据：在当前任务与条件下支持损失增加',
    NEGATIVE_VALUE_EVIDENCE: '负价值证据：在当前任务与条件下支持损失增加',
    VALUE_UNCERTAIN: '当前证据不足，暂缓使用或补充评价',
    UNCERTAIN_VALUE_EVIDENCE: '当前证据不足，暂缓使用或补充评价',
    MODEL_UNRELIABLE: '模型不可靠：当前学习器未通过预定可靠性要求',
    TASK_MODEL_UNRELIABLE: '模型不可靠：当前学习器未通过预定可靠性要求',
    VALUE_MODEL_UNRELIABLE: '模型不可靠：当前学习器未通过预定可靠性要求',
    NOT_EVALUATED: '未测量：当前缺少有效的任务测量',
    VALUE_NOT_EVALUATED: '未测量：当前缺少有效的任务测量',
    NOT_EVALUATED_FOR_TEMPORAL_DEPENDENCE: '未测量区间：时间依赖条件尚不支持当前抽样推断',
  }
  return labels[String(status)] ?? '状态含义请核对后端原始报告'
}
