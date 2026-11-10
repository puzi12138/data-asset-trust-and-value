/** Synthetic HTTP mocks verify UI behavior only; these are not research measurements. */
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { vi, describe, it, expect, afterEach } from 'vitest'
import TaskMeasurement from '../src/pages/TaskMeasurement.vue'
import { measurementApi } from '../src/selfService'

const ok = (data: unknown) => ({ ok: true, status: 200, json: async () => data })
const dataset = { dataset_id: 'SYNTHETIC_COMPONENT_FIXTURE', version: 'v1', content_hash: 'a'.repeat(64), row_count: 80, column_count: 4,
  columns: [{ name: 'label', dtype: 'string' }, { name: 'feature', dtype: 'float' }, { name: 'entity', dtype: 'string' }, { name: 'at', dtype: 'string' }], original_name: 'synthetic.csv' }
const options = { limits: {}, task_types: ['classification', 'regression'], learners: { classification: 'LogisticRegression C=1', regression: 'Ridge alpha=1' }, losses: {},
  default_protocol: { seed: 101, model_seeds: [101, 202, 303], n_bootstrap: 500, alpha: .05 }, datasets: [] }
const report = { status: 'EVALUATED', task: { task_type: 'classification', loss_unit: 'nats_per_record' }, input_binding: { content_hash: dataset.content_hash }, splits: {},
  marginal_utility: { validation: .02, test: -.03 },
  validation: { delta_u: .02, ci_lower: -.01, ci_upper: .04, base_loss_mean: .40, asset_loss_mean: .38, value_status: 'UNCERTAIN_VALUE_EVIDENCE', loss_name: 'log_loss', loss_unit: 'natural_log_units' },
  test: { delta_u: -.03, ci_lower: -.06, ci_upper: -.01 }, trust: { status: 'NEED_MORE_EVIDENCE', factual_truth_verified: false }, recommendation: { basis: 'validation_only' } }
const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.useRealTimers(); vi.unstubAllGlobals() })
function backend(finalReport: unknown = report) {
  return vi.fn(async (url: string, _init?: RequestInit) => {
    if (url.endsWith('/options')) return ok(options)
    if (url.includes('/datasets?')) return ok(dataset)
    if (url.endsWith('/runs')) return ok({ run_id: 'actual-server-id', status: 'QUEUED', poll_url: '/api/self-service/runs/actual-server-id' })
    return ok({ run_id: 'actual-server-id', status: 'COMPLETED', report: finalReport })
  })
}
async function setup(fetcher = backend()) { vi.stubGlobal('fetch', fetcher); const w = mount(TaskMeasurement); wrappers.push(w); await flushPromises(); return { w, fetcher } }
async function pick(w: VueWrapper, label: string, name = 'synthetic.csv') {
  const input = w.get(`[aria-label="${label}CSV"]`)
  Object.defineProperty(input.element, 'files', { value: [new File(['label,feature\nyes,1\nno,2'], name, { type: 'text/csv' })], configurable: true })
  await input.trigger('change')
}
async function candidate(w: VueWrapper) { await pick(w, '候选数据'); await w.get('[data-testid="upload-candidate"]').trigger('click'); await flushPromises() }
async function configured(w: VueWrapper) {
  await candidate(w)
  await w.get('[aria-label="任务测量目标列"]').setValue('label')
  await w.get('[aria-label="任务测量正类标签"]').setValue('yes')
  await w.get('[aria-label="确认模拟买方场景"]').setValue(true)
}
async function start(w: VueWrapper) { await w.get('[data-testid="start-measurement"]').trigger('click'); await flushPromises() }

