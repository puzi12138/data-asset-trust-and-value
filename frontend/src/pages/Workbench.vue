<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { request, type Row } from '../api'
import AsyncPanel from '../components/AsyncPanel.vue'
import JsonDetails from '../components/JsonDetails.vue'
const options=ref<Row>({measured:[],strategies:[]}),kind=ref('import'),identity=ref(''),budget=ref(50),strategy=ref('candidate')
const result=ref<Row|null>(null),error=ref(''),loading=ref(false),file=ref<File|null>(null),imported=ref<Row|null>(null)
const labels:Record<string,string>={srs:'简单随机',fixed_random:'固定随机（与SRS相同）',proportional:'固定比例分层',risk_priority:'固定风险优先',candidate:'试点—独立确认预算策略'}
const pct=(value:unknown)=>typeof value==='number'?(100*value).toFixed(3)+'%':'UNKNOWN'
function reset(){result.value=null;error.value=''}
function changeMode(){identity.value=kind.value==='measured'?(options.value.measured[0]?.identity??''):'';reset()}
async function refresh(){try{options.value=await request('/workbench/options');changeMode()}catch(e){error.value=(e as Error).message}}
function pick(event:Event){file.value=(event.target as HTMLInputElement).files?.[0]??null;imported.value=null;reset()}
async function assess(){loading.value=true;error.value='';result.value=null
try{if(kind.value==='import'){
 if(!file.value)throw new Error('请选择本地CSV。')
 if(file.value.size>2097152)throw new Error('CSV超过2 MiB，未上传。')
 if(!imported.value){const response=await fetch('/api/datasets/import?name='+encodeURIComponent(file.value.name),{method:'POST',headers:{'Content-Type':'text/csv','X-Research-Request':'1'},body:file.value});const body=await response.json();if(!response.ok)throw new Error(String(body.detail));imported.value=body}
 identity.value=imported.value!.dataset_id
 }
 if(!identity.value)throw new Error('请选择一个数据集或精确任务资产。')
 result.value=await request('/workbench/assess',{kind:kind.value,identity:identity.value,budget:budget.value,seed:101,strategy:strategy.value})
}catch(e){error.value=(e as Error).message}finally{loading.value=false}}
onMounted(refresh)
</script>
<template>
<section class="panel workbench"><h2>一次评估，分开解释可信证据和任务价值</h2>
 <p class="notice warning">本地CSV缺少独立参考或来源核验时，相关事实状态保持UNKNOWN；结构检测不能替代内容真实性核验或任务价值测量。</p>
 <fieldset :disabled="loading" style="border:0;padding:0;margin:0;min-width:0"><div class="form-grid">
 <label>数据入口<select v-model="kind" aria-label="工作台数据入口" @change="changeMode"><option value="import">本地CSV（无独立参考）</option><option value="measured">已有精确任务资产</option></select></label>
 <label v-if="kind==='measured'">精确任务资产<select v-model="identity" aria-label="工作台精确任务资产" @change="reset"><option v-for="r in options.measured" :key="r.identity" :value="r.identity">{{r.asset_id}} · {{r.buyer_id}} · {{r.distribution_id}}</option></select></label>
 <label v-if="kind==='import'">本地CSV文件<input aria-label="工作台本地CSV" type="file" accept=".csv,text/csv" @change="pick"></label>
 <label>单次固定审计预算<select v-model.number="budget" aria-label="工作台审计预算" @change="reset"><option v-for="n in [20,50,100]" :key="n" :value="n">{{n}}条（小资产最多核验全部记录）</option></select></label>
 <label>预算策略<select v-model="strategy" aria-label="工作台审计策略" @change="reset"><option v-for="s in options.strategies" :key="s" :value="s">{{labels[s]??s}}</option></select></label>
 </div><p class="footnote">CSV只存本机；限UTF-8、2 MiB、20,000行、128列。固定ε=5%、α=5%、seed=101；每次预算是独立敏感性情境，不可看完结果再择优停止。前端不计算区间或修改统计结论。</p>
 <button class="button" @click="assess">执行联合评估</button></fieldset>
