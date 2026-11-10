/** Synthetic HTTP fixtures test UI contracts only; no fixture is research evidence. */
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import DatasetEvidenceAudit from '../src/components/DatasetEvidenceAudit.vue'
import { valueStateText } from '../src/evidence'

const candidate = { dataset_id: 'SYNTHETIC_UI_CANDIDATE', version: 'v1', content_hash: 'a'.repeat(64), row_count: 100, column_count: 3, columns: [{ name: 'id', dtype: 'string' }, { name: 'x', dtype: 'float' }, { name: 'label', dtype: 'int' }] }
const reference = { ...candidate, dataset_id: 'SYNTHETIC_UI_REFERENCE', version: 'ref-v1', content_hash: 'b'.repeat(64), columns: [{ name: 'key', dtype: 'string' }, { name: 'feature', dtype: 'float' }, { name: 'outcome', dtype: 'int' }] }
const plan = { plan_id: 'plan_' + 'c'.repeat(64), plan_hash: 'c'.repeat(64), plan: { sample_positions: [1, 8, 20], sample_size: 50, seed: 101, sampling_method: 'SRSWOR' } }
const audit = { counts: { population_size: 100, sample_size: 50, comparable: 48, passed: 47, disagreements: 1, incomparable: 2 }, disagreement_interval: { lower: 0, upper: .12 },
  statistical_decision: 'NEED_MORE_EVIDENCE', fact_status: 'UNKNOWN', formal_decision: 'NEED_MORE_EVIDENCE',
  observations: [{ position: 8, outcome: 'DISAGREE' }, { position: 20, outcome: 'INCOMPARABLE', reason: 'REFERENCE_KEY_MISSING' }], assumptions: ['SRSWOR_FIXED_BEFORE_OBSERVATION'] }
const evaluated = { plan_id: plan.plan_id, report_hash: 'd'.repeat(64), report: audit }
const joint = { joint_id: 'joint_' + 'e'.repeat(64), report: { candidate,
  structural_quality: { status: 'PARTIALLY_MEASURED', schema_name: 'generic', dataset_binding: candidate, rules_policy: 'UNDECLARED_DOMAIN_RULES_NOT_VERIFIED',
    metrics: { completeness: .98, completeness_status: 'MEASURED', missing_cells: 6, cell_count: 300, uniqueness: 1, uniqueness_status: 'MEASURED', format_validity: null, format_validity_status: 'NOT_VERIFIED', consistency: null, consistency_status: 'NOT_VERIFIED' } },
  source_integrity: { status: 'LOCAL_BINDING_MATCHED_ORIGIN_UNKNOWN', dataset_binding: candidate,
    content_hash: { status: 'MATCHED', algorithm: 'SHA-256', expected: candidate.content_hash, registered: candidate.content_hash, actual: candidate.content_hash },
    version: { status: 'MATCHED', registered: candidate.version, assessed: candidate.version }, origin_authenticity: { status: 'UNKNOWN' },
    signature: { status: 'NOT_PROVIDED' }, source_declaration: { status: 'UNAUTHENTICATED', scope: 'REFERENCE_PLAN_DECLARATION', declaration: { source_url: 'https://example.invalid/synthetic-declaration' } } },
  duplicate_metrics: { full_row: { status: 'MEASURED', excess_duplicate_rows: 0 }, content_excluding_auxiliary_ids: { status: 'MEASURED', excess_duplicate_rows: 2 }, task_features: { status: 'MEASURED', excess_duplicate_rows: 4 }, factual_duplicates: { status: 'UNKNOWN' } },
  evidence: { status: 'EVALUATED', report: audit }, value: { value_status: 'VALUE_UNCERTAIN', explanation_zh: '区间跨零', action_hint_zh: '补充评价', original_decision: { action: 'do_not_use', no_purchase_delta_u: 0 } },
  joint_decision: { formal_decision: 'NEED_MORE_EVIDENCE', fact_status: 'UNKNOWN', exploration_action: 'NEED_MORE_EVIDENCE' }, binding: { candidate, plan_id: plan.plan_id, run_id: 'run_synthetic' } } }
