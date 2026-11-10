import { request, query, type Row } from './api'

/** A binding proves identity/protocol continuity, not factual truth or a market price. */
export interface JointEnvelope extends Row { joint_id: string; report?: Row }
export interface BindingItem { path: string; value: string }
export function jointReport(value: JointEnvelope): Row { return value.report ?? value }
export function checkedJoint(value: JointEnvelope, expected?: { joint_id?: string; case_id?: string; value_run_id?: string | null }): JointEnvelope {
  const report = jointReport(value)
  if (!value.joint_id || !['SAME_ASSET_BOUND', 'BINDING_MISMATCH', 'NOT_EVALUATED'].includes(report.status)) throw new Error('联合报告缺少有效ID或绑定状态，未展示为绑定成功。')
  if (report.status === 'SAME_ASSET_BOUND' && (!report.binding?.asset?.asset_id || !report.binding?.asset?.asset_version || !/^[a-f0-9]{64}$/i.test(report.binding?.asset?.asset_sha256 ?? '') || report.task_value?.status !== 'EVALUATED')) throw new Error('联合报告缺少完整资产版本或任务测量，未展示为绑定成功。')
  if (expected && Object.entries(expected).some(([key, item]) => value[key] !== item)) throw new Error('服务返回了其他案例或任务运行的联合报告，已阻止错配展示。')
  return value
}
export function bindingItems(value: unknown, prefix = ''): BindingItem[] {
  if (value && typeof value === 'object' && !Array.isArray(value)) return Object.entries(value).flatMap(([key, item]) => bindingItems(item, prefix ? `${prefix}.${key}` : key))
  return [{ path: prefix, value: value === null || value === undefined || value === '' ? 'UNKNOWN' : typeof value === 'object' ? JSON.stringify(value) : String(value) }]
}
export function valueExplanation(value: unknown): string {
  const meanings: Record<string, string> = {
    POSITIVE_VALUE_EVIDENCE: '已有正任务价值证据；仅适用于当前任务和评估协议。',
    NEGATIVE_VALUE_EVIDENCE: '已有负任务价值证据，保留负值及不使用选项。',
    POSITIVE_VALUE: '已有正任务价值证据；仅适用于当前任务和评估协议。',
    NEGATIVE_VALUE: '已有负任务价值证据，保留负值及不使用选项。',
    VALUE_POSITIVE: '已有正任务价值证据；仅适用于当前任务和评估协议。',
    VALUE_NEGATIVE: '已有负任务价值证据，保留负值及不使用选项。',
    VALUE_UNCERTAIN: '当前证据不足，暂缓使用或补充评价；不表示已经证明数据无价值。',
    MODEL_UNRELIABLE: '任务模型可靠性不足，当前测量不支持正式使用建议。',
    TASK_MODEL_UNRELIABLE: '任务模型可靠性不足，当前测量不支持正式使用建议。',
    NOT_EVALUATED: '尚无本精确版本的合法任务测量；不借用其他资产价值。',
  }
  return meanings[String(value)] ?? '按已保存的任务结果及其条件解释；任务效用不等于市场价格。'
}
export const jointApi = {
  capabilities: () => request('/same-asset-joint/capabilities'),
  list: () => request('/same-asset-joint'),
  create: async (caseId: string, valueRunId: string | null) => checkedJoint(await request<JointEnvelope>('/same-asset-joint', { case_id: caseId, value_run_id: valueRunId }), { case_id: caseId, value_run_id: valueRunId }),
  get: async (id: string) => checkedJoint(await request<JointEnvelope>(`/same-asset-joint/${encodeURIComponent(id)}${query({ format: 'json' })}`), { joint_id: id }),
  reportUrl: (id: string, format: 'json' | 'md') => `/api/same-asset-joint/${encodeURIComponent(id)}${query({ format })}`,
}
