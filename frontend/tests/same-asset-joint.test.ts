/** SYNTHETIC_UI_ONLY. No training, physical observation, audit, or model call occurs in this file. */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import SameAssetJoint from '../src/pages/SameAssetJoint.vue'
import { bindingItems, checkedJoint, jointApi, valueExplanation } from '../src/sameAssetJoint'
import type { Row } from '../src/api'

const asset = { asset_id: 'SYNTHETIC_UI_ASSET', asset_version: 'v1', asset_sha256: 'a'.repeat(64) }
const caseValue = { case_id: 'case_synthetic', asset }
const runValue = { run_id: 'value_synthetic', status: 'COMPLETED', candidate: { dataset_id: asset.asset_id, version: asset.asset_version, content_hash: asset.asset_sha256 } }
const report = { joint_id: 'joint_synthetic', case_id: caseValue.case_id, value_run_id: runValue.run_id, status: 'SAME_ASSET_BOUND', evaluated_at: '2026-10-25T00:00:00Z', result_sha256: 'b'.repeat(64),
  binding: { asset, task_id: 'SYNTHETIC_TASK', target_column: 'label', buyer_d0: { version: 'v1', sha256: 'c'.repeat(64) }, loss: 'log_loss', learner: { name: 'logistic', preprocessing: 'fit_on_training_only' } },
  trust: { fact_status: 'UNKNOWN', assessment: { decision: 'NEED_MORE_EVIDENCE' }, source_authentication: 'NOT_ESTABLISHED_BY_JOINT_BINDING', coordination_state: 'ADDITIONAL_EVIDENCE_REQUIRED', evidence_count: 1, review_count: 2, additional_physical_observations_from_joint_binding: 0 },
  task_value: { status: 'EVALUATED', value_status: 'VALUE_UNCERTAIN', validation: { delta_utility_mean: -.01, ci_lower: -.1, ci_upper: .1, loss_unit: 'natural_log_units' }, test: { delta_utility_mean: .02 }, recommendation: { action: 'do_not_use', uses_test: false }, buyer_mode: 'SIMULATED_BUYER_SCENARIO', market_price: 'NOT_EVALUATED', trust_multiplier_applied: false },
  recommendation: 'SYNTHETIC research recommendation; factual use requires more evidence.', issues: [] as Row[] }
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body })
const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals() })
function backend(result: Row = structuredClone(report)) {
  return vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/same-asset-joint/capabilities') return response({ status: 'AVAILABLE', value_runs: [runValue], expected_binding_fields: ['asset', 'task'] })
    if (url === '/api/visual-cases') return response({ cases: [caseValue] })
    if (url === '/api/same-asset-joint') return response(options?.method === 'POST' ? result : { reports: [{ joint_id: result.joint_id, status: result.status }] })
    if (url === '/api/same-asset-joint/joint_synthetic?format=json') return response(result)
    throw new Error('Unexpected SYNTHETIC UI URL ' + url)
  })
}
async function setup(fetcher = backend()) {
  vi.stubGlobal('fetch', fetcher)
  const w = mount(SameAssetJoint, { global: { stubs: { RouterLink: { template: '<a><slot/></a>' } } } }); wrappers.push(w)
  await flushPromises(); return { w, fetcher }
}
async function select(w: VueWrapper, run = runValue.run_id) {
  await w.get('[aria-label="联合评估视觉案例ID"]').setValue(caseValue.case_id)
  await w.get('[aria-label="联合评估价值运行ID"]').setValue(run)
}
async function submit(w: VueWrapper) { await w.get('[data-testid="joint-form"]').trigger('submit'); await flushPromises() }

