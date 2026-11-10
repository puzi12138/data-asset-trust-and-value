<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { Row } from '../api'
import { measurementApi, type MeasurementDataset } from '../selfService'
import { evidenceApi, valueStateText, type EvidencePlanResponse, type EvidenceReportResponse, type JointReportResponse } from '../evidence'
import JsonDetails from './JsonDetails.vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{ candidate: MeasurementDataset | null; runId?: string; featureColumns?: string[]; disabled?: boolean }>()
const emit = defineEmits<{ busy: [value: boolean] }>()
const file = ref<File | null>(null), separator = ref(','), reference = ref<MeasurementDataset | null>(null)
const keyRows = reactive([{ candidate: '', reference: '' }])
const rules = reactive([{ candidate_column: '', reference_column: '', comparison: 'exact', atol: 0, rtol: 0 }])
const source = reactive({ description: '', source_url: '', license: '', observed_at: '', version: '', relationship: 'UNKNOWN' })
const sampleSize = ref(50), seed = ref(101), epsilon = ref(.05), alpha = ref(.05), samplingMethod = ref('SRSWOR')
const auxiliaryColumns = ref<string[]>([])
const frozen = ref<EvidencePlanResponse | null>(null), evaluated = ref<EvidenceReportResponse | null>(null), combined = ref<JointReportResponse | null>(null)
const busy = ref(false), error = ref('')
const locked = computed(() => busy.value || props.disabled)
const candidateColumns = computed(() => props.candidate?.columns ?? [])
const referenceColumns = computed(() => reference.value?.columns ?? [])
const audit = computed(() => evaluated.value?.report ?? null)
const joint = computed(() => combined.value?.report ?? null)
const observations = computed<Row[]>(() => audit.value?.observations ?? [])
const coverage = computed(() => {
  const counts = audit.value?.counts
  return counts && typeof counts.comparable === 'number' && typeof counts.sample_size === 'number' && counts.sample_size > 0
    ? `${counts.comparable} / ${counts.sample_size}（${pct(counts.comparable / counts.sample_size)}，固定样本可比覆盖）` : 'UNKNOWN · 无可比覆盖比例'
})
const pct = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? `${(100 * value).toFixed(3)}%` : 'UNKNOWN'
const shown = (value: unknown) => value === null || value === undefined ? 'UNKNOWN' : typeof value === 'object' ? JSON.stringify(value) : String(value)
watch(busy, value => emit('busy', value), { flush: 'sync' })
function invalidate() { frozen.value = null; evaluated.value = null; combined.value = null; error.value = '' }
function invalidateJoint() { combined.value = null; error.value = '' }
function changeSampling() { if (samplingMethod.value === 'CENSUS' && props.candidate) sampleSize.value = props.candidate.row_count; invalidate() }
function duplicateText(value: unknown) {
  if (!value || typeof value !== 'object') return 'UNKNOWN'
  const row = value as Row
  return row.status === 'MEASURED' ? `额外重复 ${shown(row.excess_duplicate_rows)} 行；重复组内 ${shown(row.rows_in_duplicate_groups)} 行；${shown(row.duplicate_group_count)}组；额外重复率 ${pct(row.excess_duplicate_fraction)}` : `${shown(row.status)} · ${shown(row.reason)}`
}
watch(() => [props.candidate?.dataset_id, props.candidate?.version, props.candidate?.content_hash].join('|'), () => {
  invalidate(); auxiliaryColumns.value = []
  keyRows.splice(0, keyRows.length, { candidate: '', reference: '' })
  rules.splice(0, rules.length, { candidate_column: '', reference_column: '', comparison: 'exact', atol: 0, rtol: 0 })
})
watch(() => props.runId, invalidateJoint)
watch(() => props.featureColumns, invalidateJoint, { deep: true })
function pick(event: Event) { file.value = (event.target as HTMLInputElement).files?.[0] ?? null; reference.value = null; invalidate() }
async function upload() {
  if (!file.value) { error.value = '请选择参考CSV。'; return }
  busy.value = true; invalidate()
  try { reference.value = await measurementApi.upload(file.value, separator.value) }
  catch (e) { error.value = (e as Error).message } finally { busy.value = false }
}
async function freeze() {
  error.value = ''
  if (!props.candidate || !reference.value) { error.value = '请先导入同页候选CSV和参考CSV。'; return }
  if (keyRows.some(row => !row.candidate || !row.reference)) { error.value = '请完整选择候选键与参考键。'; return }
  if (new Set(keyRows.map(row => row.candidate)).size !== keyRows.length || new Set(keyRows.map(row => row.reference)).size !== keyRows.length) { error.value = '键映射不能重复。'; return }
  if (rules.some(row => !row.candidate_column || !row.reference_column || !Number.isFinite(row.atol) || row.atol < 0 || !Number.isFinite(row.rtol) || row.rtol < 0)) { error.value = '请完整选择字段规则，数值容差需要非负有限值。'; return }
  if (!Number.isSafeInteger(sampleSize.value) || sampleSize.value < 1 || !Number.isSafeInteger(seed.value) || seed.value < 0 || seed.value > 4294967295) { error.value = '样本量需为正整数，种子需为0至4294967295的整数。'; return }
  if (sampleSize.value > props.candidate.row_count) { error.value = '预定样本量不能超过候选数据行数。'; return }
  if (!(epsilon.value > 0 && epsilon.value < 1 && alpha.value > 0 && alpha.value < 1)) { error.value = 'ε与α需要在0与1之间。'; return }
  busy.value = true; invalidate()
  try {
    frozen.value = await evidenceApi.plan({ candidate_id: props.candidate.dataset_id, expected_candidate_hash: props.candidate.content_hash,
      reference_id: reference.value.dataset_id, expected_reference_hash: reference.value.content_hash,
      config: { key_mapping: Object.fromEntries(keyRows.map(row => [row.candidate, row.reference])),
        rules: rules.map(row => ({ ...row, atol: row.comparison === 'numeric' ? row.atol : 0, rtol: row.comparison === 'numeric' ? row.rtol : 0 })),
        sample_size: sampleSize.value, seed: seed.value, epsilon: epsilon.value, alpha: alpha.value, sampling_method: samplingMethod.value },
      source_declaration: { ...source } })
  } catch (e) { error.value = (e as Error).message } finally { busy.value = false }
}
async function evaluate() {
  if (!frozen.value) { error.value = '请先冻结审计计划。'; return }
  busy.value = true; error.value = ''; evaluated.value = null; combined.value = null
  try { evaluated.value = await evidenceApi.evaluate(frozen.value.plan_id) }
  catch (e) { error.value = (e as Error).message } finally { busy.value = false }
}
async function combine() {
  if (!props.candidate) { error.value = '请先导入同页候选CSV。'; return }
  busy.value = true; error.value = ''; combined.value = null
  try {
    combined.value = await evidenceApi.joint({ candidate_id: props.candidate.dataset_id, expected_candidate_hash: props.candidate.content_hash,
      ...(evaluated.value ? { plan_id: evaluated.value.plan_id } : {}), ...(props.runId ? { run_id: props.runId } : {}),
      auxiliary_id_columns: auxiliaryColumns.value, ...(props.featureColumns?.length ? { feature_columns: props.featureColumns } : {}) })
  } catch (e) { error.value = (e as Error).message } finally { busy.value = false }
}
</script>

