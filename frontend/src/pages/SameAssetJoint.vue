<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ApiError, type Row } from '../api'
import { casesApi, publicError, readable, type EvidenceCase } from '../visualCases'
import { bindingItems, jointApi, jointReport, valueExplanation, type JointEnvelope } from '../sameAssetJoint'
import JsonDetails from '../components/JsonDetails.vue'
import StatusBadge from '../components/StatusBadge.vue'
import DiEvidenceCoverage from '../components/DiEvidenceCoverage.vue'

const capabilities = ref<Row | null>(null), cases = ref<EvidenceCase[]>([]), saved = ref<Row[]>([])
const selected = ref<JointEnvelope | null>(null), rejected = ref<Row | null>(null)
const caseId = ref(''), valueRunId = ref(''), error = ref(''), loadError = ref('')
const loading = ref(false), running = ref(false)
const report = computed<Row>(() => selected.value ? jointReport(selected.value) : {})
const trust = computed<Row>(() => report.value.trust ?? {})
const value = computed<Row>(() => report.value.task_value ?? {})
const valueState = computed(() => value.value.value_status ?? value.value.status ?? 'NOT_EVALUATED')
const binding = computed(() => report.value.binding ? bindingItems(report.value.binding) : [])
const issues = computed<Row[]>(() => rejected.value?.issues ?? rejected.value?.result?.issues ?? report.value.issues ?? [])
const runs = computed<Row[]>(() => Array.isArray(capabilities.value?.value_runs) ? capabilities.value!.value_runs : [])
const chosenCase = computed(() => cases.value.find(item => item.case_id === caseId.value))
const chosenRun = computed(() => runs.value.find(item => (item.run_id ?? item.value_run_id) === valueRunId.value))
let operation = 0

async function load() {
  loading.value = true; loadError.value = ''
  const replies = await Promise.allSettled([jointApi.capabilities(), casesApi.list(), jointApi.list()])
  if (replies[0].status === 'fulfilled') capabilities.value = replies[0].value
  else loadError.value = publicError(replies[0].reason)
  if (replies[1].status === 'fulfilled') cases.value = replies[1].value.cases ?? []
  else loadError.value += (loadError.value ? '；' : '') + publicError(replies[1].reason)
  if (replies[2].status === 'fulfilled') saved.value = replies[2].value.reports ?? replies[2].value.joints ?? []
  else loadError.value += (loadError.value ? '；' : '') + publicError(replies[2].reason)
  loading.value = false
}
function clear() { operation++; selected.value = null; rejected.value = null; error.value = ''; running.value = false }
function captureError(e: unknown) {
  error.value = publicError(e)
  if (e instanceof ApiError && e.details && typeof e.details === 'object') {
    const detail = (e.details as Row).detail
    rejected.value = typeof detail === 'object' ? detail : null
  }
}
async function coordinate() {
  clear()
  if (!caseId.value.trim()) { error.value = '请选择或填写视觉案例ID。'; return }
  const sequence = ++operation; running.value = true
  try {
    const result = await jointApi.create(caseId.value.trim(), valueRunId.value.trim() || null)
    if (sequence !== operation) return
    selected.value = result
    await load()
  } catch (e) { if (sequence === operation) captureError(e) }
  finally { if (sequence === operation) running.value = false }
}
async function open(id: string) {
  clear(); const sequence = ++operation; running.value = true
  try { const result = await jointApi.get(id); if (sequence === operation) selected.value = result }
  catch (e) { if (sequence === operation) captureError(e) }
  finally { if (sequence === operation) running.value = false }
}
onMounted(load)
</script>

