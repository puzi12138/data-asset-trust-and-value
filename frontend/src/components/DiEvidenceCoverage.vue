<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ApiError, type Row } from '../api'
import { coverageApi, coverageLabels, type DiCoverage } from '../diEvidence'
import { publicError, readable } from '../visualCases'
import { valueExplanation } from '../sameAssetJoint'
import JsonDetails from './JsonDetails.vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps<{ jointId: string }>()
const data = ref<DiCoverage | null>(null), capabilities = ref<Row | null>(null), error = ref(''), rejection = ref<Row | null>(null)
const loading = ref(false), rowsLoading = ref(false), rowsError = ref(''), detailError = ref('')
const members = ref<Row[]>([]), total = ref<number | null>(null), offset = ref(0), showRows = ref(false), outcome = ref('')
const rowDetail = ref<Row | null>(null)
const summary = computed<Row>(() => data.value?.audit_summary ?? {})
const value = computed<Row>(() => data.value?.task_value ?? {})
const valueState = computed(() => value.value.value_status ?? value.value.status ?? 'NOT_EVALUATED')
const decision = computed<Row>(() => typeof data.value?.decision === 'object' && data.value.decision ? data.value.decision : {})
const budgets = computed<Row[]>(() => Array.isArray(data.value?.budget_results) ? data.value!.budget_results : [])
const hasAudit = computed(() => !!data.value?.audit_summary && data.value?.status !== 'NOT_EVALUATED' && data.value?.status !== 'BINDING_MISMATCH')
let generation = 0, rowGeneration = 0
async function load() {
  const current = ++generation
  rowGeneration++
  data.value = null; error.value = ''; rejection.value = null; loading.value = true
  members.value = []; total.value = null; offset.value = 0; showRows.value = false; rowDetail.value = null; rowsError.value = ''; detailError.value = ''
  try {
    const report = await coverageApi.get(props.jointId)
    if (current !== generation) return
    data.value = report
    try { const response = await coverageApi.capabilities(); if (current === generation) capabilities.value = response } catch { /* Capability hints do not replace the verified report. */ }
  } catch (e) {
    if (current !== generation) return
    error.value = publicError(e)
    if (e instanceof ApiError && e.details && typeof e.details === 'object') rejection.value = (e.details as Row).detail ?? null
  } finally { if (current === generation) loading.value = false }
}
async function loadRows(nextOffset = 0) {
  const current = generation, id = props.jointId
  rowGeneration++
  showRows.value = true; rowsLoading.value = true; rowsError.value = ''; rowDetail.value = null
  try {
    const response = await coverageApi.rows(id, nextOffset, 100, outcome.value)
    if (current !== generation) return
    if (!Array.isArray(response.rows) || !Number.isInteger(response.total) || response.total < 0) throw new Error('服务端未提供有效的审计成员列表。')
    members.value = response.rows; total.value = response.total; offset.value = response.offset
  } catch (e) { if (current === generation) { members.value = []; total.value = null; rowsError.value = publicError(e) } }
  finally { if (current === generation) rowsLoading.value = false }
}
async function detail(row: Row) {
  const current = generation, rowSequence = ++rowGeneration, id = props.jointId
  rowDetail.value = null; detailError.value = ''
  try {
    const response = await coverageApi.row(id, row.candidate_row)
    if (current !== generation || rowSequence !== rowGeneration) return
    if (response.candidate_row !== row.candidate_row || response.row_id !== row.row_id || response.role !== 'Di') throw new Error('返回的记录不是所选Di成员，已阻止错配展示。')
    rowDetail.value = response
  } catch (e) { if (current === generation && rowSequence === rowGeneration) detailError.value = publicError(e) }
}
watch(() => props.jointId, load, { immediate: true })
</script>

