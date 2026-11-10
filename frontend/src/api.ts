/** The browser carries no role keys. Mutations are signed by local backend actors. */
export type Row = Record<string, any>
export interface Asset extends Row { asset_id: string; provider_id: string; version: string; name?: string; state?: string; row_count?: number; column_count?: number; content_hash?: string }
export interface Selection { asset_id: string; version: string }
export interface CreditResult extends Row { q: number; q_lower: number; q_upper: number; n_history: number; successes: number; failures: number; origin: string; method: string; insufficient_history?: boolean }
export interface Valuation extends Row { status: string; result?: Row | null; reason?: string; report_id?: string }

export class ApiError extends Error {
  constructor(public status: number, message: string, public details?: unknown) { super(message); this.name = 'ApiError' }
}
export async function request<T = Row>(path: string, body?: unknown): Promise<T> {
  let response: Response
  try {
    response = await fetch(`/api${path}`, { method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? { Accept: 'application/json' } : { Accept: 'application/json', 'Content-Type': 'application/json', 'X-Research-Request': '1' },
      body: body === undefined ? undefined : JSON.stringify(body), cache: 'no-store' })
  } catch { throw new ApiError(0, '无法连接本地服务。请检查启动窗口和节点健康状态。') }
  let data: Row
  try { data = await response.json() } catch { throw new ApiError(response.status, '服务未返回有效 JSON；请检查本地网关配置。') }
  if (!response.ok) {
    const detail = data.detail ?? data.error ?? data.message ?? '请求失败'
    throw new ApiError(response.status, typeof detail === 'string' ? detail : JSON.stringify(detail), data)
  }
  return data as T
}
export function query(values: Record<string, unknown>): string {
  const params = new URLSearchParams()
  Object.entries(values).forEach(([key, value]) => { if (value !== undefined && value !== null && value !== '') params.set(key, String(value)) })
  return params.size ? `?${params}` : ''
}
export const api = {
  dashboard: async () => { const r = await request('/dashboard'); return { ...r, recent_reports: (r.recent_reports ?? []).map(flattenReport) } },
  assets: async (): Promise<Asset[]> => { const r = await request('/assets'); return r.assets ?? [] },
  detail: (s: Selection) => request(`/assets/${encodeURIComponent(s.asset_id)}${query({ version: s.version })}`),
  tasks: () => request('/tasks'),
  contracts: () => request('/contracts'),
  credit: (s: Selection, filters: Row) => request<CreditResult>(`/credit/${encodeURIComponent(s.asset_id)}${query({ version: s.version, ...filters })}`),
  series: (s: Selection, filters: Row) => request(`/credit-series/${encodeURIComponent(s.asset_id)}${query({ version: s.version, ...filters })}`),
  value: (body: Row) => request<Valuation>('/value', body),
  compare: (body: Row) => request('/compare', body),
  audit: (filters: Row) => request(`/audit${query(filters)}`),
  reports: async () => { const r = await request('/reports'); return { ...r, reports: (r.reports ?? []).map(flattenReport) } },
}
export function flattenReport(row: Row): Row {
  const payload = row.report ?? row
  const at = payload.as_of_time ?? payload.reports?.[0]?.as_of_time
  return { ...payload, ...row, task_id: payload.task_id ?? payload.reports?.[0]?.task_id,
    as_of_time: at, created_at: row.created_at ?? at }
}
