/** Synthetic API fixtures test UI behavior only; no model or real-world audit runs here. */
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { createHash, webcrypto } from 'node:crypto'
import { afterEach, describe, expect, it, vi } from 'vitest'
import VisualCases from '../src/pages/VisualCases.vue'
import { checkedCase, caseAsset, fileData, readable, casesApi } from '../src/visualCases'

const asset = { asset_id: 'synthetic_new_asset', asset_version: 'v1', asset_sha256: 'a'.repeat(64) }
const image = { evidence_id: 'evidence-1', filename: 'synthetic.png', sha256: 'b'.repeat(64), pixel_sha256: 'c'.repeat(64), width: 32, height: 32, source_status: 'DECLARED_NOT_AUTHENTICATED' }
const original = { case_id: 'case_ui_synthetic', asset, evidence: [image], protocol_sha256: 'd'.repeat(64), source_status: 'DECLARED_NOT_AUTHENTICATED',
  protocol: { entity_key: 'id', entity_value: 'entity-a', field: 'label', observation_target: '测试牌面形状', observation_rule: '测试规则：遮挡时不可比，不使用桥梁范围', capture_time: null, allowed_outcomes: ['MATCH', 'MISMATCH', 'INCOMPARABLE', 'DISPUTED'], source: { description: 'SYNTHETIC_UI_ONLY', url: '', license: 'Synthetic UI fixture', independence_declaration: 'NOT_REAL_FACTS' } },
  authorization: { local_processing: true, external_model_upload: false }, reviews: [] as Record<string, any>[], executions: [] as Record<string, any>[], validations: [], coordination_state: 'ADDITIONAL_EVIDENCE_REQUIRED',
  deduplication: { evidence_file_count: 1, unique_pixel_observation_count: 1, review_count: 0, additional_physical_observations_from_repeat_reviews: 0, independent_physical_observation_count: null },
  assessment: { fact_status: 'UNKNOWN', decision: 'NEED_MORE_EVIDENCE', task_value: { status: 'NOT_EVALUATED' }, population_error_interval: null, supplementary_evidence_needed: ['需要独立核实来源与实体对应'] } }
const capabilities = { live_available: false, status: 'UNAVAILABLE', reason: 'LIVE_UNAVAILABLE', adapter: { provider: null, model: null } }
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body })
const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); vi.unstubAllGlobals() })
function backend(caseValue = structuredClone(original)) {
  return vi.fn(async (url: string, options?: RequestInit) => {
    if (url === '/api/visual-cases/capabilities') return response(capabilities)
    if (url === '/api/visual-cases') return options?.method === 'POST' ? response(caseValue) : response({ cases: [caseValue] })
    if (url.includes('/execute')) return response({ detail: { status: 'UNAVAILABLE', execution_mode: 'UNAVAILABLE', reason: 'LIVE_UNAVAILABLE', model_invoked: false, fallback_performed: false } }, 503)
    if (url.endsWith('/validate')) return response({ status: 'LOCAL_INTEGRITY_VERIFIED', source_identity_verified: false, fact_verified: false })
    if (url.includes('/api/visual-cases/case_')) return response(caseValue)
    throw new Error('Unexpected synthetic UI URL ' + url)
  })
}
async function setup(fetcher = backend()) {
  vi.stubGlobal('fetch', fetcher); vi.stubGlobal('crypto', webcrypto)
  const w = mount(VisualCases, { global: { stubs: { RouterLink: { template: '<a><slot/></a>' } } } }); wrappers.push(w)
  await flushPromises(); return { w, fetcher }
}
async function choose(w: VueWrapper) { await w.get('[data-testid="case-case_ui_synthetic"]').trigger('click'); await flushPromises() }
function browserFile(name: string, text: string) {
  const bytes = new TextEncoder().encode(text), file = new File([bytes], name)
  Object.defineProperty(file, 'arrayBuffer', { value: async () => bytes.buffer })
  return file
}
async function pick(w: VueWrapper, selector: string, files: File[]) {
  const input = w.get(selector); Object.defineProperty(input.element, 'files', { value: files, configurable: true })
  await input.trigger('change'); await flushPromises()
  // WebCrypto resolves on its native queue, outside Vue's immediate queue.
  for (let i = 0; i < 20 && w.get('[data-testid="register-visual-case"]').text().includes('计算'); i++) { await new Promise(resolve => setTimeout(resolve, 5)); await flushPromises() }
}
async function fill(w: VueWrapper) {
  await pick(w, '[aria-label="视觉案例CSV"]', [browserFile('new.csv', 'id,label\nentity-a,stop\n')])
  await pick(w, '[aria-label="视觉证据图像"]', [browserFile('new.png', 'synthetic image bytes')])
  const fields: Record<string, string> = { 视觉数据集ID: 'synthetic_new_asset', 视觉数据集版本: 'v1', 视觉任务ID: 'ui-test', 视觉实体匹配字段: 'id', 视觉实体键值: 'entity-a', 视觉待核验字段: 'label', 视觉观察目标: '测试窄属性', 视觉观察规则: '遮挡则不可比，禁止总体事实推断', 视觉证据来源描述: 'SYNTHETIC_UI_ONLY', 视觉证据使用条件: 'Synthetic unit fixture', 视觉证据独立性声明: 'UNKNOWN' }
  for (const [label, value] of Object.entries(fields)) await w.get('[aria-label="' + label + '"]').setValue(value)
  await w.get('[aria-label="允许本地处理"]').setValue(true)
}