</section>
<AsyncPanel :loading="loading" :error="error" @retry="assess"><template v-if="result">
 <section class="panel workbench" data-testid="workbench-result"><div class="panel-heading"><h2>固定版本与结构质量</h2><div class="actions"><a class="button secondary" :href="`/api/workbench/reports/${result.report_id}/export`" download>导出联合JSON</a><a class="button secondary" :href="`/api/workbench/reports/${result.report_id}/markdown`" download>导出中文研究报告</a></div></div>
 <dl class="details-grid"><dt>数据集/版本</dt><dd>{{result.dataset.dataset_id}} / {{result.dataset.version}}</dd><dt>SHA-256</dt><dd data-testid="workbench-hash">{{result.dataset.content_hash}}</dd><dt>规模</dt><dd>{{result.dataset.row_count}}行 × {{result.dataset.column_count}}列</dd><dt>来源证据</dt><dd data-testid="workbench-source">{{result.source_evidence.status}} · 事实核验=false</dd></dl>
 <JsonDetails :data="result.dataset.schema" title="数据Schema"/><JsonDetails :data="result.assessment.dimensions.structure" title="实际测量的结构质量" open/>
 </section>
 <section class="panel workbench"><h2>预算、样本与条件判断</h2><dl class="details-grid"><dt>内容参照判断</dt><dd data-testid="workbench-content">{{result.audit.content_decision}}</dd><dt>来源约束后判断</dt><dd data-testid="workbench-decision">{{result.audit.decision}}</dd><dt>可比/分歧/未知</dt><dd data-testid="workbench-counts">{{result.audit.n}} / {{result.audit.e}} / {{result.audit.unresolved}}</dd><dt>实际成本</dt><dd data-testid="workbench-cost">{{result.audit.audit_cost}} 条</dd><dt>错误率区间</dt><dd data-testid="workbench-interval">{{result.audit.error_rate_interval?'['+pct(result.audit.error_rate_interval.lower)+', '+pct(result.audit.error_rate_interval.upper)+']':'UNKNOWN'}}</dd></dl>
 <p class="notice">本区间只在固定抽样设计、比较规则和参照范围下成立。签名/哈希不验证现实内容；时间与代表性缺证不会被结构高分填补。</p>
 <JsonDetails :data="result.audit" title="完整预算分配、样本位置、理由、统计假设" open/>
 </section>
 <section class="panel workbench"><h2>条件任务价值与联合建议</h2>
 <dl class="details-grid"><dt>精确任务价值</dt><dd data-testid="workbench-value">{{result.value.status}}</dd><dt>正式用途</dt><dd data-testid="workbench-formal">{{result.joint_decision.formal_use}}</dd><dt>探索性建议</dt><dd data-testid="workbench-joint">{{result.joint_decision.exploratory_use}}</dd><dt>货币价格</dt><dd>NOT_EVALUATED · trust_multiplier_applied=false</dd></dl>
 <p v-if="result.value.record" data-testid="workbench-utility">验证集ΔU={{result.value.record.validation.mean_utility}}，区间[{{result.value.record.validation.ci_lower}}, {{result.value.record.validation.ci_upper}}] {{result.value.record.loss_unit}}。测试结果仅事后评价，不能据此改推荐。</p>
 <p v-else class="notice" data-testid="workbench-unmeasured">没有该精确版本、任务、买方与协议的测量，保持NOT_EVALUATED，不借其他数据集的值。</p>
 <JsonDetails :data="result.value" title="九键绑定、条件价值与事后测试"/><JsonDetails :data="result.assessment" title="五维可信证据、缺失与局限"/>
 </section>
</template></AsyncPanel>
</template>
<style scoped>.workbench{overflow-wrap:anywhere}.workbench dd{min-width:0;overflow-wrap:anywhere}</style>
