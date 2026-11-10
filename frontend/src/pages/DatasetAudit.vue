<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { request, type Row } from '../api'
import AsyncPanel from '../components/AsyncPanel.vue'
import JsonDetails from '../components/JsonDetails.vue'
import TaskValueResearch from '../components/TaskValueResearch.vue'
const route=useRoute()
const loading=ref(false), error=ref(''), options=ref<Row>({source_reports:[],references:{}})
const selected=ref(String(route.query.report??'')), reference=ref(''), sampleSize=ref(50), seed=ref(101)
const epsilon=ref(0.05), alpha=ref(0.05), description=ref('预先固定的参考文档符合性研究；不作现实事实真实性或采购保证。')
const requireRepresentation=ref(false), frozen=ref<Row|null>(null), result=ref<Row|null>(null), file=ref<File|null>(null)
const audit=computed(()=>result.value?.audit??{})
const observations=computed(()=>result.value?.evidence?.payload?.outcomes??[])
const pct=(v:unknown)=>typeof v==='number'?(100*v).toFixed(3)+'%':'UNKNOWN'
const referenceDescription=computed(()=>options.value.references?.[reference.value]??{})
async function refresh(){try{options.value=await request('/dataset-audits/options');if(!reference.value)reference.value=Object.keys(options.value.references??{})[0]??''}catch(e){error.value=(e as Error).message}}
function changed(){frozen.value=null;result.value=null;error.value='';file.value=null}
async function freeze(){loading.value=true;error.value='';result.value=null
 try{if(!selected.value)throw new Error('请先完成一次数据集可信评估，并选择其精确版本报告。')
 frozen.value=await request('/dataset-audits/plans',{source_report_id:selected.value,reference_id:reference.value,
 sample_size:sampleSize.value,seed:seed.value,epsilon:epsilon.value,alpha:alpha.value,claim_scope:'reference_conformance',
 scenario_description:description.value,require_representativeness:requireRepresentation.value})
 result.value=frozen.value!.assessment_without_evidence
 }catch(e){error.value=(e as Error).message}finally{loading.value=false}}
function pick(event:Event){file.value=(event.target as HTMLInputElement).files?.[0]??null;error.value=''}
async function importAudit(){loading.value=true;error.value=''
 try{if(!frozen.value)throw new Error('请先冻结固定审计计划。');if(!file.value)throw new Error('请选择审计结果 JSON。')
 if(file.value.size>1048576)throw new Error('审计 JSON 超过 1 MiB。')
 const response=await fetch(`/api/dataset-audits/plans/${frozen.value.plan.plan_id}/evidence`,{method:'POST',headers:{'Content-Type':'application/json','X-Research-Request':'1'},body:file.value})
 const data=await response.json();if(!response.ok)throw new Error(String(data.detail??'审计证据导入失败'));result.value=data
 }catch(e){error.value=(e as Error).message}finally{loading.value=false}}
