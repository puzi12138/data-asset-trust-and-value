import { mount, flushPromises } from '@vue/test-utils'
import { vi, describe, it, expect, afterEach } from 'vitest'
import DatasetAudit from '../src/pages/DatasetAudit.vue'
// The inherited audit unit tests isolate the new child. Its real interactions
// are tested in task-value.test.ts and the browser integration suite.
vi.mock('../src/components/TaskValueResearch.vue',()=>({default:{template:'<div />'}}))
vi.mock('vue-router',()=>({useRoute:()=>({query:{}})}))
const ok=(data:unknown)=>({ok:true,status:200,json:async()=>data})
const settings={source_reports:[{report_id:'source1',dataset:{dataset_id:'local-1',name:'local.csv',version:'v1',row_count:100}}],references:{research_simulated:{evidence_class:'SIMULATED',supported_scopes:['reference_conformance'],reliability_basis:'Only synthetic mechanics'}}}
const plan={plan_id:'plan-1',sample_size:50,epsilon:.05,alpha:.05,positions:[0,3],plan_hash:'sha256:abc'}
const unknown={report_id:'report1',dataset_assessment:{source:'SYNTHETIC_ASSESSMENT_FIXTURE'},audit:{decision:'NEED_MORE_EVIDENCE',content_decision:'NEED_MORE_EVIDENCE',n:0,e:0,sample_size:50,error_rate_interval:null,reasons:['AUDIT_EVIDENCE_MISSING'],assumptions:['Fixed SRS, no optional stopping']},value:{status:'NOT_EVALUATED'}}
const accepted={...unknown,report_id:'report2',audit:{...unknown.audit,decision:'NEED_MORE_EVIDENCE',content_decision:'ACCEPT_CONDITIONALLY',n:50,e:0,error_rate_interval:{lower:0,upper:.04},simulation_only:true},evidence:{payload:{outcomes:[{position:0,outcome:'PASS',evidence_locator:'synthetic:declared'}]}}}
afterEach(()=>vi.unstubAllGlobals())
async function prepare(fetcher:ReturnType<typeof vi.fn>){vi.stubGlobal('fetch',fetcher);const wrapper=mount(DatasetAudit);await flushPromises();return wrapper}
describe('risk constrained dataset audit',()=>{
 it('states evidence boundaries and has no fabricated initial decision',async()=>{
  const wrapper=await prepare(vi.fn(async()=>ok(settings)))
  expect(wrapper.text()).toContain('条件接受不是“绝对真实”')
  expect(wrapper.text()).toContain('本地上传不建立可信参考')
  expect(wrapper.find('[data-testid="audit-decision"]').exists()).toBe(false)
 })
 it('requires a real source report before generating a plan',async()=>{
  const wrapper=await prepare(vi.fn(async()=>ok(settings)))
  await wrapper.get('button').trigger('click');await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('请先完成一次数据集可信评估')
 })
 it('freezes epsilon alpha budget and displays missing evidence honestly',async()=>{
  const fetcher=vi.fn(async(path:string)=>path==='/api/dataset-audits/options'?ok(settings):ok({plan,assessment_without_evidence:unknown}))
  const wrapper=await prepare(fetcher)
  await wrapper.get('[aria-label="审计数据集报告"]').setValue('source1')
  await wrapper.get('button').trigger('click');await flushPromises()
  expect(wrapper.get('[data-testid="overall-decision"]').text()).toBe('NEED_MORE_EVIDENCE')
  expect(wrapper.get('[data-testid="audit-interval"]').text()).toContain('UNKNOWN')
  expect(wrapper.text()).toContain('SYNTHETIC_ASSESSMENT_FIXTURE')
  const call=fetcher.mock.calls.find(([path])=>path==='/api/dataset-audits/plans') as unknown as [string,RequestInit]
  expect(JSON.parse(String(call[1].body))).toMatchObject({epsilon:.05,alpha:.05,sample_size:50,reference_id:'research_simulated'})
  expect(wrapper.get('a[download]').attributes('href')).toContain('/plans/plan-1/export')
 })
 it('uses actual backend counts intervals and separate content vs overall decision',async()=>{
  const fetcher=vi.fn(async(path:string)=>path==='/api/dataset-audits/options'?ok(settings):path.endsWith('/evidence')?ok(accepted):ok({plan,assessment_without_evidence:unknown}))
  const wrapper=await prepare(fetcher);await wrapper.get('[aria-label="审计数据集报告"]').setValue('source1');await wrapper.get('button').trigger('click');await flushPromises()
  const input=wrapper.get('input[type="file"]');Object.defineProperty(input.element,'files',{value:[new File(['{}'],'audit.json',{type:'application/json'})]})
  await input.trigger('change');await wrapper.findAll('button').find(b=>b.text()==='导入并核验审计证据')!.trigger('click');await flushPromises()
  expect(wrapper.get('[data-testid="content-decision"]').text()).toBe('ACCEPT_CONDITIONALLY')
  expect(wrapper.get('[data-testid="overall-decision"]').text()).toBe('NEED_MORE_EVIDENCE')
  expect(wrapper.get('[data-testid="audit-counts"]').text()).toContain('50 / 0')
  expect(wrapper.get('[data-testid="audit-interval"]').text()).toContain('4.000%')
  expect(wrapper.text()).toContain('仅模拟机制验证');expect(wrapper.text()).toContain('synthetic:declared')
  expect(wrapper.text()).toContain('NOT_EVALUATED')
 })
 it('parameter edit invalidates displayed prior result and requires a new plan',async()=>{
  const wrapper=await prepare(vi.fn(async(path:string)=>path==='/api/dataset-audits/options'?ok(settings):ok({plan,assessment_without_evidence:unknown})))
  await wrapper.get('[aria-label="审计数据集报告"]').setValue('source1');await wrapper.get('button').trigger('click');await flushPromises()
  expect(wrapper.find('[data-testid="audit-plan"]').exists()).toBe(true)
  await wrapper.get('[aria-label="最大容忍错误率"]').setValue(.1)
  expect(wrapper.find('[data-testid="audit-plan"]').exists()).toBe(false)
  expect(wrapper.find('[data-testid="audit-decision"]').exists()).toBe(false)
 })
 it('surfaces invalid evidence response without computing a browser score',async()=>{
  const wrapper=await prepare(vi.fn(async(path:string)=>path==='/api/dataset-audits/options'?ok(settings):({ok:false,status:400,json:async()=>({detail:'SOURCE_DATASET_BYTES_CHANGED'})})))
  await wrapper.get('[aria-label="审计数据集报告"]').setValue('source1');await wrapper.get('button').trigger('click');await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('SOURCE_DATASET_BYTES_CHANGED')
  expect(wrapper.find('[data-testid="audit-decision"]').exists()).toBe(false)
 })
})
