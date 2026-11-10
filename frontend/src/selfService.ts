import { ApiError, request, type Row } from './api'

export interface MeasurementDataset {
  dataset_id: string
  version: string
  content_hash: string
  row_count: number
  column_count: number
  columns: { name: string; dtype: string }[]
  trust?: Row
  original_name?: string
}
export interface MeasurementOptions {
  limits: Row
  task_types: string[]
  learners: Record<string, string>
  losses: Row
  default_protocol: { seed: number; model_seeds: number[]; n_bootstrap: number; alpha: number }
  datasets: MeasurementDataset[]
}
export interface MeasurementRun {
  run_id: string
  status: 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED'
  error?: { code: string; message: string }
  report?: Row
}

export const measurementApi = {
  options: () => request<MeasurementOptions>('/self-service/options'),
  async upload(file: File, separator: string): Promise<MeasurementDataset> {
    let response: Response
    try {
      response = await fetch(`/api/self-service/datasets?${new URLSearchParams({ name: file.name, separator })}`, {
        method: 'POST', headers: { Accept: 'application/json', 'Content-Type': 'text/csv; charset=utf-8', 'X-Research-Request': '1' }, body: file,
      })
    } catch { throw new ApiError(0, '无法连接本地服务，请检查启动窗口。') }
    let data: Row
    try { data = await response.json() } catch { throw new ApiError(response.status, 'CSV导入服务未返回有效JSON。') }
    if (!response.ok) {
      const detail = data.detail ?? data.error ?? 'CSV导入失败'
      throw new ApiError(response.status, typeof detail === 'string' ? detail : JSON.stringify(detail), data)
    }
    return data as MeasurementDataset
  },
  create: (body: Row) => request<MeasurementRun>('/self-service/runs', body),
  run: (id: string) => request<MeasurementRun>(`/self-service/runs/${encodeURIComponent(id)}`),
  reportUrl: (id: string, format: 'json' | 'md') => `/api/self-service/runs/${encodeURIComponent(id)}/report.${format}`,
}