<template>
  <section class="panel joint-intro">
    <div class="panel-heading"><h2>同一个数据版本，两类独立结论</h2><button class="button secondary" :disabled="loading || running" @click="load">刷新已保存输入</button></div>
    <p>选择视觉案例与实际任务测量。服务逐项核验资产、证据、任务和评价协议；只有一致的精确版本才形成联合报告。</p>
    <div class="notice">同版本绑定不认证事实，不将可信分乘入任务收益。NASA视觉结果与其他数据集任务价值不能拼接为同一资产。</div>
    <div class="actions"><RouterLink to="/visual-cases" class="text-link">注册证据或执行AI视觉审阅</RouterLink><RouterLink to="/task-measurement" class="text-link">运行新CSV真实任务测量</RouterLink></div>
    <p v-if="loading" role="status" class="muted">正在读取本地已保存案例与任务运行…</p>
    <p v-if="loadError" role="alert" class="notice error" data-testid="joint-load-error">{{ loadError }}</p>
    <JsonDetails v-if="capabilities" :data="capabilities" title="查看实际接口能力及允许的完整绑定字段"/>
  </section>

  <section class="panel">
    <h2>选择输入并核验绑定</h2>
    <form @submit.prevent="coordinate" data-testid="joint-form">
      <div class="form-grid">
        <label>视觉案例ID（可选择或粘贴）<input aria-label="联合评估视觉案例ID" v-model="caseId" @input="clear" list="joint-case-options" autocomplete="off" placeholder="本地已注册case_id"/><datalist id="joint-case-options"><option v-for="item in cases" :key="item.case_id" :value="item.case_id">{{ item.asset?.asset_id }} / {{ item.asset?.asset_version }}</option></datalist></label>
        <label>实际任务运行ID（可留空）<input aria-label="联合评估价值运行ID" v-model="valueRunId" @input="clear" list="joint-value-options" autocomplete="off" placeholder="本地已保存run_id；没有则NOT_EVALUATED"/><datalist id="joint-value-options"><option v-for="item in runs" :key="item.run_id ?? item.value_run_id" :value="item.run_id ?? item.value_run_id">{{ item.candidate?.dataset_id }} / {{ item.candidate?.version }} / {{ item.status }}</option></datalist></label>
      </div>
      <div class="two-column joint-inputs">
        <div v-if="chosenCase" class="notice break-word" data-testid="joint-case-preview"><b>视觉输入版本</b><p>{{ chosenCase.asset?.asset_id }} / {{ chosenCase.asset?.asset_version }}</p><p class="mono">{{ chosenCase.asset?.asset_sha256 }}</p></div>
        <div v-if="chosenRun" class="notice break-word" data-testid="joint-value-preview"><b>任务输入版本</b><p>{{ chosenRun.candidate?.dataset_id }} / {{ chosenRun.candidate?.version }} / {{ chosenRun.status }}</p><p class="mono">{{ chosenRun.candidate?.content_hash }}</p><p v-if="chosenRun.error">{{ chosenRun.error }}</p></div>
      </div>
      <button class="button" type="submit" :disabled="running || loading" data-testid="coordinate-joint">{{ running ? '正在核验保存结果…' : '核验并形成同版本联合报告' }}</button>
      <p class="footnote">本操作核验已保存的可信与价值证据，不自动训练，也不发起模型调用。LIVE与REPLAY信息保留在原始审阅记录中。</p>
    </form>
    <div v-if="error" class="notice error" role="alert" data-testid="joint-error"><b>{{ rejected?.status ?? '未形成联合结论' }}</b><p>{{ error }}</p><p>请检查资产ID、版本、SHA-256及任务协议。绑定不一致时不展示为联合成功。</p></div>
    <div v-if="issues.length" class="notice warning" data-testid="joint-issues"><b>需要处理的证据或绑定问题</b><ul><li v-for="(item, i) in issues" :key="i"><span class="mono">{{ item.code }}</span> · {{ item.path }}</li></ul></div>
  </section>

  <template v-if="selected">
    <section class="panel" data-testid="joint-result">
      <div class="panel-heading"><h2>同版本核验结果</h2><StatusBadge :value="report.status"/></div>
      <dl class="details-grid break-word"><dt>绑定状态</dt><dd data-testid="joint-status">{{ report.status }}</dd><dt>联合报告ID</dt><dd class="mono">{{ selected.joint_id }}</dd><dt>评估时间</dt><dd>{{ readable(report.evaluated_at) }}</dd><dt>结果SHA-256</dt><dd class="mono" data-testid="joint-result-hash">{{ readable(report.result_sha256) }}</dd></dl>
      <p v-if="report.status === 'SAME_ASSET_BOUND'" class="notice" data-testid="joint-bound-note">已核验同一资产版本和任务文件链。该状态不代表整份数据集真实，也不表示任务价值必然为正。</p>
      <p v-else class="notice warning">{{ valueExplanation('NOT_EVALUATED') }}</p>
      <div class="actions"><a class="button secondary" data-testid="joint-export-md" :href="jointApi.reportUrl(selected.joint_id, 'md')" download>导出中文Markdown</a><a class="button secondary" data-testid="joint-export-json" :href="jointApi.reportUrl(selected.joint_id, 'json')" download>导出完整JSON</a></div>
    </section>

    <DiEvidenceCoverage :joint-id="selected.joint_id"/>

    <div class="two-column">
      <section class="panel" data-testid="joint-trust"><h2>可信证据与条件判断</h2><dl class="details-grid break-word"><dt>事实状态</dt><dd data-testid="joint-fact-status">{{ readable(trust.fact_status) }}</dd><dt>可信判断</dt><dd>{{ readable(trust.assessment?.decision ?? trust.assessment?.status ?? trust.assessment) }}</dd><dt>来源认证</dt><dd>{{ readable(trust.source_authentication) }}</dd><dt>证据协调</dt><dd>{{ readable(trust.coordination_state) }}</dd><dt>证据 / 审阅数</dt><dd>{{ readable(trust.evidence_count) }} / {{ readable(trust.review_count) }}</dd><dt>绑定增加物理观测</dt><dd>{{ readable(trust.additional_physical_observations_from_joint_binding) }}</dd></dl><p class="notice warning">AI视觉一致性不等于事实准确率；局部MATCH不证明整个数据集真实。重复审阅不增加独立物理样本。</p><JsonDetails :data="trust" title="可信原始依据与证据边界"/></section>
      <section class="panel" data-testid="joint-value"><h2>当前任务的实际边际效用</h2><dl class="details-grid break-word"><dt>测量状态</dt><dd>{{ readable(value.status) }}</dd><dt>价值证据状态</dt><dd data-testid="joint-value-state">{{ valueState }}</dd><dt>买方情境</dt><dd>{{ readable(value.buyer_mode) }}</dd><dt>市场价格</dt><dd>{{ readable(value.market_price) }}</dd><dt>使用建议</dt><dd>{{ readable(value.recommendation) }}</dd></dl><div class="notice warning" data-testid="joint-value-explanation">{{ valueExplanation(valueState) }}</div><p v-if="value.reason" class="notice">{{ value.reason }}</p><JsonDetails v-if="value.validation" :data="value.validation" title="验证集边际效用与不确定性（决策依据）"/><JsonDetails v-if="value.test" :data="value.test" title="冻结后的测试集结果（事后评价）"/><p class="footnote">保留负效用和不使用基线；测试结果不用于事后选择最有利方案。任务损失单位按原始测量报告，不能换成市场价格。</p></section>
    </div>

    <section class="panel" data-testid="joint-binding"><h2>逐项绑定依据</h2><p class="muted small">以下字段来自服务端核验结果；页面不修改统计结论或补齐缺失证据。</p><div class="table-wrap"><table><thead><tr><th>绑定字段</th><th>已核验值 / 未知状态</th></tr></thead><tbody><tr v-for="item in binding" :key="item.path"><td class="mono break-word">{{ item.path }}</td><td class="mono break-word">{{ item.value }}</td></tr><tr v-if="!binding.length"><td colspan="2">当前报告没有完整绑定，不能视作同版本联合成功。</td></tr></tbody></table></div><div class="notice" data-testid="joint-recommendation">{{ readable(report.recommendation) }}</div><JsonDetails :data="report" title="完整服务端联合报告（含结果哈希）"/></section>
  </template>

  <section class="panel"><h2>本地已保存的联合报告</h2><p v-if="!saved.length" class="muted" data-testid="no-saved-joints">尚无已保存联合报告。无合法价值资料时仍可保留NOT_EVALUATED。</p><div v-else class="table-wrap"><table><thead><tr><th>报告ID</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="item in saved" :key="item.joint_id"><td class="mono break-word">{{ item.joint_id }}</td><td>{{ item.status ?? item.report?.status }}</td><td><button class="button compact secondary" :disabled="running" @click="open(item.joint_id)" :data-testid="'open-joint-' + item.joint_id">读取核验报告</button></td></tr></tbody></table></div></section>
</template>

<style scoped>
.joint-intro>p{font-size:12px;color:#657d8a}.joint-inputs{margin-bottom:10px}.joint-inputs>.notice{margin:0 0 14px;min-width:0}.details-grid{grid-template-columns:120px minmax(0,1fr)}td{max-width:650px}.notice,.details-grid dd,.details-grid dt{overflow-wrap:anywhere}.joint-intro .actions{margin-bottom:10px}@media(max-width:560px){.details-grid{grid-template-columns:95px minmax(0,1fr)}.joint-inputs{gap:0}table{table-layout:fixed}td:first-child{width:38%}}
</style>
