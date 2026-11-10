<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request, type Row } from '../api'
import JsonDetails from './JsonDetails.vue'
const props=defineProps<{sourceReportId?:string}>()
const loading=ref(false),error=ref(''),options=ref<Row>({records:[],source_reports:[],registry:{}})
const selected=ref(''),source=ref(props.sourceReportId??''),result=ref<Row|null>(null)
const records=computed(()=>options.value.records??[])
const selectedRow=computed(()=>records.value[Number(selected.value)]??null)
const validation=computed(()=>result.value?.record?.validation??{})
const test=computed(()=>result.value?.record?.test_posthoc??{})
const referenceDiagnostic=computed(()=>result.value?.record?.trust?.known_change_reference_diagnostic??null)
const metric=(x:unknown)=>typeof x==='number'?x.toFixed(6):'NOT_EVALUATED'
const keys=['asset_id','version','content_hash','task_id','model_id','buyer_id','scenario_id','distribution_id','protocol_hash']
function changed(){result.value=null;error.value=''}
async function refresh(){loading.value=true;error.value='';try{options.value=await request('/task-values/options');if(!selected.value&&options.value.records?.length)selected.value='0'}catch(e){error.value=(e as Error).message}finally{loading.value=false}}
async function lookup(){loading.value=true;error.value='';result.value=null;try{
 if(!selectedRow.value)throw new Error('尚无精确版本的任务价值实验，请先完成冻结研究。')
 const query:Row=Object.fromEntries(keys.map(k=>[k,selectedRow.value[k]]))
 if(source.value)query.source_report_id=source.value
 result.value=await request('/task-values/lookup',query)
 }catch(e){error.value=(e as Error).message}finally{loading.value=false}}
onMounted(refresh)
</script>
<template>
 <section class="panel task-value-research" data-testid="task-value-research"><div class="panel-heading"><h2>场景化任务价值研究</h2><button class="button secondary" :disabled="loading" @click="refresh">刷新任务测量</button></div>
  <p class="notice">价值限定于数据资产精确版本、买方已有数据、任务、学习器及评估分布。验证集用于选择；测试集仅作冻结后的事后评价。分类 Log Loss 与回归 MAE 的量纲不同，不能直接比较经济价值。</p>
  <fieldset :disabled="loading" style="border:0;padding:0;min-width:0"><div class="form-grid">
   <label>已运行的精确场景<select v-model="selected" aria-label="精确任务价值场景" @change="changed"><option v-if="!records.length" value="">NOT_EVALUATED · 尚无可采信任务测量</option><option v-for="(r,i) in records" :key="i" :value="String(i)">{{r.asset_id}} / {{r.buyer_id}} / {{r.model_id}} / {{r.distribution_id}}</option></select></label>
   <label>可选：与本地数据报告核对<select v-model="source" aria-label="任务价值源数据核对" @change="changed"><option value="">仅查看保存的研究记录</option><option v-for="r in options.source_reports" :key="r.report_id" :value="r.report_id">{{r.dataset.name??r.dataset.dataset_id}} / {{r.dataset.version}}</option></select></label>
  </div><p class="footnote">源报告核对同时要求 asset_id、version、SHA-256 相同。新导入CSV即使字段相似，也不继承旧测量；页面不能上传效用值或修改统计状态。</p>
  <button class="button" :disabled="loading" @click="lookup">查看精确任务价值</button></fieldset>
  <p v-if="error" role="alert" class="notice warning">{{error}}</p>
  <p v-if="!records.length&&!loading" data-testid="value-registry-unavailable">NOT_EVALUATED · {{options.registry?.status??'UNKNOWN'}}</p>
  <template v-if="result"><div data-testid="task-value-result"><h3>验证集价值证据 <strong data-testid="task-value-status">{{result.status}}</strong></h3>
   <p v-if="!result.record" class="notice warning" data-testid="task-value-unmatched">{{result.reason}}。没有精确任务测量，不借用其他数据集结果。</p>
   <template v-else><dl class="details-grid"><dt>任务和损失单位</dt><dd data-testid="task-value-unit">{{result.record.task_type}} / {{result.record.loss_name}} / {{result.record.loss_unit}}</dd><dt>验证集配对效用</dt><dd data-testid="validation-utility">{{metric(validation.mean_utility)}}；区间 [{{metric(validation.ci_lower)}}, {{metric(validation.ci_upper)}}]</dd><dt>测试集事后效用</dt><dd data-testid="test-posthoc-utility">{{metric(test.mean_utility)}}（不能据此改变推荐）</dd><dt>真实可信要求</dt><dd data-testid="value-trust-decision">{{result.record.trust.decision}} / {{result.record.trust.evidence_class}}</dd><dt>经济估价</dt><dd>NOT_EVALUATED · trust_multiplier_applied=false</dd></dl>
   <p class="notice warning">新研究资产缺少独立事实审计时，正式可信条件保持 NEED_MORE_EVIDENCE。已知注入参照符合性仅供机制验证，不是事实真实性认证。高任务效用不能倒推出高可信。</p>
   <p v-if="referenceDiagnostic" class="notice" data-testid="reference-gate-diagnostic">A类已知变化参照机制：{{referenceDiagnostic.statistical_decision}}；n={{referenceDiagnostic.n}}，e={{referenceDiagnostic.e}}。mechanism_only=true；factual_truth_verified=false；signature_verified=false。</p>
   <div class="actions"><a class="button secondary" :href="`/api/task-values/reports/${result.report_id}/export`" download>导出任务价值 JSON</a><a class="button secondary" :href="`/api/task-values/reports/${result.report_id}/print`" target="_blank" rel="noopener">打开任务价值打印报告</a></div>
   <h3>相同候选集下的选择与事后评价</h3><p class="footnote">所有可部署建议仅依据冻结验证集。测试oracle是不可部署的事后比较。A参照门禁与正式事实审计分开。</p>
   <JsonDetails :data="result.comparisons" title="候选版本、同信息基线、选择结果与test oracle"/>
   <JsonDetails :data="result.record.validation" title="验证集区间、状态及计算依据"/>
   <JsonDetails :data="result.record.test_posthoc" title="测试集事后指标（不用于选择）"/>
   <JsonDetails :data="result.query" title="九项精确键及协议SHA-256"/>
   <JsonDetails :data="result.limitations" title="统计假设、参考强度与经济边界"/>
   </template></div></template>
 </section>
</template>
<style scoped>
.task-value-research{overflow-wrap:anywhere}.details-grid dd{min-width:0;overflow-wrap:anywhere}select{max-width:100%}
</style>