describe('same asset joint UI uses actual API contract', () => {
  it('loads real capabilities, visual registry and saved reports without inventing an evaluation', async () => {
    const { w, fetcher } = await setup()
    expect(fetcher.mock.calls.map(c => c[0])).toEqual(['/api/same-asset-joint/capabilities', '/api/visual-cases', '/api/same-asset-joint'])
    expect(w.find('[data-testid="joint-result"]').exists()).toBe(false)
    expect(w.text()).toContain('不自动训练，也不发起模型调用')
    expect(w.find('input[type=password]').exists()).toBe(false)
  })
  it('shows precise candidate and task hashes before requesting a merge', async () => {
    const { w } = await setup(); await select(w)
    expect(w.get('[data-testid="joint-case-preview"]').text()).toContain(asset.asset_sha256)
    expect(w.get('[data-testid="joint-value-preview"]').text()).toContain(runValue.candidate.content_hash)
  })
  it('sends only saved IDs and mutation marker, without trusting browser result fields or paths', async () => {
    const { w, fetcher } = await setup(); await select(w); await submit(w)
    const call = fetcher.mock.calls.find(c => c[0] === '/api/same-asset-joint' && c[1]?.method === 'POST')!
    expect(JSON.parse(String(call[1]!.body))).toEqual({ case_id: caseValue.case_id, value_run_id: runValue.run_id })
    expect(call[1]!.headers).toMatchObject({ 'X-Research-Request': '1' })
    expect(w.get('[data-testid="joint-status"]').text()).toBe('SAME_ASSET_BOUND')
  })
  it('preserves unknown truth, zero added physical observations and separate uncertain value', async () => {
    const { w } = await setup(); await select(w); await submit(w)
    expect(w.get('[data-testid="joint-fact-status"]').text()).toBe('UNKNOWN')
    expect(w.get('[data-testid="joint-trust"]').text()).toContain('NOT_ESTABLISHED_BY_JOINT_BINDING')
    expect(w.get('[data-testid="joint-value-state"]').text()).toBe('VALUE_UNCERTAIN')
    expect(w.get('[data-testid="joint-value-explanation"]').text()).toContain('不表示已经证明数据无价值')
    expect(w.get('[data-testid="joint-value"]').text()).toContain('do_not_use')
    expect(w.text()).toContain('重复审阅不增加独立物理样本')
    expect(w.text()).toContain('natural_log_units')
  })
  it('shows all verified binding fields from backend rather than matching asset names', async () => {
    const { w } = await setup(); await select(w); await submit(w)
    const text = w.get('[data-testid="joint-binding"]').text()
    for (const field of ['asset.asset_id', 'asset.asset_version', 'asset.asset_sha256', 'buyer_d0.sha256', 'target_column', 'learner.preprocessing']) expect(text).toContain(field)
    expect(w.get('[data-testid="joint-result-hash"]').text()).toBe(report.result_sha256)
  })
  it('exports exact saved report ID in Chinese Markdown and JSON', async () => {
    const { w } = await setup(); await select(w); await submit(w)
    expect(w.get('[data-testid="joint-export-md"]').attributes('href')).toBe('/api/same-asset-joint/joint_synthetic?format=md')
    expect(w.get('[data-testid="joint-export-json"]').attributes('href')).toBe('/api/same-asset-joint/joint_synthetic?format=json')
  })
  it('blocks empty case input without a mutation', async () => {
    const { w, fetcher } = await setup(); await submit(w)
    expect(w.get('[data-testid="joint-error"]').text()).toContain('请选择或填写视觉案例ID')
    expect(fetcher.mock.calls.some(c => c[1]?.method === 'POST')).toBe(false)
  })
  it('sends a missing value run as null and preserves NOT_EVALUATED without borrowing utility', async () => {
    const result = { ...structuredClone(report), status: 'NOT_EVALUATED', value_run_id: null, task_value: { status: 'NOT_EVALUATED', reason: 'NO_VALUE_RUN_FOR_EXACT_ASSET' } }
    const { w, fetcher } = await setup(backend(result)); await select(w, ''); await submit(w)
    expect(w.get('[data-testid="joint-status"]').text()).toBe('NOT_EVALUATED')
    expect(w.get('[data-testid="joint-value-state"]').text()).toBe('NOT_EVALUATED')
    expect(w.text()).toContain('NO_VALUE_RUN_FOR_EXACT_ASSET')
    expect(JSON.parse(String(fetcher.mock.calls.find(c => c[1]?.method === 'POST')![1]!.body))).toMatchObject({ value_run_id: null })
  })
  it.each(['ASSET_ID_MISMATCH', 'ASSET_VERSION_MISMATCH', 'ASSET_SHA256_MISMATCH', 'EVIDENCE_PROTOCOL_HASH_MISMATCH'])('shows %s as refusal, never a positive joint result', async code => {
    const fetcher = backend(), underlying = fetcher.getMockImplementation()!
    fetcher.mockImplementation((url, options) => url === '/api/same-asset-joint' && options?.method === 'POST' ? Promise.resolve(response({ detail: { status: 'BINDING_MISMATCH', issues: [{ code, path: 'binding.asset' }] } }, 409)) : underlying(url, options))
    const { w } = await setup(fetcher); await select(w); await submit(w)
    expect(w.get('[data-testid="joint-error"]').text()).toContain('BINDING_MISMATCH')
    expect(w.get('[data-testid="joint-issues"]').text()).toContain(code)
    expect(w.find('[data-testid="joint-result"]').exists()).toBe(false)
  })
  it('clears a previous successful report when inputs change or the next merge is refused', async () => {
    const fetcher = backend(), underlying = fetcher.getMockImplementation()!
    const { w } = await setup(fetcher); await select(w); await submit(w)
    expect(w.find('[data-testid="joint-result"]').exists()).toBe(true)
    await w.get('[aria-label="联合评估价值运行ID"]').setValue('other_asset_run')
    expect(w.find('[data-testid="joint-result"]').exists()).toBe(false)
    fetcher.mockImplementation((url, options) => url === '/api/same-asset-joint' && options?.method === 'POST' ? Promise.resolve(response({ detail: { status: 'BINDING_MISMATCH', issues: [{ code: 'ASSET_MISMATCH', path: 'asset' }] } }, 409)) : underlying(url, options))
    await submit(w)
    expect(w.find('[data-testid="joint-result"]').exists()).toBe(false)
  })
  it('loads a saved report via its exact ID without retraining or invoking a model', async () => {
    const { w, fetcher } = await setup(); await w.get('[data-testid="open-joint-joint_synthetic"]').trigger('click'); await flushPromises()
    expect(w.get('[data-testid="joint-status"]').text()).toBe('SAME_ASSET_BOUND')
    expect(fetcher.mock.calls.some(c => c[0].endsWith('/execute') || c[0].includes('/run'))).toBe(false)
  })
  it('keeps actual negative value and uncertain value semantically distinct', async () => {
    const result = structuredClone(report); result.task_value.value_status = 'NEGATIVE_VALUE_EVIDENCE'
    const { w } = await setup(backend(result)); await select(w); await submit(w)
    expect(w.get('[data-testid="joint-value-state"]').text()).toBe('NEGATIVE_VALUE_EVIDENCE')
    expect(w.get('[data-testid="joint-value-explanation"]').text()).toContain('已有负任务价值证据')
    expect(w.get('[data-testid="joint-value"]').text()).toContain('-0.01')
  })
  it('preserves unavailable service failures and empty lists without synthetic fallback', async () => {
    const fetcher = vi.fn(async (url: string) => url === '/api/same-asset-joint/capabilities' ? response({ detail: 'JOINT_SERVICE_UNAVAILABLE' }, 503) : response(url === '/api/visual-cases' ? { cases: [] } : { reports: [] }))
    const { w } = await setup(fetcher)
    expect(w.get('[data-testid="joint-load-error"]').text()).toContain('JOINT_SERVICE_UNAVAILABLE')
    expect(w.get('[data-testid="no-saved-joints"]').text()).toContain('NOT_EVALUATED')
    expect(w.find('[data-testid="joint-result"]').exists()).toBe(false)
  })
  it('discards an in-flight response after the user changes the selected task', async () => {
    const fetcher = backend(), underlying = fetcher.getMockImplementation()!
    let resolve!: (value: ReturnType<typeof response>) => void
    fetcher.mockImplementation((url, options) => url === '/api/same-asset-joint' && options?.method === 'POST' ? new Promise(r => { resolve = r }) : underlying(url, options))
    const { w } = await setup(fetcher); await select(w); await w.get('[data-testid="joint-form"]').trigger('submit'); await flushPromises()
    await w.get('[aria-label="联合评估价值运行ID"]').setValue('another_run')
    resolve(response(report)); await flushPromises()
    expect(w.find('[data-testid="joint-result"]').exists()).toBe(false)
  })
})

