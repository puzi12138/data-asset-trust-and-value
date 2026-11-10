import { request, query, type Row } from './api'

/** Identity checks only. Counts, intervals and decisions remain server results. */
export interface DiCoverage extends Row { joint_id: string; status: string; audit_summary?: Row | null }
export interface DiRows extends Row { rows: Row[]; total: number; offset: number; limit: number }
export function checkedCoverage(value: DiCoverage, jointId: string): DiCoverage {
  if (!value || value.joint_id !== jointId || !['SAME_ASSET_BOUND', 'NOT_EVALUATED', 'BINDING_MISMATCH'].includes(value.status)) throw new Error('覆盖报告与当前联合报告不匹配，已阻止错版本展示。')
  if (value.status !== 'NOT_EVALUATED' && value.status !== 'BINDING_MISMATCH' && (!value.binding || !value.audit_summary)) throw new Error('覆盖报告缺少版本绑定或审计统计，不能展示为已核验。')
  return value
}
export const coverageLabels: Record<string, string> = {
  population_size: '实际参与估值的Di记录数',
  reference_available_records: '有公开参考证据的记录',
  audited_records: '实际核查记录（去重）',
  match_records: '核查符合 MATCH',
  mismatch_records: '发现分歧 MISMATCH',
  incomparable_records: '不可比较 INCOMPARABLE',
  disputed_records: '待裁定 DISPUTED',
  not_audited_records: '尚未核验记录',
  ai_visual_reviewed_di_records: 'AI视觉已审阅的Di记录',
  independent_fact_verified_records: '独立现实事实已核实的记录',
}
export const coverageApi = {
  capabilities: () => request('/di-evidence/capabilities'),
  get: async (jointId: string) => checkedCoverage(await request<DiCoverage>(`/di-evidence/${encodeURIComponent(jointId)}?format=json`), jointId),
  rows: (jointId: string, offset = 0, limit = 100, outcome = '') => request<DiRows>(`/di-evidence/${encodeURIComponent(jointId)}/rows${query({ offset, limit, outcome })}`),
  row: (jointId: string, candidateRow: number) => request(`/di-evidence/${encodeURIComponent(jointId)}/rows/${encodeURIComponent(String(candidateRow))}`),
  reportUrl: (jointId: string, format: 'json' | 'md') => `/api/di-evidence/${encodeURIComponent(jointId)}${query({ format })}`,
}