describe('general visual case API interface with synthetic fixtures', () => {
  it('loads backend model capability and cases without claiming LIVE succeeded', async () => {
    const { w, fetcher } = await setup()
    expect(fetcher.mock.calls.map(call => call[0])).toContain('/api/visual-cases/capabilities')
    expect(w.get('[data-testid="live-capability"]').text()).toContain('LIVE_UNAVAILABLE')
    expect(w.text()).toContain('接口工程不等于已完成在线审阅')
    expect(w.find('[data-testid="new-case-execution"]').exists()).toBe(false)
  })
  it('keeps missing model information unknown and has no browser credential field', async () => {
    const { w } = await setup()
    expect(w.get('[data-testid="live-capability"]').text()).toContain('UNKNOWN')
    expect(w.find('input[type="password"]').exists()).toBe(false)
    expect(w.get('[aria-label="允许注册案例外发模型"]').element).toHaveProperty('checked', false)
  })
  it('shows a genuine empty registry without fabricated cards or a score', async () => {
    const fetcher = backend(), underlying = fetcher.getMockImplementation()!
    fetcher.mockImplementation((url, options) => url === '/api/visual-cases' ? Promise.resolve(response({ cases: [] })) : underlying(url, options))
    const { w } = await setup(fetcher)
    expect(w.get('[data-testid="no-visual-cases"]').text()).toContain('未导入图像不会生成视觉成功')
    expect(w.find('[data-testid="new-case-joint"]').exists()).toBe(false)
  })
  it('binds case, dataset SHA, original image and domain-specific protocol', async () => {
    const { w } = await setup(); await choose(w)
    expect(w.get('[data-testid="new-case-binding"]').text()).toContain(asset.asset_sha256)
    expect(w.get('[data-testid="new-case-evidence-image"]').attributes('src')).toBe('/api/visual-cases/case_ui_synthetic/evidence/evidence-1')
    expect(w.text()).toContain(original.protocol.observation_rule)
    expect(w.text()).toContain('不预设桥梁或50米规则')
    expect(w.get('[data-testid="source-status"]').text()).toContain('DECLARED_NOT_AUTHENTICATED')
  })
  it('keeps unknown facts, unevaluated task value and absent probability interval', async () => {
    const { w } = await setup(); await choose(w)
    expect(w.get('[data-testid="new-case-fact"]').text()).toBe('UNKNOWN')
    expect(w.get('[data-testid="new-case-value"]').text()).toBe('NOT_EVALUATED')
    expect(w.text()).toContain('重复AI审阅不增加物理样本')
    expect(w.get('[data-testid="no-new-reviews"]').text()).toContain('未生成虚假的MATCH')
  })
  it('does not treat a local hash receipt as identity authentication or fact truth', async () => {
    const { w, fetcher } = await setup(); await choose(w)
    await w.get('[data-testid="validate-new-evidence"]').trigger('click'); await flushPromises()
    expect(fetcher.mock.calls.find(call => call[0].endsWith('/validate'))?.[1]?.body).toBe('{"evidence_id":"evidence-1"}')
    expect(w.text()).toContain('source_identity_verified')
    expect(w.get('[data-testid="new-case-fact"]').text()).toBe('UNKNOWN')
  })
  it('does not silently fall back to REPLAY when LIVE is unavailable', async () => {
    const { w, fetcher } = await setup(); await choose(w)
    await w.get('[data-testid="new-case-live"]').trigger('click'); await flushPromises()
    expect(w.get('[data-testid="new-case-execution-error"]').text()).toContain('LIVE_UNAVAILABLE')
    expect(w.get('[data-testid="new-case-execution"]').text()).toContain('UNAVAILABLE')
    expect(w.find('[data-testid="new-case-note"]').exists()).toBe(false)
    const executions = fetcher.mock.calls.filter(call => call[0].endsWith('/execute'))
    expect(executions).toHaveLength(1)
    expect(JSON.parse(executions[0][1]!.body as string)).toMatchObject({ mode: 'LIVE', ...{ asset_sha256: asset.asset_sha256, asset_version: 'v1' }, external_upload_authorized: false })
  })
  it.each([['MODEL_TIMEOUT', 'TIMEOUT', 504], ['MODEL_REQUEST_FAILED', 'ADAPTER_ERROR', 502]] as const)('preserves %s and does not label it successful LIVE', async (reason, type, status) => {
    const fetcher = backend(), underlying = fetcher.getMockImplementation()!
    fetcher.mockImplementation((url, options) => url.endsWith('/execute') ? Promise.resolve(response({ detail: { status: 'FAILED', execution_mode: 'FAILED', reason, error_type: type, model_invoked: false, fallback_performed: false } }, status)) : underlying(url, options))
    const { w } = await setup(fetcher); await choose(w); await w.get('[data-testid="new-case-live"]').trigger('click'); await flushPromises()
    expect(w.get('[data-testid="new-case-execution-error"]').text()).toContain(reason)
    expect(w.get('[data-testid="new-case-execution"]').text()).toContain(type)
    expect(w.find('[data-testid="new-case-note"]').exists()).toBe(false)
  })
  it('rejects a 200 response mislabeled as LIVE when no model was invoked', async () => {
    const fetcher = backend(), underlying = fetcher.getMockImplementation()!
    fetcher.mockImplementation((url, options) => url.endsWith('/execute') ? Promise.resolve(response({ status: 'LIVE', execution_mode: 'LIVE', model_invoked: false })) : underlying(url, options))
    const { w } = await setup(fetcher); await choose(w); await w.get('[data-testid="new-case-live"]').trigger('click'); await flushPromises()
    expect(w.get('[data-testid="new-case-execution-error"]').text()).toContain('没有返回经确认的本次LIVE')
    expect(w.find('[data-testid="new-case-note"]').exists()).toBe(false)
  })
  it('shows model execution provenance only from successful API output and clears one-time external consent', async () => {
    const fetcher = backend(), underlying = fetcher.getMockImplementation()!
    fetcher.mockImplementation((url, options) => url.endsWith('/execute') ? Promise.resolve(response({ status: 'LIVE', execution_mode: 'LIVE', model_invoked: true, fallback_performed: false, receipt: { provider: 'SYNTHETIC_UI_PROVIDER', model: 'SYNTHETIC_UI_MODEL', request_id: 'SYNTHETIC_UI_REQUEST', called_at: '2026-10-25T00:00:00Z' } })) : underlying(url, options))
    const { w } = await setup(fetcher); await choose(w)
    await w.get('[aria-label="本次允许图像外发"]').setValue(true)
    await w.get('[data-testid="new-case-live"]').trigger('click'); await flushPromises()
    expect(w.get('[data-testid="new-case-note"]').text()).toContain('AI结论不是独立事实认证')
    expect(w.get('[data-testid="new-case-execution"]').text()).toContain('SYNTHETIC_UI_REQUEST')
    expect(w.get('[aria-label="本次允许图像外发"]').element).toHaveProperty('checked', false)
  })
  it('shows conflicts and original reviews without voting or reducing the unknown denominator', async () => {
    const dossier = structuredClone(original)
    dossier.reviews = [{ review_id: 'synthetic-A', evidence_id: 'evidence-1', reviewer_type: 'AI', outcome: 'MATCH', observation_basis: '测试支持', uncertainty_reason: 'UNKNOWN' }, { review_id: 'synthetic-B', evidence_id: 'evidence-1', reviewer_type: 'AI', outcome: 'INCOMPARABLE', observation_basis: '测试遮挡', uncertainty_reason: 'NOT_UNIQUE' }]
    dossier.coordination_state = 'REVIEW_DISAGREEMENT'
    const { w } = await setup(backend(dossier)); await choose(w)
    expect(w.get('[data-testid="new-case-coordination"]').text()).toContain('REVIEW_DISAGREEMENT')
    expect(w.text()).toContain('自报收据不升级认证')
    expect(w.text()).toContain('NOT_UNIQUE')
    expect(w.get('[data-testid="new-case-fact"]').text()).toBe('UNKNOWN')
  })
  it('labels REPLAY as history and sends its exact saved run with version binding', async () => {
    const dossier = structuredClone(original)
    dossier.executions = [{ run_id: 'run_synthetic_saved', evidence_id: 'evidence-1', status: 'LIVE', review: { outcome: 'INCOMPARABLE' } }]
    const fetcher = backend(dossier), underlying = fetcher.getMockImplementation()!
    fetcher.mockImplementation((url, options) => url.endsWith('/execute') ? Promise.resolve(response({ status: 'REPLAY', execution_mode: 'REPLAY', model_invoked: false, fallback_performed: false })) : underlying(url, options))
    const { w } = await setup(fetcher); await choose(w); await w.get('[aria-label="选择视觉历史执行"]').setValue('run_synthetic_saved'); await w.get('[data-testid="new-case-replay"]').trigger('click'); await flushPromises()
    expect(w.get('[data-testid="new-case-note"]').text()).toContain('本次没有调用模型')
    expect(JSON.parse(String(fetcher.mock.calls.find(call => call[0].endsWith('/execute'))?.[1]?.body))).toMatchObject({ mode: 'REPLAY', run_id: 'run_synthetic_saved', asset_sha256: asset.asset_sha256 })
  })
  it('reports original image failure without inventing an observation', async () => {
    const { w } = await setup(); await choose(w); await w.get('[data-testid="new-case-evidence-image"]').trigger('error')
    expect(w.text()).toContain('原始图像未加载')
    expect(w.get('[data-testid="new-case-fact"]').text()).toBe('UNKNOWN')
  })
  it('does not offer a saved review bound to another image as a replay of the current image', async () => {
    const dossier = structuredClone(original)
    dossier.executions = [{ run_id: 'run_other_image', evidence_id: 'evidence-2', status: 'LIVE', review: { outcome: 'MATCH' } }]
    const { w } = await setup(backend(dossier)); await choose(w)
    expect(w.find('[data-testid="new-case-replay"]').exists()).toBe(false)
  })
  it('exports the selected case report and keeps task measurement available', async () => {
    const { w } = await setup(); await choose(w)
    expect(w.get('[data-testid="new-case-report-md"]').attributes('href')).toBe('/api/visual-cases/case_ui_synthetic/report?format=md')
    expect(w.get('[data-testid="new-case-report-json"]').attributes('href')).toContain('format=json')
    expect(w.text()).toContain('进入新CSV真实任务价值测量')
  })
  it('blocks registration without local authorization and required evidence', async () => {
    const { w, fetcher } = await setup()
    await w.get('[data-testid="visual-case-form"]').trigger('submit'); await flushPromises()
    expect(w.get('[data-testid="registration-error"]').text()).toContain('确认本地处理授权')
    expect(fetcher.mock.calls.some(call => call[0] === '/api/visual-cases' && call[1]?.method === 'POST')).toBe(false)
  })
  it('uploads actual selected bytes with computed SHA and explicit permissions, without self-reported receipts', async () => {
    const { w, fetcher } = await setup(); await fill(w)
    expect(w.get('[data-testid="csv-file-receipt"]').text()).toContain(createHash('sha256').update('id,label\nentity-a,stop\n').digest('hex'))
    await w.get('[data-testid="visual-case-form"]').trigger('submit'); await flushPromises()
    const call = fetcher.mock.calls.find(call => call[0] === '/api/visual-cases' && call[1]?.method === 'POST')!
    const payload = JSON.parse(String(call[1]?.body))
    expect(payload).toMatchObject({ source: { url: '', independence_declaration: 'UNKNOWN' }, authorization: { local_processing: true, external_model_upload: false }, capture_time: null })
    expect(atob(payload.dataset_csv_base64)).toBe('id,label\nentity-a,stop\n')
    expect(payload.evidence[0].sha256).toBe(createHash('sha256').update('synthetic image bytes').digest('hex'))
    expect(payload.evidence[0]).not.toHaveProperty('validation_receipt')
    expect(call[1]?.headers).toMatchObject({ 'X-Research-Request': '1' })
  })
  it('refuses too many images and clears stale uploads', async () => {
    const { w } = await setup()
    await pick(w, '[aria-label="视觉证据图像"]', Array.from({ length: 5 }, (_, i) => browserFile('image' + i + '.png', 'x')))
    expect(w.get('[data-testid="registration-error"]').text()).toContain('最多4幅')
    expect(w.find('.case-image-input').exists()).toBe(false)
  })
  it('preserves API registration rejection instead of reporting registered success', async () => {
    const fetcher = backend(), underlying = fetcher.getMockImplementation()!
    fetcher.mockImplementation((url, options) => url === '/api/visual-cases' && options?.method === 'POST' ? Promise.resolve(response({ detail: 'ASSET_VERSION_BYTES_CHANGED' }, 409)) : underlying(url, options))
    const { w } = await setup(fetcher); await fill(w); await w.get('[data-testid="visual-case-form"]').trigger('submit'); await flushPromises()
    expect(w.get('[data-testid="registration-error"]').text()).toContain('ASSET_VERSION_BYTES_CHANGED')
    expect(w.find('[data-testid="new-case-note"]').exists()).toBe(false)
  })
})

