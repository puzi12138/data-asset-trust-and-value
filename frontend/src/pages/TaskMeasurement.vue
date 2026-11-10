<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { measurementApi, type MeasurementDataset, type MeasurementOptions, type MeasurementRun } from '../selfService'
import JsonDetails from '../components/JsonDetails.vue'
import StatusBadge from '../components/StatusBadge.vue'
import DatasetEvidenceAudit from '../components/DatasetEvidenceAudit.vue'
import { valueStateText } from '../evidence'
import type { Row } from '../api'

type Role = 'candidate' | 'buyer' | 'validation' | 'test'
const roles: { key: Role; label: string }[] = [{ key: 'candidate', label: '候选数据' }, { key: 'buyer', label: '买方已有数据 D0' }, { key: 'validation', label: '独立验证数据' }, { key: 'test', label: '独立测试数据' }]
const files = reactive<Record<Role, File | null>>({ candidate: null, buyer: null, validation: null, test: null })
const datasets = reactive<Record<Role, MeasurementDataset | null>>({ candidate: null, buyer: null, validation: null, test: null })
const options = ref<MeasurementOptions | null>(null), error = ref(''), busy = ref(false)
const separator = ref(','), taskType = ref('classification'), target = ref(''), positiveLabel = ref('')
const buyerMode = ref('SIMULATED_BUYER_SCENARIO'), simulationConfirmed = ref(false), explicitEvaluation = ref(false)
const splitMode = ref('record'), entityColumn = ref(''), timeColumn = ref(''), idColumns = ref<string[]>([])
const seed = ref(101), modelSeeds = ref('101,202,303'), targetUnit = ref('target_units')
const run = ref<MeasurementRun | null>(null), polling = ref(false), evidenceBusy = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined
let disposed = false
const active = computed(() => run.value?.status === 'QUEUED' || run.value?.status === 'RUNNING')
const locked = computed(() => busy.value || active.value || evidenceBusy.value)
const columns = computed(() => datasets.candidate?.columns ?? [])
const visibleRoles = computed(() => roles.filter(r => r.key === 'candidate' || (r.key === 'buyer' && buyerMode.value === 'REAL_BUYER') || ((r.key === 'validation' || r.key === 'test') && explicitEvaluation.value)))
const report = computed(() => run.value?.report ?? null)
const featureColumns = computed(() => target.value ? columns.value.map(column => column.name).filter(name => ![target.value, splitMode.value==='entity'?entityColumn.value:'', splitMode.value==='time'?timeColumn.value:'', ...idColumns.value].includes(name)) : undefined)
const metric = (x: unknown) => typeof x === 'number' && Number.isFinite(x) ? x.toFixed(6) : 'UNKNOWN'
function utilityText(value: unknown): string {
  if (!value || typeof value !== 'object') return 'NOT_EVALUATED'
  const row = value as Row
  const mean = row.delta_u
  if (typeof mean !== 'number') return 'NOT_EVALUATED'
  const lower = row.ci_lower
  const upper = row.ci_upper
  return `${metric(mean)}；区间 [${metric(lower)}, ${metric(upper)}]`
}
function clearResult() { run.value = null; error.value = ''; if (timer) clearTimeout(timer) }
function pick(role: Role, event: Event) {
  files[role] = (event.target as HTMLInputElement).files?.[0] ?? null
  datasets[role] = null
  if (role === 'candidate') { target.value = ''; entityColumn.value = ''; timeColumn.value = ''; idColumns.value = [] }
  clearResult()
}
function changed() { clearResult() }
function changeType() { positiveLabel.value = ''; changed() }
function changeBuyer() { simulationConfirmed.value = false; explicitEvaluation.value = false; changed() }
async function refreshOptions() {
  busy.value = true; error.value = ''
  try {
    options.value = await measurementApi.options()
    seed.value = options.value.default_protocol.seed
    modelSeeds.value = options.value.default_protocol.model_seeds.join(',')
  } catch (e) { error.value = (e as Error).message } finally { busy.value = false }
}
async function upload(role: Role) {
  const file = files[role]
  if (!file) { error.value = '请先选择本地CSV文件。'; return }
  busy.value = true; clearResult()
  try { datasets[role] = await measurementApi.upload(file, separator.value) }
  catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function poll() {
  if (!run.value?.run_id || disposed || polling.value) return
  if (timer) clearTimeout(timer)
  polling.value = true
  const id = run.value.run_id
  try {
    const latest = await measurementApi.run(id)
    if (disposed || run.value?.run_id !== id) return
    run.value = latest; error.value = ''
    if (latest.status === 'QUEUED' || latest.status === 'RUNNING') timer = setTimeout(poll, 1000)
  } catch (e) { error.value = (e as Error).message }
  finally { polling.value = false }
}
async function start(trustOnly = false) {
  error.value = ''
  if (!options.value) { error.value = '协议选项尚未加载，请先重新读取。'; return }
  if (!datasets.candidate) { error.value = '请先导入候选CSV。'; return }
  if (!trustOnly) {
  if (!target.value) { error.value = '请选择目标列。'; return }
  if (taskType.value === 'classification' && !positiveLabel.value.trim()) { error.value = '分类任务需要填写精确的正类标签。'; return }
  if (buyerMode.value === 'SIMULATED_BUYER_SCENARIO' && !simulationConfirmed.value) { error.value = '请明确确认模拟买方场景。'; return }
  if (buyerMode.value === 'REAL_BUYER' && !datasets.buyer) { error.value = '真实买方场景需要导入买方已有数据D0。'; return }
  if (explicitEvaluation.value && (!datasets.validation || !datasets.test)) { error.value = '独立评估需要同时导入验证和测试CSV。'; return }
  if (splitMode.value === 'entity' && !entityColumn.value) { error.value = '实体划分需要选择实体列。'; return }
  if (splitMode.value === 'time' && !timeColumn.value) { error.value = '时间划分需要选择时间列。'; return }
  }
  const seeds = trustOnly ? options.value.default_protocol.model_seeds : modelSeeds.value.split(',').map(x => Number(x.trim()))
  if (!trustOnly) {
  if (!modelSeeds.value.trim() || seeds.length > 3 || seeds.some(x => !Number.isSafeInteger(x) || x < 0 || x > 4294967295) || new Set(seeds).size !== seeds.length) { error.value = '模型种子请填写1至3个互不重复的非负整数（最大4294967295），用逗号分隔。'; return }
  if (!Number.isSafeInteger(seed.value) || seed.value < 0 || seed.value > 4294967294) { error.value = '划分种子需要是0至4294967294的整数。'; return }
  }
  busy.value = true; run.value = null
  try {
    run.value = await measurementApi.create({ candidate_id: datasets.candidate.dataset_id, expected_candidate_hash: datasets.candidate.content_hash,
      buyer_id: !trustOnly && buyerMode.value === 'REAL_BUYER' ? datasets.buyer?.dataset_id : null,
      validation_id: !trustOnly && explicitEvaluation.value ? datasets.validation?.dataset_id : null, test_id: !trustOnly && explicitEvaluation.value ? datasets.test?.dataset_id : null,
      task: { target: trustOnly ? null : target.value, task_type: trustOnly ? null : taskType.value, positive_label: !trustOnly && taskType.value === 'classification' ? positiveLabel.value : null,
        buyer_mode: trustOnly ? 'SIMULATED_BUYER_SCENARIO' : buyerMode.value, split_mode: trustOnly ? 'record' : splitMode.value,
        entity_column: !trustOnly && splitMode.value === 'entity' ? entityColumn.value : null,
        time_column: !trustOnly && splitMode.value === 'time' ? timeColumn.value : null, id_columns: trustOnly ? [] : idColumns.value,
        seed: trustOnly ? options.value.default_protocol.seed : seed.value, model_seeds: seeds,
        n_bootstrap: options.value.default_protocol.n_bootstrap, alpha: options.value.default_protocol.alpha, target_unit: trustOnly ? 'target_units' : targetUnit.value } })
    if (active.value) timer = setTimeout(poll, 1000)
  } catch (e) { error.value = (e as Error).message } finally { busy.value = false }
}
onMounted(refreshOptions)
onUnmounted(() => { disposed = true; if (timer) clearTimeout(timer) })
</script>

<template>
<section class="panel measurement" data-testid="task-measurement">
 <div class="panel-heading"><div><h2>为新CSV运行一次任务价值测量</h2><p class="muted small">固定输入版本与任务协议，比较买方使用候选数据前后的损失变化。</p></div><button class="button secondary" :disabled="locked" @click="refreshOptions">重新读取协议</button></div>
 <p class="notice">分类使用 Log Loss，回归使用 MAE。效用ΔU为基线损失减去增广损失，正值表示损失降低；两类任务量纲不同。任务效用、数据可信性与价格分别解释。</p>
 <fieldset :disabled="locked" class="measurement-fields">
  <h3>1. 选择任务和买方情境</h3>
  <div class="form-grid">
   <label>任务类型<select v-model="taskType" aria-label="任务测量类型" @change="changeType"><option value="classification">二分类 · Log Loss</option><option value="regression">回归 · MAE</option></select></label>
   <label>买方情境<select v-model="buyerMode" aria-label="任务测量买方模式" @change="changeBuyer"><option value="SIMULATED_BUYER_SCENARIO">模拟买方：由候选数据划分D0</option><option value="REAL_BUYER">真实买方：上传自己的D0</option></select></label>
  </div>
  <label v-if="buyerMode==='SIMULATED_BUYER_SCENARIO'" class="check-label"><input v-model="simulationConfirmed" type="checkbox" aria-label="确认模拟买方场景" @change="changed">我确认这是模拟买方场景，不代表真实买方已有数据或现实业务收益。</label>
  <label v-if="buyerMode==='REAL_BUYER'" class="check-label"><input v-model="explicitEvaluation" type="checkbox" aria-label="使用独立验证测试CSV" @change="changed">另行上传独立验证与测试CSV（两者必须同时提供）；默认从买方数据划分</label>
  <p v-else class="footnote">模拟场景只使用候选CSV，在后端划分D0、增广数据、验证集与测试集。</p>
  <h3>2. 导入本地CSV并固定字节版本</h3>
  <label class="separator-label">CSV分隔符<select v-model="separator" aria-label="任务测量CSV分隔符" @change="changed"><option value=",">逗号</option><option value=";">分号</option><option :value="'\t'">制表符</option></select></label>
  <div class="upload-grid">
   <div v-for="role in visibleRoles" :key="role.key" class="upload-card">
    <label>{{role.label}}<input :aria-label="role.label+'CSV'" type="file" accept=".csv,text/csv" @change="pick(role.key,$event)"></label>
    <button class="button secondary compact" :disabled="!files[role.key]" :data-testid="'upload-'+role.key" @click="upload(role.key)">导入{{role.label}}</button>
    <div v-if="datasets[role.key]" class="upload-receipt" :data-testid="'dataset-'+role.key"><strong>{{datasets[role.key]!.original_name??datasets[role.key]!.dataset_id}}</strong><p>{{datasets[role.key]!.row_count}}行 × {{datasets[role.key]!.column_count}}列 · {{datasets[role.key]!.version}}</p><p class="mono">SHA-256 {{datasets[role.key]!.content_hash}}</p></div>
   </div>
  </div>
  <p class="footnote">CSV传给本机服务，不上传公网。格式为UTF-8；当前协议限单CSV {{options?.limits.bytes?metric(options.limits.bytes/1048576):'UNKNOWN'}} MiB、各输入合计{{options?.limits.combined_rows??'UNKNOWN'}}行、{{options?.limits.columns??'UNKNOWN'}}列。格式、合法标签与划分条件由后端核验；导入成功不等于数据内容真实。</p>
  <template v-if="datasets.candidate">
   <h3>3. 固定目标、隔离字段与划分方式</h3>
   <div class="form-grid">
    <label>目标列<select v-model="target" aria-label="任务测量目标列" @change="changed"><option value="">请选择目标</option><option v-for="column in columns" :key="column.name" :value="column.name">{{column.name}} · {{column.dtype}}</option></select></label>
    <label v-if="taskType==='classification'">正类标签（与CSV完全一致）<input v-model="positiveLabel" aria-label="任务测量正类标签" placeholder="例如 1 或 yes" @input="changed"></label>
    <label v-else>目标单位<input v-model="targetUnit" aria-label="任务测量目标单位" placeholder="target_units" @input="changed"></label>
    <label>划分方式<select v-model="splitMode" aria-label="任务测量划分方式" @change="changed"><option value="record">按记录随机划分</option><option value="entity">按实体隔离划分</option><option value="time">按时间先后划分</option></select></label>
    <label v-if="splitMode==='entity'">实体列<select v-model="entityColumn" aria-label="任务测量实体列" @change="changed"><option value="">请选择实体列</option><option v-for="column in columns" :key="column.name" :value="column.name">{{column.name}}</option></select></label>
    <label v-if="splitMode==='time'">时间列<select v-model="timeColumn" aria-label="任务测量时间列" @change="changed"><option value="">请选择时间列</option><option v-for="column in columns" :key="column.name" :value="column.name">{{column.name}}</option></select></label>
   </div>
   <fieldset class="id-columns"><legend>ID列（从模型特征中排除，可多选）</legend><div class="id-options"><label v-for="column in columns" :key="column.name" class="check-label"><input v-model="idColumns" type="checkbox" :value="column.name" :aria-label="'排除ID列 '+column.name" @change="changed">{{column.name}}</label></div></fieldset>
   <p v-if="splitMode==='record'" class="notice warning" data-testid="measurement-independence-warning">按记录划分不能证明实体或时间独立，独立性保持UNKNOWN。存在重复实体或时间顺序时，应选择相应隔离方式。</p>
   <h3>4. 固定模型与随机协议后运行</h3>
   <div class="form-grid">
    <label>固定学习器<input :value="options?.learners?.[taskType]??'等待协议'" aria-label="任务测量固定学习器" readonly></label>
    <label>损失函数<input :value="taskType==='classification'?'Log Loss · nats/record':'MAE · '+targetUnit" aria-label="任务测量损失函数" readonly></label>
    <label>划分种子<input v-model.number="seed" aria-label="任务测量划分种子" type="number" min="0" step="1" @input="changed"></label>
    <label>模型种子（逗号分隔）<input v-model="modelSeeds" aria-label="任务测量模型种子" @input="changed"></label>
   </div>
   <p class="footnote">Bootstrap={{options?.default_protocol.n_bootstrap??'UNKNOWN'}}，α={{options?.default_protocol.alpha??'UNKNOWN'}}。模型与损失固定；验证集用于建议，测试集仅作冻结后评价。多次调整配置再择优报告会产生选择偏差，应保留全部运行。</p>
  </template>
  <div class="actions"><button class="button" data-testid="start-measurement" :disabled="!options" @click="start(false)">运行真实任务测量</button><button class="button secondary" data-testid="trust-only-measurement" :disabled="!options||!datasets.candidate" @click="start(true)">仅检查可信性（无任务资料）</button></div>
  <p class="footnote">无标签或没有目标任务时，可仅检查可信性：不训练模型，任务价值保持NOT_EVALUATED，保留缺失原因。</p>
 </fieldset>
 <p v-if="busy" class="notice" role="status">正在提交或导入本地服务数据…</p>
 <p v-if="error" class="notice error" role="alert">{{error}}</p>
</section>
<DatasetEvidenceAudit :candidate="datasets.candidate" :run-id="run?.status==='COMPLETED'?run.run_id:undefined" :feature-columns="featureColumns" :disabled="busy||active" @busy="evidenceBusy=$event"/>
<section v-if="run" class="panel measurement" data-testid="measurement-run">
 <div class="panel-heading"><h2>实际运行状态 <StatusBadge :value="run.status"/></h2><button v-if="active" class="button secondary" :disabled="polling" @click="poll">刷新运行状态</button></div>
 <dl class="details-grid"><dt>运行标识</dt><dd data-testid="measurement-run-id">{{run.run_id}}</dd><dt>执行状态</dt><dd data-testid="measurement-run-status">{{run.status}}</dd></dl>
 <p v-if="active" class="notice" role="status">后端正在排队或训练；每秒读取一次真实状态，完成后显示保存的报告。</p>
 <p v-if="run.error" class="notice error" role="alert">{{run.error.code}}：{{run.error.message}}</p>
 <template v-if="report">
  <div class="panel-heading"><h3>任务测量结果 <StatusBadge :value="report.status"/></h3><div class="actions"><a class="button secondary" :href="measurementApi.reportUrl(run.run_id,'json')" download>导出任务JSON</a><a class="button secondary" :href="measurementApi.reportUrl(run.run_id,'md')" download>导出中文研究报告</a></div></div>
  <dl class="details-grid"><dt>测量结论</dt><dd data-testid="measurement-result-status">{{report.status}}</dd><dt>实际拟合次数</dt><dd data-testid="measurement-fit-count">{{typeof report.fit_count==='number'?report.fit_count:'UNKNOWN'}}</dd><dt>验证价值证据状态</dt><dd data-testid="measurement-value-status">{{report.validation?.value_status??'NOT_EVALUATED'}}</dd><dt>损失单位</dt><dd data-testid="measurement-loss-unit">{{report.validation?.loss_name??'UNKNOWN'}} / {{report.validation?.loss_unit??'UNKNOWN'}}</dd><dt>验证基线/增广损失</dt><dd data-testid="measurement-validation-loss">{{metric(report.validation?.base_loss_mean)}} / {{metric(report.validation?.asset_loss_mean)}}</dd><dt>验证集ΔU与区间</dt><dd data-testid="measurement-validation-utility">{{utilityText(report.validation)}}</dd><dt>测试集ΔU与区间</dt><dd data-testid="measurement-test-utility">{{utilityText(report.test)}}（事后评价）</dd><dt>可信与价值</dt><dd data-testid="measurement-trust">{{report.trust?.decision??report.trust?.status??'UNKNOWN'}} · 独立解释</dd><dt>货币估价</dt><dd>NOT_EVALUATED · 不将效用换算为市场价格</dd></dl>
  <p v-if="report.status==='NOT_EVALUATED'" class="notice warning" data-testid="measurement-not-evaluated">该输入或任务未满足可测量条件，保留NOT_EVALUATED与后端原因，不借用其他数据集的结果。</p>
  <p class="notice" data-testid="measurement-value-explanation">{{valueStateText(report.validation?.value_status??'NOT_EVALUATED')}}。五类证据状态分别为正证据、负证据、不确定、模型不可靠、未测量；不确定不表示已经证明无价值。</p>
  <p class="notice" data-testid="measurement-truth-boundary">来源真实性：{{report.source_authenticity??report.trust?.source_authenticity??'UNKNOWN'}}；内容真实性：{{report.content_authenticity??report.trust?.content_authenticity??'UNKNOWN'}}；正式事实核验：{{report.formal_factual_status??'UNKNOWN'}}。可信证据独立于任务效用：{{report.trust?.independent_of_task_value??'UNKNOWN'}}。</p>
  <div v-if="report.reasons?.length" class="notice warning" data-testid="measurement-reasons"><strong>后端保留的未评估原因</strong><ul><li v-for="reason in report.reasons" :key="reason">{{reason}}</li></ul></div>
  <p class="notice warning">区间是在本次输入、划分、学习器与抽样假设下的条件区间，不是无条件未来保证。来源声明及结构检查不证明内容真实；正任务效用不补齐独立事实审计。</p>
  <JsonDetails :data="report.task" title="固定任务与损失单位"/>
  <JsonDetails :data="report.input_binding" title="输入版本、SHA-256与任务绑定"/>
  <JsonDetails :data="report.splits" title="实际划分、样本规模与独立性"/>
  <JsonDetails :data="report.isolation_assessment" title="实体/时间隔离核查与未知边界"/>
  <JsonDetails :data="report.marginal_utility" title="完整边际效用、条件区间与状态" open/>
  <JsonDetails :data="report.validation" title="验证集模型损失与基线"/>
  <JsonDetails :data="report.test" title="测试集事后损失（不用于选择）"/>
  <JsonDetails :data="report.trust" title="独立的可信证据与未核验事项"/>
  <JsonDetails :data="report.recommendation" title="基于验证集的建议及条件"/>
  <JsonDetails :data="report" title="完整后端研究报告"/>
 </template>
</section>
</template>

<style scoped>
.measurement{overflow-wrap:anywhere}.measurement-fields{border:0;padding:0;margin:0;min-width:0}.measurement-fields h3{margin-top:24px}.measurement dd{min-width:0;overflow-wrap:anywhere}.separator-label{max-width:240px;margin-bottom:16px}.upload-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.upload-card{border:1px solid var(--border);border-radius:6px;padding:15px;min-width:0;background:#fbfcfc}.upload-card .button{margin-top:12px}.upload-receipt{margin-top:12px;font-size:10px;color:#617f88}.upload-receipt strong{font-size:11px}.upload-receipt .mono{font-size:9px;margin-top:5px}.id-columns{border:1px solid var(--border);border-radius:6px;padding:10px 14px;min-width:0}.id-columns legend{font-size:11px;color:#657d8a;padding:0 6px}.id-options{display:flex;flex-wrap:wrap;gap:8px 20px}.id-options .check-label{margin:3px 0}.check-label{align-items:flex-start}.check-label input{flex:none;margin-top:4px}.measurement .panel-heading h3{margin:0}@media(max-width:700px){.upload-grid{grid-template-columns:1fr}.measurement .panel-heading{display:block}.measurement .panel-heading>.button{margin-top:12px}} 
</style>
