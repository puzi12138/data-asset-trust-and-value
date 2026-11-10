import { mount, flushPromises } from '@vue/test-utils'
import { vi, describe, it, expect, afterEach } from 'vitest'
import DatasetTrust from '../src/pages/DatasetTrust.vue'
const ok=(data:unknown)=>({ok:true,status:200,json:async()=>data})
const report={report_id:'r1',dataset:{dataset_id:'local-a',version:'v1',row_count:2,column_count:2,content_hash:'sha256:abc',schema:[]},assessment:{dimensions:{accuracy:{score:null,status:'UNKNOWN',evidence_class:'missing',meaning:'External evidence needed'}},fusion:{status:'REFUSED',point_index:null,interval_lower:0,interval_upper:1,evidence_coverage:0,observed_projection:null,refusal_reasons:['NO_FACTUAL_AUDIT']},baselines:{},risks:[],missing_evidence:[]},value:{status:'NOT_EVALUATED',reason:'LOCAL_IMPORT_HAS_NO_EXACT_TASK_MEASUREMENT'}}
afterEach(()=>vi.unstubAllGlobals())
describe('dataset trust entry',()=>{
 it('explains limits and unknowns without giving a default score',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>ok({tasks:[]})))
  const wrapper=mount(DatasetTrust);await flushPromises()
  expect(wrapper.text()).toContain('签名只能验证来源绑定与完整性')
  expect(wrapper.text()).toContain('2 MiB / 20,000 行 / 128 字段')
  expect(wrapper.find('[data-testid="dataset-assessment"]').exists()).toBe(false)
 })
 it('requires a selected file and renders a visible error',async()=>{
  vi.stubGlobal('fetch',vi.fn(async()=>ok({tasks:[]})))
  const wrapper=mount(DatasetTrust);await flushPromises()
  await wrapper.get('button').trigger('click');await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('请先选择')
 })
 it('posts raw CSV only to the local endpoint then keeps unknown and value separate',async()=>{
  const fetcher=vi.fn(async(path:string)=>path==='/api/tasks'?ok({tasks:[]}):path.startsWith('/api/datasets/import')?ok({dataset_id:'local-a'}):ok(report))
  vi.stubGlobal('fetch',fetcher)
  const wrapper=mount(DatasetTrust);await flushPromises()
  const input=wrapper.get('input[type="file"]')
  Object.defineProperty(input.element,'files',{value:[new File(['a,b\n1,2\n'],'sample.csv',{type:'text/csv'})]})
  await input.trigger('change');await wrapper.get('button').trigger('click');await flushPromises()
  expect(wrapper.text()).toContain('REFUSED');expect(wrapper.text()).toContain('UNKNOWN')
  expect(wrapper.text()).toContain('NOT_EVALUATED');expect(wrapper.text()).toContain('可信指数不乘以价值')
  const call=fetcher.mock.calls.find(([p])=>p.startsWith('/api/datasets/import')) as unknown as [string,RequestInit]
  expect(call[1].headers).toMatchObject({'Content-Type':'text/csv','X-Research-Request':'1'})
  expect(call[1].body).toBeInstanceOf(File)
  expect(wrapper.get('a[download]').attributes('href')).toBe('/api/datasets/reports/r1/export')
  await wrapper.get('input[aria-label="来源声明"]').setValue('Different declaration after assessment')
  expect(wrapper.find('[data-testid="dataset-assessment"]').exists()).toBe(false)
 })
 it('displays actual server rejection without inventing a result',async()=>{
  vi.stubGlobal('fetch',vi.fn(async(path:string)=>path==='/api/tasks'?ok({tasks:[]}):({ok:false,status:400,json:async()=>({detail:'CSV_EMPTY_DUPLICATE_OR_LONG_COLUMN_NAME'})})))
  const wrapper=mount(DatasetTrust);await flushPromises()
  const input=wrapper.get('input[type="file"]');Object.defineProperty(input.element,'files',{value:[new File(['a,a\n1,2\n'],'bad.csv')]})
  await input.trigger('change');await wrapper.get('button').trigger('click');await flushPromises()
  expect(wrapper.get('[role="alert"]').text()).toContain('CSV_EMPTY_DUPLICATE')
  expect(wrapper.find('[data-testid="dataset-assessment"]').exists()).toBe(false)
 })
 it('switching input mode clears an earlier result',async()=>{
  vi.stubGlobal('fetch',vi.fn(async(path:string)=>ok(path==='/api/assets'?{assets:[]}:{tasks:[]})))
  const wrapper=mount(DatasetTrust);await flushPromises()
  await wrapper.get('select[aria-label="可信评估输入方式"]').setValue('asset');await flushPromises()
  expect(wrapper.text()).toContain('先通过原有“授权与交付”')
  expect(wrapper.find('input[type="file"]').exists()).toBe(false)
 })
})
