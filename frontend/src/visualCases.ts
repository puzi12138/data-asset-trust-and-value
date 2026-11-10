import { request, query, type Row } from './api'

export interface CaseAsset { asset_id: string; asset_version: string; asset_sha256: string }
export interface EvidenceCase extends Row { case_id: string }
export interface LocalFile { filename: string; sha256: string; data_base64: string; size: number }
export const uploadLimits = { csv: 2 * 1024 * 1024, image: 5 * 1024 * 1024, images: 4 }

export function caseAsset(value: Row): CaseAsset {
  const asset = value.asset ?? value
  return { asset_id: asset.asset_id, asset_version: asset.asset_version, asset_sha256: asset.asset_sha256 }
}
export function checkedCase(value: EvidenceCase, expected?: CaseAsset): EvidenceCase {
  const asset = caseAsset(value)
  if (!value.case_id || !asset.asset_id || !asset.asset_version || !/^[a-f0-9]{64}$/i.test(asset.asset_sha256 ?? '')) throw new Error('案例响应缺少精确版本或SHA-256，未展示不完整证据。')
  if (expected && Object.keys(expected).some(k => asset[k as keyof CaseAsset] !== expected[k as keyof CaseAsset])) throw new Error('案例返回了不同数据版本，已阻止旧证据和旧结论复用。')
  return value
}
export async function fileData(file: File, kind: 'csv' | 'image'): Promise<LocalFile> {
  const limit = kind === 'csv' ? uploadLimits.csv : uploadLimits.image
  if (!file.size || file.size > limit) throw new Error(kind === 'csv' ? 'CSV须非空且不超过2MiB。' : '单图须非空且不超过5MiB。')
  if (kind === 'csv' ? !/\.csv$/i.test(file.name) : !/\.(png|jpe?g)$/i.test(file.name)) throw new Error(kind === 'csv' ? '请选择CSV文件。' : '只接受PNG或JPEG原始图像。')
  const bytes = new Uint8Array(await file.arrayBuffer())
  const digest = await crypto.subtle.digest('SHA-256', bytes)
  let binary = ''; for (let offset = 0; offset < bytes.length; offset += 32768) binary += String.fromCharCode(...bytes.subarray(offset, offset + 32768))
  return { filename: file.name, size: bytes.length, sha256: Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join(''), data_base64: btoa(binary) }
}
export function readable(value: unknown): string {
  if (value === undefined || value === null || value === '') return 'UNKNOWN'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}
export function publicError(error: unknown): string { return error instanceof Error ? error.message : String(error) }
export const casesApi = {
  capabilities: () => request('/visual-cases/capabilities'),
  list: () => request<{ cases: EvidenceCase[] }>('/visual-cases'),
  create: async (payload: Row) => checkedCase(await request<EvidenceCase>('/visual-cases', payload)),
  get: async (id: string, asset?: CaseAsset) => checkedCase(await request<EvidenceCase>(`/visual-cases/${encodeURIComponent(id)}${query(asset ? { ...asset } : {})}`), asset),
  validate: (id: string, evidenceId: string) => request(`/visual-cases/${encodeURIComponent(id)}/validate`, { evidence_id: evidenceId }),
  execute: (id: string, body: Row) => request(`/visual-cases/${encodeURIComponent(id)}/execute`, body),
  evidenceUrl: (id: string, evidenceId: string) => `/api/visual-cases/${encodeURIComponent(id)}/evidence/${encodeURIComponent(evidenceId)}`,
  reportUrl: (id: string, format: 'md' | 'json') => `/api/visual-cases/${encodeURIComponent(id)}/report${query({ format })}`,
}
