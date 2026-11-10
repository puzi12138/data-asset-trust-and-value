/** SYNTHETIC_UI_ONLY: no audit, model execution, training or physical observation. */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import DiEvidenceCoverage from '../src/components/DiEvidenceCoverage.vue'
import { checkedCoverage, coverageApi, type DiCoverage } from '../src/diEvidence'
import type { Row } from '../src/api'

const id = 'joint_ui_synthetic'
const member = { candidate_row: 7, row_id: 'source_0042', role: 'Di', reference_record: 'source_0042', reference_line: 42, outcome: 'MATCH', evidence_level: 'B_PUBLIC_COLLECTOR_REFERENCE', checks: [{ field: 'label', candidate: '8', reference: '8', outcome: 'MATCH' }] }
const report: DiCoverage = { joint_id: id, status: 'SAME_ASSET_BOUND', result_sha256: 'b'.repeat(64), binding: { asset: { asset_id: 'UI_SYNTHETIC', asset_version: 'v1', asset_sha256: 'a'.repeat(64) }, Di_content_hash: 'c'.repeat(64), value_result_sha256: 'd'.repeat(64) },
  audit_summary: { population_size: 3, reference_available_records: 2, audited_records: 2, match_records: 1, mismatch_records: 0, incomparable_records: 1, disputed_records: 0, not_audited_records: 1, ai_visual_reviewed_di_records: 0, independent_fact_verified_records: 0, reference_level: 'B_PUBLIC_COLLECTOR_REFERENCE', shared_upstream: true, reference_conformance_status: 'NEED_MORE_EVIDENCE', fact_status: 'UNKNOWN' },
  members: [member], reference: { evidence_class: 'B', shared_upstream: true },
  budget_results: [{ budget: 2, method: 'FIXED_SRS', assessment: { disagreement_interval: { lower: 0, upper: .7 }, coverage_claim: 'SYNTHETIC_FIXED_REFERENCE_ONLY' }, reference_conformance_status: 'NEED_MORE_EVIDENCE', coverage: { not_audited_records: 1 } }],
  task_value: { status: 'EVALUATED', value_status: 'VALUE_UNCERTAIN', validation: { delta_u: -.02, ci_lower: -.1, ci_upper: .1, loss_unit: 'natural_log_units' }, recommendation: { action: 'do_not_use', uses_test: false } },
  decision: { formal_factual_use: 'NEED_MORE_EVIDENCE', value_status: 'VALUE_UNCERTAIN', recommendation: '当前证据不足，暂缓使用或补充评价。', trust_multiplier_applied: false } }
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body })
const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals() })
function backend(result = structuredClone(report)) {
  return vi.fn(async (url: string, _options?: RequestInit) => {
    if (url === `/api/di-evidence/${id}?format=json`) return response(result)
    if (url === '/api/di-evidence/capabilities') return response({ no_new_model_calls: true, audit_availability: 'AVAILABLE' })
    if (url.startsWith(`/api/di-evidence/${id}/rows?`)) { const outcome = new URL(url, 'http://localhost').searchParams.get('outcome'); return response({ rows: outcome === 'NOT_AUDITED' ? [] : [member], total: outcome === 'NOT_AUDITED' ? 0 : 1, offset: 0, limit: 100 }) }
    if (url === `/api/di-evidence/${id}/rows/7`) return response(member)
    throw new Error('Unexpected SYNTHETIC UI URL ' + url)
  })
}
async function setup(fetcher = backend()) {
  vi.stubGlobal('fetch', fetcher)
  const w = mount(DiEvidenceCoverage, { props: { jointId: id } }); wrappers.push(w); await flushPromises(); return { w, fetcher }
}
describe('Di coverage minimal report extension', () => {
  it('uses only saved report GETs and does not launch a model, train or mutate audit evidence', async () => {
    const { w, fetcher } = await setup()
    expect(fetcher.mock.calls.map(c => c[0])).toEqual([`/api/di-evidence/${id}?format=json`, '/api/di-evidence/capabilities'])
    expect(fetcher.mock.calls.every(c => c[1]?.method === 'GET')).toBe(true)
    expect(w.get('[data-testid="coverage-status"]').text()).toBe('SAME_ASSET_BOUND')
  })
  it('displays actual denominators, unknown positions and no inflated AI or independent observation count', async () => {
    const { w } = await setup()
    for (const [key, count] of Object.entries(report.audit_summary!)) {
      if (typeof count === 'number') expect(w.get(`[data-testid="coverage-count-${key}"]`).text()).toBe(String(count))
    }
    expect(w.get('[data-testid="coverage-count-population_size"]').text()).toBe('3')
    expect(w.text()).toContain('重复审阅不增加独立审计样本数')
    expect(w.text()).toContain('验证集或测试集中的视觉记录不充当Di内容审计')
  })
  it('keeps fixed-reference B, shared upstream and independent reality C separate', async () => {
    const { w } = await setup()
    expect(w.get('[data-testid="coverage-reference-level"]').text()).toBe('B_PUBLIC_COLLECTOR_REFERENCE')
    expect(w.get('[data-testid="coverage-fact-status"]').text()).toBe('UNKNOWN')
    expect(w.get('[data-testid="coverage-evidence-boundary"]').text()).toContain('共享原始采集上游')
    expect(w.text()).toContain('不自动成为独立现实事实核验C')
  })
  it('preserves value uncertainty, loss unit, negative realized effect and do-not-use baseline', async () => {
    const { w } = await setup()
    expect(w.get('[data-testid="coverage-value-state"]').text()).toBe('VALUE_UNCERTAIN')
    expect(w.get('[data-testid="coverage-value-explanation"]').text()).toContain('不表示已经证明数据无价值')
    expect(w.text()).toContain('-0.02'); expect(w.text()).toContain('natural_log_units'); expect(w.text()).toContain('do_not_use')
    expect(w.get('[data-testid="coverage-decision"]').text()).toContain('暂缓使用')
  })
  it.each(['NEGATIVE_VALUE_EVIDENCE', 'POSITIVE_VALUE_EVIDENCE', 'TASK_MODEL_UNRELIABLE'])('displays %s using the existing distinct semantics', async state => {
    const result = structuredClone(report); result.task_value.value_status = state
    const { w } = await setup(backend(result))
    expect(w.get('[data-testid="coverage-value-state"]').text()).toBe(state)
    expect(w.get('[data-testid="coverage-value-explanation"]').text()).not.toContain('已经证明数据无价值')
  })
  it('does not fill absent measurements or absent counts with zeros', async () => {
    const result = structuredClone(report); delete result.audit_summary!.mismatch_records; delete result.task_value
    const { w } = await setup(backend(result))
    expect(w.get('[data-testid="coverage-count-mismatch_records"]').text()).toBe('UNKNOWN')
    expect(w.get('[data-testid="coverage-value-state"]').text()).toBe('NOT_EVALUATED')
  })
  it('shows saved fixed-budget intervals without calculating a truth probability', async () => {
    const { w } = await setup()
    expect(w.get('[data-testid="coverage-budgets"]').text()).toContain('[0, 0.7]')
    expect(w.get('[data-testid="coverage-budgets"]').text()).toContain('FIXED_SRS')
    expect(w.text()).toContain('不构成多预算同时保证或可选停止保证')
  })
  it('exports the exact joint report without constructing a browser-only result', async () => {
    const { w } = await setup()
    expect(w.get('[data-testid="coverage-export-md"]').attributes('href')).toBe(`/api/di-evidence/${id}?format=md`)
    expect(w.get('[data-testid="coverage-export-json"]').attributes('href')).toBe(`/api/di-evidence/${id}?format=json`)
  })
  it('loads server members and exact original reference for the selected Di row', async () => {
    const { w, fetcher } = await setup(); await w.get('[data-testid="coverage-show-members"]').trigger('click'); await flushPromises()
    expect(fetcher.mock.calls.map(c => c[0])).toContain(`/api/di-evidence/${id}/rows?offset=0&limit=100`)
    expect(w.get('[data-testid="coverage-members"]').text()).toContain('source_0042')
    await w.get('[data-testid="coverage-row-7"]').trigger('click'); await flushPromises()
    expect(fetcher.mock.calls.map(c => c[0])).toContain(`/api/di-evidence/${id}/rows/7`)
    expect(w.get('[data-testid="coverage-row-detail"]').text()).toContain('"reference": "8"')
  })
  it('allows viewing unverified records and keeps their original summary denominator', async () => {
    const { w, fetcher } = await setup(); await w.get('[data-testid="coverage-show-members"]').trigger('click'); await flushPromises()
    await w.get('[aria-label="Di核查状态筛选"]').setValue('NOT_AUDITED'); await flushPromises()
    expect(fetcher.mock.calls.map(c => c[0])).toContain(`/api/di-evidence/${id}/rows?offset=0&limit=100&outcome=NOT_AUDITED`)
    expect(w.get('[data-testid="coverage-count-not_audited_records"]').text()).toBe('1')
    expect(w.get('[data-testid="coverage-member-total"]').text()).toContain('共 0 条')
  })
  it('refuses wrong-row, wrong-role reference details instead of relabeling validation as Di', async () => {
    const fetcher = backend(), original = fetcher.getMockImplementation()!
    fetcher.mockImplementation((url, options) => url.endsWith('/rows/7') ? Promise.resolve(response({ ...member, role: 'validation' })) : original(url, options))
    const { w } = await setup(fetcher); await w.get('[data-testid="coverage-show-members"]').trigger('click'); await flushPromises(); await w.get('[data-testid="coverage-row-7"]').trigger('click'); await flushPromises()
    expect(w.get('[data-testid="coverage-detail-error"]').text()).toContain('不是所选Di成员')
    expect(w.find('[data-testid="coverage-row-detail"]').exists()).toBe(false)
  })
  it.each(['REFERENCE_VERSION_MISMATCH', 'DI_MEMBERSHIP_MISMATCH', 'ASSET_SHA256_MISMATCH'])('preserves %s as a refusal and clears any audit conclusion', async code => {
    const { w } = await setup(vi.fn(async () => response({ detail: { status: 'BINDING_MISMATCH', issues: [{ code }] } }, 409)))
    expect(w.get('[data-testid="coverage-error"]').text()).toContain('BINDING_MISMATCH')
    expect(w.get('[data-testid="coverage-error"]').text()).toContain(code)
    expect(w.find('[data-testid="coverage-counts"]').exists()).toBe(false)
  })
  it('shows unavailable exact-version audit as NOT_EVALUATED without a zero-error assertion', async () => {
    const { w } = await setup(backend({ joint_id: id, status: 'NOT_EVALUATED', audit_summary: null, members: [], reason: 'NO_AUDIT_FOR_EXACT_VERSION' }))
    expect(w.get('[data-testid="coverage-not-evaluated"]').text()).toContain('NO_AUDIT_FOR_EXACT_VERSION')
    expect(w.find('[data-testid="coverage-counts"]').exists()).toBe(false)
    expect(w.find('[data-testid="coverage-export-md"]').exists()).toBe(false)
  })
  it('does not convert duplicate member display into additional audited or physical samples', async () => {
    const fetcher = backend(), original = fetcher.getMockImplementation()!
    fetcher.mockImplementation((url, options) => url.includes('/rows?') ? Promise.resolve(response({ rows: [member, { ...member, candidate_row: 8 }], total: 2, offset: 0, limit: 100 })) : original(url, options))
    const { w } = await setup(fetcher); await w.get('[data-testid="coverage-show-members"]').trigger('click'); await flushPromises()
    expect(w.get('[data-testid="coverage-count-audited_records"]').text()).toBe('2')
    expect(w.get('[data-testid="coverage-count-ai_visual_reviewed_di_records"]').text()).toBe('0')
  })
  it('discards an earlier audit response after the selected joint changes', async () => {
    let resolve!: (value: ReturnType<typeof response>) => void
    const fetcher = backend(), original = fetcher.getMockImplementation()!
    fetcher.mockImplementation((url, options) => url === `/api/di-evidence/${id}?format=json` ? new Promise(r => { resolve = r }) : url === '/api/di-evidence/another?format=json' ? Promise.resolve(response({ joint_id: 'another', status: 'NOT_EVALUATED', audit_summary: null })) : original(url, options))
    vi.stubGlobal('fetch', fetcher)
    const w = mount(DiEvidenceCoverage, { props: { jointId: id } }); wrappers.push(w); await flushPromises()
    await w.setProps({ jointId: 'another' }); await flushPromises(); resolve(response(report)); await flushPromises()
    expect(w.get('[data-testid="coverage-status"]').text()).toBe('NOT_EVALUATED')
    expect(w.find('[data-testid="coverage-counts"]').exists()).toBe(false)
  })
})
describe('coverage identity boundary helpers', () => {
  it('rejects wrong report identity, invalid status and missing successful audit summary', () => {
    expect(() => checkedCoverage({ ...report, joint_id: 'other' }, id)).toThrow('错版本')
    expect(() => checkedCoverage({ ...report, status: 'TRUTH_CERTIFIED' }, id)).toThrow('错版本')
    expect(() => checkedCoverage({ ...report, audit_summary: null }, id)).toThrow('审计统计')
  })
  it('encodes opaque joint IDs and rejects an unrelated server report', async () => {
    const fetcher = vi.fn(async (_url: string) => response(report)); vi.stubGlobal('fetch', fetcher)
    await expect(coverageApi.get('j /?')).rejects.toThrow('错版本')
    expect(fetcher.mock.calls[0][0]).toBe('/api/di-evidence/j%20%2F%3F?format=json')
  })
})