describe('joint response and report boundary helpers', () => {
  it('rejects missing IDs, impossible states and incomplete successful bindings', () => {
    expect(() => checkedJoint({ joint_id: '', status: 'SAME_ASSET_BOUND' })).toThrow('有效ID')
    expect(() => checkedJoint({ joint_id: 'j', status: 'LIVE' })).toThrow('绑定状态')
    expect(() => checkedJoint({ joint_id: 'j', status: 'SAME_ASSET_BOUND', binding: { asset: { ...asset, asset_sha256: 'bad' } }, task_value: { status: 'EVALUATED' } })).toThrow('完整资产版本')
    expect(() => checkedJoint({ ...report, task_value: { status: 'NOT_EVALUATED' } })).toThrow('任务测量')
  })
  it('refuses another case or value run returned by an otherwise successful server response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => response({ ...report, case_id: 'another_case' })))
    await expect(jointApi.create(caseValue.case_id, runValue.run_id)).rejects.toThrow('其他案例或任务运行')
  })
  it('uses opaque encoded report IDs and refuses a different saved report', async () => {
    const fetcher = vi.fn(async (_url: string) => response(report)); vi.stubGlobal('fetch', fetcher)
    await expect(jointApi.get('j /?')).rejects.toThrow('错配')
    expect(fetcher.mock.calls[0][0]).toBe('/api/same-asset-joint/j%20%2F%3F?format=json')
  })
  it('retains missing values as UNKNOWN and loss arrays as actual values rather than zeros', () => {
    expect(bindingItems({ a: null, b: '', c: { seed: 0 }, d: [1, 2] })).toEqual([{ path: 'a', value: 'UNKNOWN' }, { path: 'b', value: 'UNKNOWN' }, { path: 'c.seed', value: '0' }, { path: 'd', value: '[1,2]' }])
    expect(valueExplanation('VALUE_UNCERTAIN')).not.toBe(valueExplanation('NEGATIVE_VALUE_EVIDENCE'))
    expect(valueExplanation('MODEL_UNRELIABLE')).toContain('可靠性不足')
  })
})