const ok = (data: unknown) => ({ ok: true, status: 200, json: async () => data })
const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals() })
function backend(auditReport: unknown = evaluated, jointReport: unknown = joint) {
  return vi.fn(async (url: string, _init?: RequestInit) => {
    if (url.includes('/datasets?')) return ok(reference)
    if (url.endsWith('/evaluate')) return ok(auditReport)
    if (url.endsWith('/plans')) return ok(plan)
    if (url.endsWith('/joint')) return ok(jointReport)
    throw new Error('Unexpected synthetic route ' + url)
  })
}
function setup(fetcher = backend(), candidateProp: typeof candidate | null = candidate, runId?: string) {
  vi.stubGlobal('fetch', fetcher)
  const w = mount(DatasetEvidenceAudit, { props: { candidate: candidateProp, runId, featureColumns: ['x'] } }); wrappers.push(w)
  return { w, fetcher }
}
async function upload(w: VueWrapper) {
  const input = w.get('[aria-label="联合审计参考CSV"]')
  Object.defineProperty(input.element, 'files', { value: [new File(['key,feature,outcome\na,1,0'], 'synthetic-reference.csv', { type: 'text/csv' })], configurable: true })
  await input.trigger('change'); await w.get('[data-testid="upload-reference"]').trigger('click'); await flushPromises()
}
async function configure(w: VueWrapper) {
  await upload(w)
  await w.get('[aria-label="候选键 0"]').setValue('id'); await w.get('[aria-label="参考键 0"]').setValue('key')
  await w.get('[aria-label="候选规则字段 0"]').setValue('x'); await w.get('[aria-label="参考规则字段 0"]').setValue('feature')
}
async function freeze(w: VueWrapper) { await w.get('[data-testid="freeze-evidence-plan"]').trigger('click'); await flushPromises() }
async function evaluate(w: VueWrapper) { await w.get('[data-testid="evaluate-evidence-plan"]').trigger('click'); await flushPromises() }
async function combine(w: VueWrapper) { await w.get('[data-testid="create-joint-report"]').trigger('click'); await flushPromises() }