onMounted(refresh)
</script>
<template>
 <TaskValueResearch :source-report-id="String(route.query.report??'')"/>
 <section class="panel"><h2>先固定风险要求，再取得审计结果</h2>
  <p class="notice">判断限定于预先规定的参考、数据版本与容忍错误率。条件接受不是“绝对真实”；签名证明本地核验程序的记录绑定，不证明参考事实。A（注入）/ B（跨源比较）/ C（原始文档核对）/ SIMULATED 必须区分。</p>
  <fieldset :disabled="loading" style="border:0;padding:0;margin:0;min-width:0"><div class="form-grid">
   <label>已有可信评估报告<select v-model="selected" aria-label="审计数据集报告" @change="changed"><option value="">请先在数据集可信评估中导入或评估</option><option v-for="r in options.source_reports" :key="r.report_id" :value="r.report_id">{{r.dataset.name??r.dataset.dataset_id}} / {{r.dataset.version}} · {{r.dataset.row_count}}行</option></select></label>
   <label>本地预配审计参考<select v-model="reference" aria-label="审计参考" @change="changed"><option v-for="(r,id) in options.references" :key="id" :value="id">{{id}} · {{r.evidence_class}}</option></select></label>
   <label>固定抽样条数 n<input v-model.number="sampleSize" aria-label="固定审计样本量" type="number" min="1" max="20000" @input="changed"></label>
   <label>随机种子<input v-model.number="seed" aria-label="审计随机种子" type="number" min="0" max="4294967295" @input="changed"></label>
   <label>最大容忍错误率 ε<input v-model.number="epsilon" aria-label="最大容忍错误率" type="number" step="0.001" min="0.001" max="0.999" @input="changed"></label>
   <label>不确定性风险参数 α<input v-model.number="alpha" aria-label="审计风险参数" type="number" step="0.001" min="0.001" max="0.499" @input="changed"></label>
  </div><label>预定业务场景<input v-model="description" aria-label="预定审计场景" maxlength="500" @input="changed"></label>
  <label><input v-model="requireRepresentation" aria-label="要求代表性证据" type="checkbox" @change="changed"> 使用决策同时要求代表性证据（当前缺少总体参考时保持证据不足）</label>
  <p class="footnote">本地上传不建立可信参考。风险要求和抽样位置一旦冻结不能修改；更改要求会创建新计划，不能据结果挑选最有利参数。固定预算不支持可选停止。</p>
  <div class="actions"><button class="button" @click="freeze">冻结审计计划</button><button class="button secondary" @click="refresh">刷新已有报告</button></div></fieldset>
  <JsonDetails :data="referenceDescription" title="参考强度、可靠性依据与适用范围"/>
 </section>
 <AsyncPanel :loading="loading" :error="error" @retry="freeze">
  <section v-if="frozen" class="panel" data-testid="audit-plan"><div class="panel-heading"><h2>固定计划与审计证据</h2><a class="button secondary" :href="`/api/dataset-audits/plans/${frozen.plan.plan_id}/export`" download>导出固定计划 JSON</a></div>
   <dl class="details-grid"><dt>计划ID</dt><dd>{{frozen.plan.plan_id}}</dd><dt>事前要求</dt><dd>ε={{pct(frozen.plan.epsilon)}} · α={{pct(frozen.plan.alpha)}} · n={{frozen.plan.sample_size}}</dd></dl>
   <JsonDetails :data="frozen.plan" title="计划哈希、固定位置和抽样机制"/>
   <p class="notice warning">请由审计人员或单独的本地核验程序完成所选位置检查，再导入签名结果。网页不持有审计私钥，不提供“点击全部通过”。未签名声明保留为证据不足；无效签名或错误版本不能采信。</p>
   <div class="form-grid"><label>审计结果 JSON（≤1 MiB）<input aria-label="审计证据JSON" type="file" accept=".json,application/json" @change="pick"></label></div>
   <div class="actions"><button class="button" :disabled="loading" @click="importAudit">导入并核验审计证据</button></div>
  </section>
  <template v-if="result"><section class="panel" data-testid="audit-decision"><div class="panel-heading"><h2>风险约束判断</h2><div class="actions"><a class="button secondary" :href="`/api/dataset-audits/reports/${result.report_id}/export`" download>导出审计报告 JSON</a><a class="button secondary" :href="`/api/dataset-audits/reports/${result.report_id}/print`" target="_blank" rel="noopener">打开审计打印报告</a></div></div>
   <dl class="details-grid"><dt>内容条件判断</dt><dd><strong data-testid="content-decision">{{audit.content_decision??'UNKNOWN'}}</strong></dd><dt>整体适用判断</dt><dd><strong data-testid="overall-decision">{{audit.decision??'UNKNOWN'}}</strong></dd><dt>有效可比数 n / 已确认错误 e</dt><dd data-testid="audit-counts">{{audit.n??0}} / {{audit.e??0}} · 未解决 {{audit.unresolved??'UNKNOWN'}} / 固定样本 {{audit.sample_size}}</dd><dt>内容错误率区间</dt><dd data-testid="audit-interval">{{audit.error_rate_interval?'['+pct(audit.error_rate_interval.lower)+', '+pct(audit.error_rate_interval.upper)+']':'UNKNOWN · 尚无可采信的抽样推断'}}</dd><dt>适用范围</dt><dd>{{audit.claim_scope}} · {{audit.simulation_only?'仅模拟机制验证':'参照范围内的条件符合性'}}</dd><dt>价值关联</dt><dd>{{result.value.status}} · trust_multiplier_applied=false</dd></dl>
   <div class="notice warning"><strong>判断原因</strong><ul><li v-for="(reason,i) in audit.reasons" :key="i">{{reason}}</li><li v-for="(reason,i) in audit.content_reasons" :key="'content'+i">{{reason}}</li></ul></div>
   <h3>统计假设</h3><ul><li v-for="(assumption,i) in audit.assumptions" :key="i">{{assumption}}</li></ul>
   <p class="notice">任何新CSV没有有效审计仍为未知。内容满足容忍率不代表具有目标总体代表性，也不替代任务效用。Bank仅精确版本关联既有任务测量；Wine和新CSV没有新任务实验时仍 NOT_EVALUATED。</p>
   <JsonDetails :data="audit" title="实际 n / e、错误率界、原因、假设和证据状态" open/>
   <JsonDetails :data="result.trust_context_public" title="本地受信公钥与参考配置（不含私钥）"/>
   <JsonDetails :data="result.dataset_assessment" title="五维指标、证据充分性与准入门禁"/>
  </section>
  <section v-if="observations.length" class="panel"><h2>样本核验结果</h2><p class="footnote">位置为从0开始的固定数据行位置。页面仅展示实际导入观测，不自行改变算法结论。</p><div class="table-wrap"><table style="min-width:600px"><thead><tr><th>位置</th><th>核验结果</th><th>证据定位</th></tr></thead><tbody><tr v-for="(o,i) in observations" :key="i"><td>{{o.position}}</td><td>{{o.outcome}}</td><td style="overflow-wrap:anywhere">{{o.evidence_locator??'未提供'}}</td></tr></tbody></table></div></section></template>
 </AsyncPanel>
</template>
<style scoped>
/* Long immutable IDs and machine-readable reason codes remain readable on
   narrow screens; the evidence table keeps its own horizontal scroll. */
.panel { overflow-wrap: anywhere; }
.details-grid dd { min-width: 0; overflow-wrap: anywhere; }
</style>