<template>
<section class="panel evidence-audit" data-testid="evidence-audit">
 <div class="panel-heading"><div><h2>同一候选版本的参考审计与联合评估</h2><p class="muted small">无目标任务也可审计；没有参考数据也可保留独立的价值测量。</p></div></div>
 <p v-if="!candidate" class="notice">先在上方导入候选CSV，审计与任务测量会共用该精确版本。</p>
 <template v-else>
 <dl class="details-grid" data-testid="evidence-candidate-binding"><dt>共用候选版本</dt><dd>{{candidate.dataset_id}} / {{candidate.version}}</dd><dt>候选字节SHA-256</dt><dd>{{candidate.content_hash}}</dd></dl>
 <fieldset :disabled="locked" class="evidence-fields">
  <h3>参考数据与声明</h3>
  <div class="form-grid"><label>参考CSV<input aria-label="联合审计参考CSV" type="file" accept=".csv,text/csv" @change="pick"></label><label>参考CSV分隔符<select v-model="separator" aria-label="联合审计参考分隔符" @change="invalidate"><option value=",">逗号</option><option value=";">分号</option><option :value="'\t'">制表符</option></select></label></div>
  <button class="button secondary" data-testid="upload-reference" :disabled="!file" @click="upload">导入参考CSV</button>
  <p v-if="reference" class="notice" data-testid="evidence-reference-binding">{{reference.original_name??reference.dataset_id}} / {{reference.version}} · {{reference.row_count}}行<br>SHA-256 {{reference.content_hash}}</p>
  <div class="form-grid"><label>参考来源描述<input v-model="source.description" aria-label="参考来源描述" maxlength="500" @input="invalidate"></label><label>来源URL（声明）<input v-model="source.source_url" aria-label="参考来源URL" maxlength="500" @input="invalidate"></label><label>许可（声明）<input v-model="source.license" aria-label="参考许可" maxlength="200" @input="invalidate"></label><label>观测时间（声明）<input v-model="source.observed_at" aria-label="参考观测时间" placeholder="例如 2026-10-24T00:00:00Z" maxlength="200" @input="invalidate"></label><label>来源版本（声明）<input v-model="source.version" aria-label="参考来源版本" maxlength="200" @input="invalidate"></label><label>候选与参考来源关系<select v-model="source.relationship" aria-label="参考来源关系" @change="invalidate"><option value="UNKNOWN">UNKNOWN · 关系未知</option><option value="SAME_UPSTREAM">SAME_UPSTREAM · 共同上游</option><option value="DECLARED_INDEPENDENT">DECLARED_INDEPENDENT · 声明独立</option></select></label></div>
  <p class="notice warning">上述来源、许可、时间、版本及独立性均为用户声明，尚未核验。参考数据符合性不证明现实事实；共同上游可能共享错误，声明独立也不是独立审计证明。</p>
  <template v-if="reference">
   <h3>冻结候选/参考键映射与比较规则</h3>
   <div v-for="(row,index) in keyRows" :key="index" class="mapping-row"><label>候选键<select v-model="row.candidate" :aria-label="'候选键 '+index" @change="invalidate"><option value="">请选择</option><option v-for="column in candidateColumns" :key="column.name" :value="column.name">{{column.name}}</option></select></label><label>参考键<select v-model="row.reference" :aria-label="'参考键 '+index" @change="invalidate"><option value="">请选择</option><option v-for="column in referenceColumns" :key="column.name" :value="column.name">{{column.name}}</option></select></label><button class="button secondary compact" :disabled="keyRows.length===1" :aria-label="'移除键映射 '+index" @click="keyRows.splice(index,1);invalidate()">移除</button></div>
   <button class="button secondary compact" @click="keyRows.push({candidate:'',reference:''});invalidate()">增加复合键</button>
   <div v-for="(rule,index) in rules" :key="index" class="rule-row"><label>候选字段<select v-model="rule.candidate_column" :aria-label="'候选规则字段 '+index" @change="invalidate"><option value="">请选择</option><option v-for="column in candidateColumns" :key="column.name" :value="column.name">{{column.name}}</option></select></label><label>参考字段<select v-model="rule.reference_column" :aria-label="'参考规则字段 '+index" @change="invalidate"><option value="">请选择</option><option v-for="column in referenceColumns" :key="column.name" :value="column.name">{{column.name}}</option></select></label><label>比较规则<select v-model="rule.comparison" :aria-label="'字段比较规则 '+index" @change="invalidate"><option value="exact">精确相等</option><option value="numeric">数值容差</option></select></label><label v-if="rule.comparison==='numeric'">绝对容差<input v-model.number="rule.atol" :aria-label="'绝对容差 '+index" type="number" min="0" step="any" @input="invalidate"></label><label v-if="rule.comparison==='numeric'">相对容差<input v-model.number="rule.rtol" :aria-label="'相对容差 '+index" type="number" min="0" step="any" @input="invalidate"></label><button class="button secondary compact" :disabled="rules.length===1" :aria-label="'移除字段规则 '+index" @click="rules.splice(index,1);invalidate()">移除</button></div>
   <button class="button secondary compact" @click="rules.push({candidate_column:'',reference_column:'',comparison:'exact',atol:0,rtol:0});invalidate()">增加比较字段</button>
   <h3>事前固定审计设计</h3>
   <div class="form-grid"><label>抽样方式<select v-model="samplingMethod" aria-label="联合审计抽样方式" @change="changeSampling"><option value="SRSWOR">简单随机不放回 SRSWOR</option><option value="CENSUS">全量核验 CENSUS</option></select></label><label>预定样本量 n<input v-model.number="sampleSize" aria-label="联合审计样本量" type="number" min="1" step="1" :readonly="samplingMethod==='CENSUS'" @input="invalidate"></label><label>审计随机种子<input v-model.number="seed" aria-label="联合审计种子" type="number" min="0" step="1" @input="invalidate"></label><label>容忍分歧率 ε<input v-model.number="epsilon" aria-label="联合审计epsilon" type="number" min="0.0001" max="0.9999" step="0.001" @input="invalidate"></label><label>区间风险参数 α<input v-model.number="alpha" aria-label="联合审计alpha" type="number" min="0.0001" max="0.9999" step="0.001" @input="invalidate"></label></div>
   <p class="footnote">先冻结计划再查看核验结果；更改参数会清除当前显示并创建新计划，不能看完结果再挑有利预算或种子。非概率观测不自动产生概率区间。</p>
   <button class="button" data-testid="freeze-evidence-plan" @click="freeze">冻结参考审计计划</button>
  </template>
 </fieldset>
 <div v-if="frozen" class="frozen-evidence" data-testid="evidence-plan"><h3>已冻结的审计计划</h3><dl class="details-grid"><dt>计划ID</dt><dd>{{frozen.plan_id}}</dd><dt>计划SHA-256</dt><dd>{{frozen.plan_hash}}</dd><dt>冻结抽样要求</dt><dd>n={{frozen.plan.sample_size??'UNKNOWN'}} / N={{frozen.plan.population_size??'UNKNOWN'}}，seed={{frozen.plan.config?.seed??'UNKNOWN'}}，ε={{pct(frozen.plan.config?.epsilon)}}，α={{pct(frozen.plan.config?.alpha)}}</dd></dl><JsonDetails :data="frozen.plan" title="固定样本位置、版本与规则"/><button class="button" data-testid="evaluate-evidence-plan" :disabled="locked" @click="evaluate">按固定计划执行参考核验</button></div>
 <section v-if="audit" data-testid="evidence-result"><h3>样本参考符合性 <StatusBadge :value="audit.statistical_decision"/></h3>
  <dl class="details-grid"><dt>样本通过/分歧/不可比</dt><dd data-testid="evidence-counts">{{shown(audit.counts?.passed)}} / {{shown(audit.counts?.disagreements)}} / {{shown(audit.counts?.incomparable)}}（固定样本={{shown(audit.counts?.sample_size)}}，候选总体={{shown(audit.counts?.population_size)}}）</dd><dt>参考覆盖</dt><dd data-testid="evidence-coverage">{{coverage}}</dd><dt>分歧率区间</dt><dd data-testid="evidence-interval">{{audit.disagreement_interval?'['+pct(audit.disagreement_interval.lower)+', '+pct(audit.disagreement_interval.upper)+']':'UNKNOWN · 未形成可用概率区间'}}</dd><dt>参照范围统计判断</dt><dd data-testid="evidence-statistical-decision">{{audit.statistical_decision??'UNKNOWN'}}</dd><dt>现实事实状态</dt><dd data-testid="evidence-fact-status">{{audit.fact_status??'UNKNOWN'}}</dd><dt>正式使用条件</dt><dd data-testid="evidence-formal-decision">{{audit.formal_decision??'NEED_MORE_EVIDENCE'}}</dd></dl>
  <p class="notice warning">不可比或未解决不等于通过。参考分歧、覆盖与现实事实状态分别解释；即使参照符合性通过，内容事实仍可能未知。</p>
  <div class="actions"><a class="button secondary" :href="evidenceApi.reportUrl(evaluated!.plan_id,'json')" download>导出参考审计JSON</a><a class="button secondary" :href="evidenceApi.reportUrl(evaluated!.plan_id,'md')" download>导出中文参考审计报告</a></div>
  <JsonDetails :data="audit.disagreement_interval" title="区间对象、风险参数、方法与覆盖条件" open/>
  <JsonDetails :data="audit.limitations" title="抽样、来源声明与区间的限制" open/>
  <div v-if="observations.length" class="table-wrap"><table class="observation-table"><thead><tr><th>观测序号</th><th>后端保留的逐行核验结果</th></tr></thead><tbody><tr v-for="(observation,index) in observations" :key="index"><td>{{index}}</td><td><pre>{{JSON.stringify(observation,null,2)}}</pre></td></tr></tbody></table></div>
  <JsonDetails :data="audit" title="完整参考审计报告与计数依据"/>
 </section>
 <fieldset :disabled="locked" class="evidence-fields joint-controls"><h3>同版本联合报告与重复诊断</h3><p class="notice">当前价值运行：{{runId??'NOT_EVALUATED · 尚无完成的任务运行'}}。联合服务按候选ID、版本与SHA-256核对证据，不借用其他数据集或版本的结论。</p>
  <div class="id-options"><label v-for="column in candidateColumns" :key="column.name" class="check-label"><input v-model="auxiliaryColumns" :value="column.name" type="checkbox" :aria-label="'重复诊断辅助ID '+column.name" @change="invalidateJoint">辅助ID：{{column.name}}</label></div>
  <p class="footnote">整行重复、去除辅助ID后的内容重复、任务特征重复是不同口径；请选择真正的辅助ID。重复本身不判断事实真伪或直接证明任务收益。</p>
  <p class="footnote">本次任务特征列：{{featureColumns?.length?featureColumns.join('、'):'NOT_SPECIFIED · 没有任务特征定义'}}。</p>
  <button class="button" data-testid="create-joint-report" @click="combine">生成同版本联合报告</button>
 </fieldset>
 <section v-if="joint" data-testid="joint-result"><div class="panel-heading"><h3>可信证据与任务价值联合说明</h3><div class="actions"><a class="button secondary" :href="evidenceApi.jointUrl(combined!.joint_id,'json')" download>导出联合JSON</a><a class="button secondary" :href="evidenceApi.jointUrl(combined!.joint_id,'md')" download>导出中文联合报告</a></div></div>
  <dl class="details-grid"><dt>联合候选版本</dt><dd data-testid="joint-candidate-binding">{{joint.candidate?.dataset_id}} / {{joint.candidate?.version}} / {{joint.candidate?.content_hash}}</dd><dt>参考审计</dt><dd>{{joint.evidence?.status??'NOT_EVALUATED'}}</dd><dt>价值证据状态</dt><dd data-testid="joint-value-status">{{joint.value?.value_status??'NOT_EVALUATED'}} · {{valueStateText(joint.value?.value_status??'NOT_EVALUATED')}}</dd><dt>证据说明与行动</dt><dd data-testid="joint-value-action">{{joint.value?.explanation_zh??'UNKNOWN'}} {{joint.value?.action_hint_zh??''}}</dd><dt>保留的原决策</dt><dd data-testid="joint-original-decision">{{shown(joint.value?.original_decision)}}</dd><dt>正式用途</dt><dd data-testid="joint-formal-decision">{{joint.joint_decision?.formal_decision??'NEED_MORE_EVIDENCE'}}</dd><dt>现实事实</dt><dd data-testid="joint-fact-status">{{joint.joint_decision?.fact_status??'UNKNOWN'}}</dd><dt>探索性建议</dt><dd data-testid="joint-exploration-action">{{joint.joint_decision?.exploration_action??'NEED_MORE_EVIDENCE'}}</dd></dl>
  <h3>结构质量与来源完整性</h3>
  <dl class="details-grid"><dt>结构测量状态</dt><dd data-testid="joint-structural-status">{{joint.structural_quality?.status??'UNKNOWN'}} · {{joint.structural_quality?.schema_name??'UNKNOWN'}}</dd><dt>单元格完整性</dt><dd data-testid="joint-completeness">{{pct(joint.structural_quality?.metrics?.completeness)}} · {{joint.structural_quality?.metrics?.completeness_status??'UNKNOWN'}}；缺失{{shown(joint.structural_quality?.metrics?.missing_cells)}} / {{shown(joint.structural_quality?.metrics?.cell_count)}}个单元格</dd><dt>格式有效性</dt><dd data-testid="joint-format-validity">{{pct(joint.structural_quality?.metrics?.format_validity)}} · {{joint.structural_quality?.metrics?.format_validity_status??'UNKNOWN'}}</dd><dt>规则一致性</dt><dd data-testid="joint-consistency">{{pct(joint.structural_quality?.metrics?.consistency)}} · {{joint.structural_quality?.metrics?.consistency_status??'UNKNOWN'}}</dd><dt>本地字节/版本绑定</dt><dd data-testid="joint-source-binding">{{joint.source_integrity?.status??'UNKNOWN'}}；SHA-256={{joint.source_integrity?.content_hash?.status??'UNKNOWN'}}；版本={{joint.source_integrity?.version?.status??'UNKNOWN'}}</dd><dt>来源真实性</dt><dd data-testid="joint-source-authenticity">{{joint.source_integrity?.origin_authenticity?.status??'UNKNOWN'}}</dd><dt>来源签名</dt><dd data-testid="joint-signature-status">{{joint.source_integrity?.signature?.status??'UNKNOWN'}}</dd><dt>来源声明认证</dt><dd data-testid="joint-source-declarations">{{joint.source_integrity?.source_declaration?.status??'UNKNOWN'}} · {{joint.source_integrity?.source_declaration?.scope??'UNKNOWN'}}</dd></dl>
  <p class="notice warning">单元格完整或本地字节哈希匹配不证明现实内容真实；未声明领域格式与一致性规则时保持NOT_VERIFIED。来源、签名与用户声明认证独立报告，不能由结构质量或正任务效用补齐。</p>
  <div data-testid="joint-structural-details"><JsonDetails :data="joint.structural_quality" title="完整结构质量、单元格计数与未核验规则"/></div>
  <div data-testid="joint-source-integrity-details"><JsonDetails :data="joint.source_integrity" title="来源完整性、实际字节哈希、版本及未认证声明"/></div>
  <h3>三种重复口径与事实未知</h3>
  <dl class="details-grid"><dt>整行重复</dt><dd data-testid="duplicate-full-row">{{duplicateText(joint.duplicate_metrics?.full_row)}}</dd><dt>去辅助ID内容重复</dt><dd data-testid="duplicate-content">{{duplicateText(joint.duplicate_metrics?.content_excluding_auxiliary_ids)}}</dd><dt>任务特征重复</dt><dd data-testid="duplicate-task-features">{{duplicateText(joint.duplicate_metrics?.task_features)}}</dd><dt>事实层面重复</dt><dd data-testid="duplicate-factual">{{joint.duplicate_metrics?.factual_duplicates?.status??'UNKNOWN'}} · 未由字段相等证明现实实体/事件重复</dd></dl>
  <p class="notice warning">参考符合性不代替事实核验；任务价值为条件证据，不乘可信分，不估计市场价格。不采购基线与历史决策保留在原始报告中。</p>
  <JsonDetails :data="joint.binding" title="候选、参考审计与价值运行的精确绑定"/>
  <JsonDetails :data="joint.value" title="验证效用、原决策、不采购基线与五状态语义"/>
  <JsonDetails :data="joint.duplicate_metrics" title="完整重复计数、列集合与计算范围"/>
  <JsonDetails :data="joint" title="精确版本、结构重复、审计、价值五状态与联合建议" open/>
 </section>
 </template>
 <p v-if="busy" class="notice" role="status">正在读取真实本地证据服务…</p>
 <p v-if="error" class="notice error" role="alert">{{error}}</p>
</section>
</template>

<style scoped>
.evidence-audit{overflow-wrap:anywhere}.evidence-fields{border:0;padding:0;margin:0;min-width:0}.evidence-fields h3{margin-top:24px}.mapping-row{display:grid;grid-template-columns:1fr 1fr auto;align-items:end;gap:12px;margin:14px 0}.rule-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr)) auto;align-items:end;gap:12px;margin:14px 0}.rule-row:has(input){grid-template-columns:repeat(3,minmax(0,1fr))}.frozen-evidence{border-top:1px solid var(--border);margin-top:20px;padding-top:4px}.evidence-audit dd{min-width:0;overflow-wrap:anywhere}.joint-controls{border-top:1px solid var(--border);margin-top:24px}.id-options{display:flex;flex-wrap:wrap;gap:4px 18px}.id-options .check-label{margin:3px 0}.observation-table{min-width:440px}.observation-table pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:10px;margin:0;max-height:180px;overflow:auto}.table-wrap{max-height:420px;margin-top:16px}@media(max-width:700px){.mapping-row,.rule-row,.rule-row:has(input){grid-template-columns:1fr}.mapping-row button,.rule-row button{justify-self:start}}
</style>