describe('same-version evidence and value UI with synthetic HTTP mocks', () => {
  it('requires a shared imported candidate and invents no result', () => {
    const { w, fetcher } = setup(backend(), null)
    expect(w.text()).toContain('先在上方导入候选CSV')
    expect(w.find('[data-testid="joint-result"]').exists()).toBe(false)
    expect(fetcher).not.toHaveBeenCalled()
  })
  it('uploads reference bytes locally and explicitly keeps provenance as declarations', async () => {
    const { w, fetcher } = setup(); await upload(w)
    expect(fetcher.mock.calls[0][0]).toContain('/api/self-service/datasets?')
    expect(fetcher.mock.calls[0][1]?.body).toBeInstanceOf(File)
    expect(fetcher.mock.calls[0][1]?.headers).toMatchObject({ 'X-Research-Request': '1' })
    expect(w.get('[data-testid="evidence-reference-binding"]').text()).toContain(reference.content_hash)
    expect(w.text()).toContain('均为用户声明，尚未核验')
  })
  it('freezes exact candidate/reference hashes, explicit key mapping and canonical audit defaults', async () => {
    const { w, fetcher } = setup(); await configure(w); await w.get('[aria-label="参考来源关系"]').setValue('SAME_UPSTREAM'); await freeze(w)
    const call = fetcher.mock.calls.find(([url]) => url.endsWith('/plans'))!
    expect(JSON.parse(String(call[1]?.body))).toEqual({ candidate_id: candidate.dataset_id, expected_candidate_hash: candidate.content_hash,
      reference_id: reference.dataset_id, expected_reference_hash: reference.content_hash,
      config: { key_mapping: { id: 'key' }, rules: [{ candidate_column: 'x', reference_column: 'feature', comparison: 'exact', atol: 0, rtol: 0 }], sample_size: 50, seed: 101, epsilon: .05, alpha: .05, sampling_method: 'SRSWOR' },
      source_declaration: { description: '', source_url: '', license: '', observed_at: '', version: '', relationship: 'SAME_UPSTREAM' } })
    expect(w.get('[data-testid="evidence-plan"]').text()).toContain(plan.plan_hash)
    expect(w.find('[data-testid="evidence-result"]').exists()).toBe(false)
  })
  it('sends numeric tolerance rules rather than comparing fields in the browser', async () => {
    const { w, fetcher } = setup(); await configure(w); await w.get('[aria-label="字段比较规则 0"]').setValue('numeric')
    await w.get('[aria-label="绝对容差 0"]').setValue('.1'); await w.get('[aria-label="相对容差 0"]').setValue('.01'); await freeze(w)
    const body = JSON.parse(String(fetcher.mock.calls.find(([url]) => url.endsWith('/plans'))![1]?.body))
    expect(body.config.rules[0]).toMatchObject({ comparison: 'numeric', atol: .1, rtol: .01 })
  })
  it('keeps incomparable counts, observations, reference coverage and factual UNKNOWN separate', async () => {
    const { w } = setup(); await configure(w); await freeze(w); await evaluate(w)
    expect(w.get('[data-testid="evidence-counts"]').text()).toContain('47 / 1 / 2')
    expect(w.get('[data-testid="evidence-coverage"]').text()).toContain('96.000%')
    expect(w.get('[data-testid="evidence-interval"]').text()).toContain('12.000%')
    expect(w.get('[data-testid="evidence-fact-status"]').text()).toBe('UNKNOWN')
    expect(w.get('[data-testid="evidence-formal-decision"]').text()).toBe('NEED_MORE_EVIDENCE')
    expect(w.text()).toContain('REFERENCE_KEY_MISSING')
    expect(w.text()).toContain('不可比或未解决不等于通过')
    expect(w.get('a[href$="format=md"]').attributes('href')).toContain(plan.plan_id)
  })
  it('shows an unavailable non-probability interval without supplying a replacement', async () => {
    const { w } = setup(backend({ ...evaluated, report: { ...audit, disagreement_interval: null, statistical_decision: 'NON_PROBABILITY_NO_INTERVAL' } }))
    await configure(w); await freeze(w); await evaluate(w)
    expect(w.get('[data-testid="evidence-interval"]').text()).toContain('UNKNOWN')
    expect(w.get('[data-testid="evidence-statistical-decision"]').text()).toBe('NON_PROBABILITY_NO_INTERVAL')
  })
  it('audits without a task and combines only evaluated evidence IDs', async () => {
    const { w, fetcher } = setup(); await configure(w); await freeze(w); await evaluate(w); await combine(w)
    const body = JSON.parse(String(fetcher.mock.calls.find(([url]) => url.endsWith('/joint'))![1]?.body))
    expect(body).toEqual({ candidate_id: candidate.dataset_id, expected_candidate_hash: candidate.content_hash, plan_id: plan.plan_id, auxiliary_id_columns: [], feature_columns: ['x'] })
    expect(body.run_id).toBeUndefined()
  })
  it('combines value without a reference and preserves source version binding', async () => {
    const { w, fetcher } = setup(backend(), candidate, 'run_synthetic'); await combine(w)
    const body = JSON.parse(String(fetcher.mock.calls.find(([url]) => url.endsWith('/joint'))![1]?.body))
    expect(body.run_id).toBe('run_synthetic'); expect(body.plan_id).toBeUndefined()
    expect(body.expected_candidate_hash).toBe(candidate.content_hash)
    expect(w.get('[data-testid="joint-candidate-binding"]').text()).toContain(candidate.version)
  })
  it('explains uncertainty without asserting zero value, retaining original no-purchase decision', async () => {
    const { w } = setup(); await combine(w)
    expect(w.get('[data-testid="joint-value-status"]').text()).toContain('当前证据不足，暂缓使用或补充评价')
    expect(w.get('[data-testid="joint-original-decision"]').text()).toContain('do_not_use')
    expect(w.get('[data-testid="joint-original-decision"]').text()).toContain('no_purchase_delta_u')
    expect(w.get('[data-testid="joint-formal-decision"]').text()).toBe('NEED_MORE_EVIDENCE')
    expect(w.get('[data-testid="joint-fact-status"]').text()).toBe('UNKNOWN')
  })
  it('separates full-row, content, task-feature and unknown factual duplication', async () => {
    const { w, fetcher } = setup(); await w.get('[aria-label="重复诊断辅助ID id"]').setValue(true); await combine(w)
    expect(JSON.parse(String(fetcher.mock.calls.find(([url]) => url.endsWith('/joint'))![1]?.body)).auxiliary_id_columns).toEqual(['id'])
    expect(w.get('[data-testid="duplicate-full-row"]').text()).toContain('额外重复 0 行')
    expect(w.get('[data-testid="duplicate-content"]').text()).toContain('额外重复 2 行')
    expect(w.get('[data-testid="duplicate-task-features"]').text()).toContain('额外重复 4 行')
    expect(w.get('[data-testid="duplicate-factual"]').text()).toContain('UNKNOWN')
  })
  it('shows measured cell completeness and keeps undeclared format and consistency unverified', async () => {
    const { w } = setup(); await combine(w)
    expect(w.get('[data-testid="joint-structural-status"]').text()).toContain('PARTIALLY_MEASURED')
    expect(w.get('[data-testid="joint-completeness"]').text()).toContain('98.000% · MEASURED')
    expect(w.get('[data-testid="joint-completeness"]').text()).toContain('6 / 300')
    expect(w.get('[data-testid="joint-format-validity"]').text()).toBe('UNKNOWN · NOT_VERIFIED')
    expect(w.get('[data-testid="joint-consistency"]').text()).toBe('UNKNOWN · NOT_VERIFIED')
    expect(w.get('[data-testid="joint-structural-details"]').text()).toContain('UNDECLARED_DOMAIN_RULES_NOT_VERIFIED')
  })
  it('separates matched local hash/version from unknown origin and unauthenticated source declarations', async () => {
    const { w } = setup(); await combine(w)
    expect(w.get('[data-testid="joint-source-binding"]').text()).toContain('SHA-256=MATCHED；版本=MATCHED')
    expect(w.get('[data-testid="joint-source-authenticity"]').text()).toBe('UNKNOWN')
    expect(w.get('[data-testid="joint-signature-status"]').text()).toBe('NOT_PROVIDED')
    expect(w.get('[data-testid="joint-source-declarations"]').text()).toContain('UNAUTHENTICATED')
    expect(w.get('[data-testid="joint-source-integrity-details"]').text()).toContain(candidate.content_hash)
    expect(w.get('[data-testid="joint-source-integrity-details"]').text()).toContain('synthetic-declaration')
    expect(w.get('[data-testid="joint-formal-decision"]').text()).toBe('NEED_MORE_EVIDENCE')
  })
  it('does not invent structural scores or source verification when optional evidence is absent', async () => {
    const { structural_quality: _structure, source_integrity: _source, ...olderReport } = joint.report
    const { w } = setup(backend(evaluated, { ...joint, report: olderReport })); await combine(w)
    expect(w.get('[data-testid="joint-completeness"]').text()).toContain('UNKNOWN')
    expect(w.get('[data-testid="joint-format-validity"]').text()).toBe('UNKNOWN · UNKNOWN')
    expect(w.get('[data-testid="joint-source-binding"]').text()).toContain('SHA-256=UNKNOWN')
    expect(w.get('[data-testid="joint-source-declarations"]').text()).toContain('UNKNOWN')
  })
  it('clears all evidence when candidate version/hash changes', async () => {
    const { w } = setup(); await configure(w); await freeze(w); await evaluate(w); await combine(w)
    await w.setProps({ candidate: { ...candidate, version: 'v2', content_hash: 'f'.repeat(64) } })
    expect(w.find('[data-testid="evidence-plan"]').exists()).toBe(false)
    expect(w.find('[data-testid="evidence-result"]').exists()).toBe(false)
    expect(w.find('[data-testid="joint-result"]').exists()).toBe(false)
  })
  it('changing audit design discards prior plan instead of mutating it', async () => {
    const { w } = setup(); await configure(w); await freeze(w)
    await w.get('[aria-label="联合审计样本量"]').setValue('60')
    expect(w.find('[data-testid="evidence-plan"]').exists()).toBe(false)
  })
  it('changing value-run ID invalidates the combined report only', async () => {
    const { w } = setup(); await configure(w); await freeze(w); await evaluate(w); await combine(w)
    await w.setProps({ runId: 'new_run' })
    expect(w.find('[data-testid="joint-result"]').exists()).toBe(false)
    expect(w.find('[data-testid="evidence-result"]').exists()).toBe(true)
  })
  it('preserves backend binding rejection and never displays a forged joint success', async () => {
    const fetcher = backend(); fetcher.mockImplementation(async () => ({ ok: false, status: 409, json: async () => ({ detail: 'CANDIDATE_VERSION_HASH_MISMATCH' }) }))
    const { w } = setup(fetcher); await combine(w)
    expect(w.get('[role="alert"]').text()).toContain('CANDIDATE_VERSION_HASH_MISMATCH')
    expect(w.find('[data-testid="joint-result"]').exists()).toBe(false)
  })
  it('rejects incomplete field mappings before any plan mutation', async () => {
    const { w, fetcher } = setup(); await upload(w); await freeze(w)
    expect(w.get('[role="alert"]').text()).toContain('完整选择候选键与参考键')
    expect(fetcher.mock.calls.some(([url]) => url.endsWith('/plans'))).toBe(false)
  })
  it('exports exact saved joint IDs with local JSON and Chinese Markdown URLs', async () => {
    const { w } = setup(); await combine(w)
    const links = w.get('[data-testid="joint-result"]').findAll('a[download]').map(link => link.attributes('href'))
    expect(links).toEqual([`/api/evidence/joint/${joint.joint_id}/report?format=json`, `/api/evidence/joint/${joint.joint_id}/report?format=md`])
  })
})

describe('five value evidence states', () => {
  it.each(['POSITIVE_VALUE_EVIDENCE', 'NEGATIVE_VALUE_EVIDENCE', 'VALUE_UNCERTAIN', 'TASK_MODEL_UNRELIABLE', 'NOT_EVALUATED'])('describes %s without changing a saved decision', status => {
    expect(valueStateText(status)).not.toBe('状态含义请核对后端原始报告')
  })
})