<template>
  <section class="panel di-coverage" data-testid="di-coverage">
    <div class="panel-heading"><h2>Di可信证据覆盖</h2><button class="button secondary" :disabled="loading" @click="load" data-testid="coverage-refresh">重新核验覆盖绑定</button></div>
    <p>这里核查实际加入基础训练数据的候选子集Di。验证集或测试集中的视觉记录不充当Di内容审计。</p>
    <p v-if="loading" role="status" data-testid="coverage-loading">正在核对冻结成员、原始参照和价值结果…</p>
    <div v-if="error" role="alert" class="notice error" data-testid="coverage-error"><b>{{ rejection?.status ?? '未形成覆盖结论' }}</b><p>{{ error }}</p><p>版本、成员、参照或协议不匹配时拒绝沿用旧审计；当前页面不会补齐或伪造核验结果。</p><JsonDetails v-if="rejection" :data="rejection" title="查看拒绝原因"/></div>
    <template v-if="data">
      <div class="panel-heading"><h3>同版本覆盖状态</h3><StatusBadge :value="data.status"/></div>
      <dl class="details-grid break-word"><dt>联合报告ID</dt><dd class="mono">{{ data.joint_id }}</dd><dt>覆盖结果状态</dt><dd data-testid="coverage-status">{{ data.status }}</dd><dt>结果SHA-256</dt><dd class="mono">{{ readable(data.result_sha256) }}</dd></dl>
      <div v-if="!hasAudit" class="notice warning" data-testid="coverage-not-evaluated">该精确版本没有可用的Di审计，保持NOT_EVALUATED；没有核验不等于已经符合。{{ readable(data.reason) }}</div>
      <template v-else>
        <div class="table-wrap"><table data-testid="coverage-counts"><thead><tr><th>证据范围</th><th>服务器实际结果</th></tr></thead><tbody><tr v-for="(label, key) in coverageLabels" :key="key"><td>{{ label }}</td><td :data-testid="'coverage-count-' + key">{{ readable(summary[key]) }}</td></tr></tbody></table></div>
        <dl class="details-grid break-word"><dt>证据来源类别</dt><dd data-testid="coverage-reference-level">{{ readable(summary.reference_level) }}</dd><dt>共享原始上游</dt><dd>{{ readable(summary.shared_upstream) }}</dd><dt>公开参照符合状态</dt><dd>{{ readable(summary.reference_conformance_status) }}</dd><dt>现实事实状态</dt><dd data-testid="coverage-fact-status">{{ readable(summary.fact_status) }}</dd><dt>任务价值状态</dt><dd data-testid="coverage-value-state">{{ valueState }}</dd><dt>联合使用建议</dt><dd data-testid="coverage-decision">{{ readable(decision.recommendation ?? decision.action ?? decision.status ?? data.decision) }}</dd></dl>
        <div class="notice warning" data-testid="coverage-evidence-boundary">公开采集者标注参照属于B类，与像素共享原始采集上游。文件完整、与标注一致和AI视觉支持应分别解释；这些证据不自动成为独立现实事实核验C。重复审阅不增加独立审计样本数。</div>
        <p class="notice" data-testid="coverage-value-explanation">{{ valueExplanation(valueState) }} 新审计不改变冻结的预测损失、效用或区间，不将可信指数乘进任务效用。</p>
        <h3>预先固定的审计预算与区间</h3>
        <p class="muted">区间只针对本固定公开参照的分歧率。各固定预算分别解释，不构成多预算同时保证或可选停止保证；普查结果不是真实性概率。</p>
        <div class="table-wrap"><table data-testid="coverage-budgets"><thead><tr><th>实际审计预算</th><th>抽样方法</th><th>分歧率区间</th><th>参照符合判断</th><th>未核验记录</th></tr></thead><tbody><tr v-for="(budget, i) in budgets" :key="i"><td>{{ readable(budget.budget) }}</td><td>{{ readable(budget.method) }}</td><td>[{{ readable(budget.assessment?.disagreement_interval?.lower) }}, {{ readable(budget.assessment?.disagreement_interval?.upper) }}]</td><td>{{ readable(budget.reference_conformance_status) }}</td><td>{{ readable(budget.coverage?.not_audited_records) }}</td></tr><tr v-if="!budgets.length"><td colspan="5">未提供适用的区间结果，保留UNKNOWN。</td></tr></tbody></table></div>
        <JsonDetails :data="data.budget_results" title="查看预定审计预算、错误率区间与统计假设"/>
        <JsonDetails :data="data.binding" title="查看Di成员、参照协议、资产与冻结价值的精确绑定"/>
        <JsonDetails :data="data.reference" title="查看公开原始参照及来源记录"/>
        <JsonDetails :data="data.task_value" title="查看原冻结价值、区间及不使用基线"/>
        <JsonDetails :data="data.decision" title="查看联合使用判断理由与必要证据条件"/>
        <div class="actions coverage-actions"><button class="button secondary" :disabled="rowsLoading" @click="loadRows(0)" data-testid="coverage-show-members">查看Di成员、参照与未核验记录</button><a class="button secondary" data-testid="coverage-export-md" :href="coverageApi.reportUrl(data.joint_id, 'md')" download>导出中文覆盖联合报告</a><a class="button secondary" data-testid="coverage-export-json" :href="coverageApi.reportUrl(data.joint_id, 'json')" download>导出完整覆盖JSON</a></div>
        <div v-if="showRows" data-testid="coverage-members">
          <label>核查状态筛选<select aria-label="Di核查状态筛选" v-model="outcome" @change="loadRows(0)"><option value="">全部Di成员</option><option value="MATCH">MATCH · 符合参照</option><option value="MISMATCH">MISMATCH · 分歧</option><option value="INCOMPARABLE">INCOMPARABLE · 无法比较</option><option value="DISPUTED">DISPUTED · 待裁定</option><option value="NOT_AUDITED">NOT_AUDITED · 未核验</option></select></label>
          <p v-if="rowsLoading" role="status">正在读取逐条审计记录…</p><p v-if="rowsError" class="notice error" role="alert" data-testid="coverage-rows-error">{{ rowsError }}</p>
          <p v-if="total !== null" class="muted" data-testid="coverage-member-total">当前筛选共 {{ total }} 条；成员角色、参照定位与结论均来自服务端。</p>
          <div class="table-wrap"><table><thead><tr><th>原CSV行 / 记录ID</th><th>实际角色</th><th>公开参照定位</th><th>核查结果</th><th>操作</th></tr></thead><tbody><tr v-for="row in members" :key="String(row.candidate_row) + ':' + row.row_id"><td class="mono break-word">{{ row.candidate_row }} / {{ row.row_id }}</td><td>{{ readable(row.role) }}</td><td class="mono break-word">{{ readable(row.reference_record) }} / {{ readable(row.reference_line) }}</td><td>{{ readable(row.outcome) }}</td><td><button class="button compact secondary" :data-testid="'coverage-row-' + row.candidate_row" @click="detail(row)">查看原始参照与审计依据</button></td></tr><tr v-if="!rowsLoading && !members.length && !rowsError"><td colspan="5">当前筛选没有记录；未核验数量仍以覆盖统计为准。</td></tr></tbody></table></div>
          <div class="actions" v-if="total !== null && total > 100"><button class="button secondary" :disabled="rowsLoading || offset === 0" @click="loadRows(Math.max(0, offset - 100))">上一页</button><button class="button secondary" :disabled="rowsLoading || offset + members.length >= total" @click="loadRows(offset + 100)">下一页</button></div>
          <p v-if="detailError" class="notice error" role="alert" data-testid="coverage-detail-error">{{ detailError }}</p><div v-if="rowDetail" class="notice" data-testid="coverage-row-detail"><h4>所选Di记录的证据链</h4><p class="mono">{{ rowDetail.row_id }} · {{ rowDetail.outcome }} · {{ rowDetail.evidence_level }}</p><JsonDetails :data="rowDetail" title="展开匹配键、原始参照、字段检查与审计依据"/></div>
        </div>
      </template>
      <JsonDetails :data="data" title="完整覆盖报告与证据边界"/>
    </template>
    <JsonDetails v-if="capabilities" :data="capabilities" title="本地覆盖接口能力（无新增训练或模型调用）"/>
  </section>
</template>

<style scoped>
.details-grid{grid-template-columns:150px minmax(0,1fr)}.di-coverage p{overflow-wrap:anywhere}.coverage-actions{margin:16px 0}td{overflow-wrap:anywhere}.di-coverage select{max-width:100%}@media(max-width:560px){.details-grid{grid-template-columns:105px minmax(0,1fr)}.coverage-actions{align-items:stretch}.coverage-actions>*{text-align:center}.table-wrap{overflow-x:auto}table{min-width:480px}}
</style>
