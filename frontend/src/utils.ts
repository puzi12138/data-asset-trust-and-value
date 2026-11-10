import type { Row } from './api'
export const origins = [
  { value: 'local_operational', label: '本地正常试交付', note: '真实本机 HTTP 观测；不代表商业违约样本' },
  { value: 'fault_injection', label: '人为故障注入', note: '预定义研究故障；不得混入自然履约统计' },
  { value: 'simulated', label: '模拟履约', note: '模拟履约，与本地正常交付严格分账' },
]
export const methods = [ { value: 'beta', label: 'Beta–Bernoulli' }, { value: 'decay30', label: '时间衰减 · 30 天' }, { value: 'decay90', label: '时间衰减 · 90 天' }, { value: 'hierarchy', label: '提供方分层模型' }, { value: 'decay_hier30', label: '分层 + 30 天衰减' }, { value: 'decay_hier90', label: '分层 + 90 天衰减' } ]
export function number(v: unknown, digits = 3): string { return typeof v === 'number' && Number.isFinite(v) ? v.toLocaleString('zh-CN', { maximumFractionDigits: digits, minimumFractionDigits: digits }) : '—' }
export function date(v: unknown): string { if (typeof v !== 'string' || !v) return '—'; const d = new Date(v); return Number.isNaN(d.getTime()) ? v : d.toLocaleString('zh-CN', { hour12: false }) }
export function short(v: unknown, n = 20): string { const s = String(v ?? '—'); return s.length > n ? `${s.slice(0, n)}…` : s }
export function iso(local: string): string | undefined { return local ? new Date(local).toISOString() : undefined }
export function valueBody(selection: Row, task: string, economics: string, at = '', origin = 'local_operational', method = 'decay30'): Row { return { ...selection, task_id: task, economics_id: economics, at: iso(at), origin, method } }
export function raw(value: unknown): string { return JSON.stringify(value, null, 2) }
export function reasonLabel(value: string): string {
  const known: Record<string, string> = { NO_EXACT_FROZEN_TASK_ASSET_VERSION_CONTENT_HASH_ECONOMICS_MATCH: '该资产 ID、版本、哈希、任务与经济场景无精确效用测量', TASK_MEASUREMENT_NOT_YET_LOCALLY_AVAILABLE: '该评估时点尚无可用任务测量', LOCAL_DELIVERY_HISTORY_INSUFFICIENT: '本地有效合同历史不足', WIDE_UNCALIBRATED_CREDIT_INTERVAL: '履约信用模型条件区间较宽', FIXED_Q_BOOTSTRAP_VALUE_CROSSES_ZERO: '固定 q 的任务效用区间跨越零', NONPOSITIVE_VALUE: '预期净价值非正', NONPOSITIVE_BENEFIT_SUCCESS: '成功交付条件收益非正', EVIDENCE_NOT_VERIFIED: '当前有效验收证据不足', POLICY_NOT_VERIFIED: '授权或资产状态不满足' }
  return known[value] ?? value
}
