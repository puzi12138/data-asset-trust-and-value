import { mount, flushPromises } from '@vue/test-utils'
import { vi, describe, it, expect, afterEach } from 'vitest'
import { request, ApiError, query, flattenReport } from '../src/api'
import { number, iso, valueBody } from '../src/utils'
import AsyncPanel from '../src/components/AsyncPanel.vue'
import StatusBadge from '../src/components/StatusBadge.vue'
import AssetSelect from '../src/components/AssetSelect.vue'
import CreditResult from '../src/components/CreditResult.vue'
import ValueResult from '../src/components/ValueResult.vue'
import Catalog from '../src/pages/Catalog.vue'
import Delivery from '../src/pages/Delivery.vue'

afterEach(() => vi.unstubAllGlobals())
const ok = (data: unknown) => ({ ok: true, status: 200, json: async () => data })

describe('API boundary and user-visible errors', () => {
  it('sends mutation marker and JSON to the same-origin backend; no keys', async () => {
    const fetcher = vi.fn(async () => ok({status:'SUCCESS'})); vi.stubGlobal('fetch',fetcher)
    await request('/deliver',{contract_id:'c1'})
    const [url,options] = fetcher.mock.calls[0] as unknown as [string, RequestInit]
    expect(url).toBe('/api/deliver');expect(options.method).toBe('POST')
    expect(options.headers).toMatchObject({'X-Research-Request':'1'})
    expect(options.body).toBe('{"contract_id":"c1"}')
  })
  it('preserves authorization rejection instead of returning success', async () => {
    vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,status:403,json:async()=>({detail:'USAGE_POLICY_DENIED_NO_CONTRACT_CREATED'})})))
    await expect(request('/access',{})).rejects.toMatchObject({status:403,message:'USAGE_POLICY_DENIED_NO_CONTRACT_CREATED'})
  })
  it('reports a network failure with an actionable local-service message', async () => {
    vi.stubGlobal('fetch',vi.fn(async()=>{throw new Error('offline')}))
    await expect(request('/dashboard')).rejects.toThrow('检查启动窗口')
  })
  it('reports invalid JSON without hiding backend status', async () => {
    vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,status:502,json:async()=>{throw new Error('HTML')}})))
    try {await request('/assets');expect.unreachable()} catch(e) {expect(e).toBeInstanceOf(ApiError);expect((e as ApiError).status).toBe(502)}
  })
  it('encodes query values and omits absent timestamps',()=>{expect(query({version:'v 1',at:undefined,q:0})).toBe('?version=v+1&q=0')})
  it('flattens saved report summaries without changing the original result',()=>{expect(flattenReport({report_id:'r',report:{status:'NOT_EVALUATED',asset_id:'new'}})).toMatchObject({report_id:'r',status:'NOT_EVALUATED',asset_id:'new'})})
})
describe('loading, empty, error and semantic states',()=>{
  it('renders actual error and retry action',async()=>{const w=mount(AsyncPanel,{props:{error:'HTTP 403'}});expect(w.get('[role=alert]').text()).toContain('HTTP 403');await w.get('button').trigger('click');expect(w.emitted('retry')).toHaveLength(1)})
  it('separates loading from empty data',()=>{expect(mount(AsyncPanel,{props:{loading:true}}).get('[role=status]').text()).toContain('正在读取');expect(mount(AsyncPanel,{props:{empty:true}}).text()).toContain('暂无记录')})
  it('does not style NOT_EVALUATED as success',()=>{const w=mount(StatusBadge,{props:{value:'NOT_EVALUATED'}});expect(w.classes()).not.toContain('success')})
  it('renders PENDING separately from FAILURE',()=>{expect(mount(StatusBadge,{props:{value:'PENDING'}}).classes()).toContain('warning');expect(mount(StatusBadge,{props:{value:'FAILURE'}}).classes()).toContain('danger')})
  it('warns when credit has only one actual successful contract',()=>{const w=mount(CreditResult,{props:{result:{q:2/3,q_lower:.158,q_upper:.987,n_history:1,successes:1,failures:0,origin:'local_operational',method:'beta'}}});expect(w.get('[data-testid=insufficient-history]').text()).toContain('1 份有效合同');expect(w.text()).toContain('不能解释为真实商业违约风险')})
  it('rejects missing exact utility without borrowing a price',()=>{const w=mount(ValueResult,{props:{report:{status:'NOT_EVALUATED',result:null,reason:'NO_EXACT_FROZEN_TASK_ASSET_VERSION_CONTENT_HASH_ECONOMICS_MATCH'}}});expect(w.get('[data-testid=not-evaluated]').text()).toContain('拒绝生成估值');expect(w.find('.big-value').exists()).toBe(false)})
  it('preserves negative economic value and shows actual utility',()=>{const w=mount(ValueResult,{props:{report:{status:'EVALUATED',conditional_utility:{result:{delta_utility:-.02}},result:{value:-12,b_success:-10,b_failure:-1,fixed_cost:3,q:.9,conditional_low:-15,conditional_high:-8,credit_n_history:1,reasons:[]}}}});expect(w.get('.big-value').text()).toContain('-12.000');expect(w.get('.value-highlight').classes()).toContain('negative');expect(w.text()).toContain('-0.020000')})
})
describe('asset and exact-version interaction',()=>{
  const assets=[{asset_id:'clean',version:'v1',provider_id:'provider_a'},{asset_id:'clean',version:'v2',provider_id:'provider_a'},{asset_id:'label_020',version:'v1',provider_id:'provider_b'}]
  it('selects only a version of the newly selected asset',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>ok({assets})))
    const w=mount(AssetSelect,{props:{modelValue:{asset_id:'clean',version:'v2'}}});await flushPromises()
    await w.get('[aria-label=数据资产]').setValue('label_020')
    expect(w.emitted('update:modelValue')?.at(-1)).toEqual([{asset_id:'label_020',version:'v1'}])
  })
  it('emits exact version changes rather than hiding the selector',async()=>{
    vi.stubGlobal('fetch',vi.fn(async()=>ok({assets})))
    const w=mount(AssetSelect,{props:{modelValue:{asset_id:'clean',version:'v1'}}});await flushPromises();await w.get('[aria-label=精确版本]').setValue('v2');expect(w.emitted('update:modelValue')?.at(-1)).toEqual([{asset_id:'clean',version:'v2'}])
  })
  it('loads selected asset detail and fetches the switched exact version',async()=>{
    const fetcher=vi.fn(async(url:string)=>url==='/api/assets'?ok({assets}):ok({asset:{asset_id:'clean',version:url.includes('v2')?'v2':'v1',schema:{age:'int64'},content_hash:'sha256:actual'},quality:null,task_measurements:[]}));vi.stubGlobal('fetch',fetcher)
    const w=mount(Catalog);await flushPromises();await w.findAll('button').find(b=>b.text()==='查看详情')!.trigger('click');await flushPromises();expect(w.text()).toContain('没有可核验的质量测量');await w.get('[aria-label=详情版本]').setValue('v2');await flushPromises();expect(fetcher.mock.calls.map(c=>c[0])).toContain('/api/assets/clean?version=v2');expect(w.text()).toContain('sha256:actual')
  })
})
describe('numeric and temporal honesty',()=>{
  it('never formats missing values as zero',()=>{expect(number(null)).toBe('—');expect(number(undefined)).toBe('—');expect(number(NaN)).toBe('—');expect(number(-1)).toBe('-1.000')})
  it('keeps selected asset/version and explicit timestamp in value context',()=>{expect(valueBody({asset_id:'x',version:'v2'},'task','econ','', 'fault_injection','beta')).toMatchObject({asset_id:'x',version:'v2',origin:'fault_injection',method:'beta',at:undefined});expect(iso('2026-10-23T12:00')).toMatch(/Z$/)})
})
describe('real delivery orchestration boundary',()=>{
  function backend() {
    const fetcher=vi.fn(async(url:string,options?:RequestInit)=>{
      if(url==='/api/assets')return ok({assets:[{asset_id:'clean',version:'v1',provider_id:'provider_a'}]})
      if(url==='/api/contracts')return ok({contracts:[]})
      if(url==='/api/access')return ok({contract:{signer_id:'registry',payload:{contract_id:'actual-contract',asset_id:'clean',asset_version:'v1'}}})
      if(url==='/api/deliver')return ok({status:'FAILURE',origin:'fault_injection',received_bytes:0,checks:{on_time:false},verifier_signature_valid:true})
      throw new Error(`Unexpected ${url} ${options?.method}`)
    });vi.stubGlobal('fetch',fetcher);return fetcher
  }
  it('starts actual backend delivery automatically for the short-deadline case',async()=>{
    const fetcher=backend(), w=mount(Delivery);await flushPromises();await w.get('[aria-label=交付模式]').setValue('timeout');await w.get('input[type=checkbox]').setValue(true)
    await w.findAll('button').find(b=>b.text().includes('请求授权合同'))!.trigger('click');await flushPromises()
    const calls=fetcher.mock.calls as unknown as [string,RequestInit][]
    expect(calls.filter(c=>c[0]==='/api/deliver')).toHaveLength(1)
    expect(calls.find(c=>c[0]==='/api/access')?.[1].body).toContain('"mode":"timeout"')
    expect(w.text()).toContain('Verifier 实际接收字节')
    expect(w.text()).toContain('FAILURE')
  })
  it('does not invent delivery success after a normal authorization contract',async()=>{
    const fetcher=backend(),w=mount(Delivery);await flushPromises();await w.findAll('button').find(b=>b.text().includes('请求授权合同'))!.trigger('click');await flushPromises()
    expect(fetcher.mock.calls.some(c=>c[0]==='/api/deliver')).toBe(false)
    expect(w.text()).toContain('第二步将取得 Provider 承诺')
  })
})