describe('new CSV self-service measurement with synthetic HTTP mocks', () => {
  it('shows no result or assumed truth before an actual request', async () => {
    const { w, fetcher } = await setup()
    expect(w.find('[data-testid="measurement-run"]').exists()).toBe(false)
    expect(w.text()).toContain('两类任务量纲不同')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })
  it('rejects a missing candidate without creating a run', async () => {
    const { w, fetcher } = await setup(); await start(w)
    expect(w.get('[role="alert"]').text()).toContain('请先导入候选CSV')
    expect(fetcher.mock.calls.some(([url]) => url.endsWith('/runs'))).toBe(false)
  })
  it('uploads raw CSV using the local mutation marker and selected separator', async () => {
    const { w, fetcher } = await setup(); await w.get('[aria-label="任务测量CSV分隔符"]').setValue(';'); await candidate(w)
    const upload = fetcher.mock.calls.find(([url]) => url.includes('/datasets?'))!
    expect(upload[0]).toBe('/api/self-service/datasets?name=synthetic.csv&separator=%3B')
    expect(upload[1]?.headers).toMatchObject({ 'X-Research-Request': '1', 'Content-Type': 'text/csv; charset=utf-8' })
    expect(upload[1]?.body).toBeInstanceOf(File)
    expect(w.get('[data-testid="dataset-candidate"]').text()).toContain(dataset.content_hash)
  })
  it('requires explicit simulation consent and the exact positive label', async () => {
    const { w, fetcher } = await setup(); await candidate(w); await w.get('[aria-label="任务测量目标列"]').setValue('label'); await start(w)
    expect(w.get('[role="alert"]').text()).toContain('正类标签')
    await w.get('[aria-label="任务测量正类标签"]').setValue('yes'); await start(w)
    expect(w.get('[role="alert"]').text()).toContain('明确确认模拟买方')
    expect(fetcher.mock.calls.some(([url]) => url.endsWith('/runs'))).toBe(false)
  })
  it('binds actual imported ID/hash and fixed protocol in the run request', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const { w, fetcher } = await setup(); await configured(w)
    await w.get('[aria-label="排除ID列 entity"]').setValue(true); await start(w)
    const create = fetcher.mock.calls.find(([url]) => url.endsWith('/runs'))!
    expect(JSON.parse(String(create[1]?.body))).toEqual({ candidate_id: dataset.dataset_id, expected_candidate_hash: dataset.content_hash,
      buyer_id: null, validation_id: null, test_id: null, task: { target: 'label', task_type: 'classification', positive_label: 'yes', buyer_mode: 'SIMULATED_BUYER_SCENARIO',
        split_mode: 'record', entity_column: null, time_column: null, id_columns: ['entity'], seed: 101, model_seeds: [101, 202, 303], n_bootstrap: 500, alpha: .05, target_unit: 'target_units' } })
    expect(w.get('[data-testid="measurement-run-status"]').text()).toBe('QUEUED')
    expect(w.find('[data-testid="measurement-result-status"]').exists()).toBe(false)
    expect(w.get('[data-testid="start-measurement"]').element.closest('fieldset')?.disabled).toBe(true)
  })
  it('polls the saved run and preserves negative test utility, conditional interval and trust', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const { w, fetcher } = await setup(); await configured(w); await start(w); await vi.advanceTimersByTimeAsync(1000); await flushPromises()
    expect(fetcher.mock.calls.map(([url]) => url)).toContain('/api/self-service/runs/actual-server-id')
    expect(w.get('[data-testid="measurement-validation-utility"]').text()).toContain('0.020000；区间 [-0.010000, 0.040000]')
    expect(w.get('[data-testid="measurement-test-utility"]').text()).toContain('-0.030000')
    expect(w.get('[data-testid="measurement-trust"]').text()).toContain('NEED_MORE_EVIDENCE')
    expect(w.text()).toContain('不是无条件未来保证')
    expect(w.get('a[href$="report.md"]').attributes('href')).toBe('/api/self-service/runs/actual-server-id/report.md')
  })
  it('keeps an unmeasurable backend result without a fabricated utility', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const { w } = await setup(backend({ status: 'NOT_EVALUATED', reason: 'INSUFFICIENT_CLASS_SUPPORT', trust: { decision: 'NEED_MORE_EVIDENCE' } }))
    await configured(w); await start(w); await vi.advanceTimersByTimeAsync(1000); await flushPromises()
    expect(w.get('[data-testid="measurement-result-status"]').text()).toBe('NOT_EVALUATED')
    expect(w.get('[data-testid="measurement-not-evaluated"]').text()).toContain('不借用其他数据集')
    expect(w.get('[data-testid="measurement-validation-utility"]').text()).toBe('NOT_EVALUATED')
    expect(w.text()).toContain('INSUFFICIENT_CLASS_SUPPORT')
  })
  it('runs trust-only for unlabeled CSV with no training or borrowed task value', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const { w, fetcher } = await setup(backend({ status: 'NOT_EVALUATED', fit_count: 0, reasons: ['TASK_TYPE_NOT_SPECIFIED_OR_UNSUPPORTED', 'TARGET_NOT_SPECIFIED'], validation: {}, test: {}, trust: { status: 'NEED_MORE_EVIDENCE' } }))
    await candidate(w)
    await w.get('[data-testid="trust-only-measurement"]').trigger('click'); await flushPromises()
    const body = JSON.parse(String(fetcher.mock.calls.find(([url]) => url.endsWith('/runs'))![1]?.body))
    expect(body.task.target).toBeNull(); expect(body.task.task_type).toBeNull(); expect(body.buyer_id).toBeNull()
    await vi.advanceTimersByTimeAsync(1000); await flushPromises()
    expect(w.get('[data-testid="measurement-fit-count"]').text()).toBe('0')
    expect(w.get('[data-testid="measurement-result-status"]').text()).toBe('NOT_EVALUATED')
    expect(w.get('[data-testid="measurement-validation-utility"]').text()).toBe('NOT_EVALUATED')
    expect(w.get('[data-testid="measurement-reasons"]').text()).toContain('TARGET_NOT_SPECIFIED')
  })
  it('requires real D0 and a paired explicit validation/test upload', async () => {
    const { w } = await setup(); await configured(w); await w.get('[aria-label="任务测量买方模式"]').setValue('REAL_BUYER'); await start(w)
    expect(w.get('[role="alert"]').text()).toContain('买方已有数据D0')
    await pick(w, '买方已有数据 D0'); await w.get('[data-testid="upload-buyer"]').trigger('click'); await flushPromises()
    await w.get('[aria-label="使用独立验证测试CSV"]').setValue(true); await start(w)
    expect(w.get('[role="alert"]').text()).toContain('同时导入验证和测试')
  })
  it('requires entity/time columns and labels record-only independence UNKNOWN', async () => {
    const { w } = await setup(); await configured(w)
    expect(w.get('[data-testid="measurement-independence-warning"]').text()).toContain('UNKNOWN')
    await w.get('[aria-label="任务测量划分方式"]').setValue('entity'); await start(w)
    expect(w.get('[role="alert"]').text()).toContain('实体列')
    await w.get('[aria-label="任务测量划分方式"]').setValue('time'); await start(w)
    expect(w.get('[role="alert"]').text()).toContain('时间列')
  })
  it('switches regression loss/model without comparing monetary values', async () => {
    const { w } = await setup(); await candidate(w); await w.get('[aria-label="任务测量类型"]').setValue('regression')
    expect((w.get('[aria-label="任务测量固定学习器"]').element as HTMLInputElement).value).toBe('Ridge alpha=1')
    expect((w.get('[aria-label="任务测量损失函数"]').element as HTMLInputElement).value).toContain('MAE')
    expect(w.find('[aria-label="任务测量正类标签"]').exists()).toBe(false)
  })
  it('invalid seed lists never reach the run API', async () => {
    const { w, fetcher } = await setup(); await configured(w); await w.get('[aria-label="任务测量模型种子"]').setValue('101,101'); await start(w)
    expect(w.get('[role="alert"]').text()).toContain('互不重复')
    expect(fetcher.mock.calls.some(([url]) => url.endsWith('/runs'))).toBe(false)
  })
  it('preserves backend conflict without replacing it with a score', async () => {
    const f = backend(); f.mockImplementation(async (url: string) => url.endsWith('/options') ? ok(options) : url.includes('/datasets?') ? ok(dataset) : { ok: false, status: 409, json: async () => ({ detail: 'CANDIDATE_BYTES_CHANGED' }) })
    const { w } = await setup(f); await configured(w); await start(w)
    expect(w.get('[role="alert"]').text()).toContain('CANDIDATE_BYTES_CHANGED')
    expect(w.find('[data-testid="measurement-run"]').exists()).toBe(false)
  })
  it('exposes worker failure as failure and stops polling', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const f = backend(); f.mockImplementation(async (url: string) => url.endsWith('/options') ? ok(options) : url.includes('/datasets?') ? ok(dataset) : url.endsWith('/runs') ? ok({ run_id: 'failed-id', status: 'QUEUED' }) : ok({ run_id: 'failed-id', status: 'FAILED', error: { code: 'WORKER_FAILED', message: '原错误保留' } }))
    const { w } = await setup(f); await configured(w); await start(w); await vi.advanceTimersByTimeAsync(1000); await flushPromises()
    expect(w.get('[data-testid="measurement-run-status"]').text()).toBe('FAILED')
    expect(w.get('[role="alert"]').text()).toContain('WORKER_FAILED')
    expect(vi.getTimerCount()).toBe(0)
  })
  it('changing inputs clears a saved result and selecting new file clears its old hash', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    const { w } = await setup(); await configured(w); await start(w); await vi.advanceTimersByTimeAsync(1000); await flushPromises()
    await w.get('[aria-label="任务测量划分种子"]').setValue('202')
    expect(w.find('[data-testid="measurement-run"]').exists()).toBe(false)
    await pick(w, '候选数据', 'new.csv')
    expect(w.find('[data-testid="dataset-candidate"]').exists()).toBe(false)
    expect(w.find('[aria-label="任务测量目标列"]').exists()).toBe(false)
  })
})

describe('CSV upload API errors', () => {
  it('does not turn a network error or non-JSON response into an imported dataset', async () => {
    const file = new File(['x'], 'test.csv')
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline') }))
    await expect(measurementApi.upload(file, ',')).rejects.toThrow('无法连接本地服务')
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 502, json: async () => { throw new Error('html') } })))
    await expect(measurementApi.upload(file, ',')).rejects.toMatchObject({ status: 502 })
  })
})