describe('new case boundary helpers', () => {
  it('rejects cross-version and cross-hash case responses', () => {
    expect(caseAsset(original)).toEqual(asset)
    expect(() => checkedCase(original, { ...asset, asset_version: 'v2' })).toThrow('阻止旧证据')
    expect(() => checkedCase(original, { ...asset, asset_sha256: 'f'.repeat(64) })).toThrow('阻止旧证据')
    expect(() => checkedCase({ case_id: 'missing-binding' })).toThrow('缺少精确版本')
    expect(readable(null)).toBe('UNKNOWN')
  })
  it('computes SHA-256 from real bytes and rejects empty, oversized and invalid extensions', async () => {
    vi.stubGlobal('crypto', webcrypto)
    const file = await fileData(browserFile('new.csv', 'abc'), 'csv')
    expect(file.sha256).toBe('ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
    expect(atob(file.data_base64)).toBe('abc')
    await expect(fileData(new File([], 'empty.csv'), 'csv')).rejects.toThrow('非空')
    await expect(fileData(browserFile('image.svg', '<svg/>'), 'image')).rejects.toThrow('PNG或JPEG')
    const huge = browserFile('huge.csv', 'x'); Object.defineProperty(huge, 'size', { value: 2 * 1024 * 1024 + 1 })
    await expect(fileData(huge, 'csv')).rejects.toThrow('2MiB')
  })
  it('encodes opaque IDs and preserves the expected binding on API reads', async () => {
    const fetcher = vi.fn(async (_url: string) => response({ ...original, asset: { ...asset, asset_version: 'v2' } })); vi.stubGlobal('fetch', fetcher)
    await expect(casesApi.get('case /?', asset)).rejects.toThrow('阻止旧证据')
    expect(fetcher.mock.calls[0][0]).toContain('/case%20%2F%3F?asset_id=')
  })
})
