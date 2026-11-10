<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ApiError, type Row } from '../api'
import { casesApi, caseAsset, fileData, publicError, readable, uploadLimits, type EvidenceCase, type LocalFile } from '../visualCases'
import JsonDetails from '../components/JsonDetails.vue'
import StatusBadge from '../components/StatusBadge.vue'

const capabilities = ref<Row | null>(null), cases = ref<EvidenceCase[]>([]), selected = ref<EvidenceCase | null>(null)
const loading = ref(false), registering = ref(false), running = ref(false), reading = ref(false)
const error = ref(''), registrationError = ref(''), executionError = ref(''), note = ref(''), imageError = ref(false)
const csv = ref<LocalFile | null>(null), images = ref<(LocalFile & { evidence_id: string })[]>([])
const selectedEvidenceId = ref(''), execution = ref<Row | null>(null), validation = ref<Row | null>(null)
const externalRunConsent = ref(false), replayId = ref('')
const form = ref({ asset_id: '', asset_version: '', task_id: '', entity_key: '', entity_value: '', field: '', observation_target: '', observation_rule: '', capture_time: '', description: '', url: '', license: '', independence_declaration: '', local: false, external: false })
const evidence = computed<Row[]>(() => selected.value?.evidence ?? [])
const currentEvidence = computed(() => evidence.value.find(e => e.evidence_id === selectedEvidenceId.value))
const assessment = computed<Row>(() => selected.value?.assessment ?? {})
const reviews = computed<Row[]>(() => selected.value?.reviews ?? [])
const executions = computed<Row[]>(() => selected.value?.executions ?? [])
const replayable = computed(() => executions.value.filter(r => r.status === 'LIVE' && r.review && r.evidence_id === selectedEvidenceId.value))
const imageUrl = computed(() => selected.value && selectedEvidenceId.value ? casesApi.evidenceUrl(selected.value.case_id, selectedEvidenceId.value) : '')
const ready = computed(() => csv.value && images.value.length > 0 && form.value.local && ['asset_id', 'asset_version', 'task_id', 'entity_key', 'entity_value', 'field', 'observation_target', 'observation_rule', 'description', 'license', 'independence_declaration'].every(key => String(form.value[key as keyof typeof form.value]).trim()))
let selectSequence = 0

async function load() {
  loading.value = true; error.value = ''
  const results = await Promise.allSettled([casesApi.capabilities(), casesApi.list()])
  if (results[0].status === 'fulfilled') capabilities.value = results[0].value
  else error.value = publicError(results[0].reason)
  if (results[1].status === 'fulfilled') cases.value = results[1].value.cases
  else error.value += (error.value ? '；' : '') + publicError(results[1].reason)
  loading.value = false
}
async function choose(id: string) {
  const sequence = ++selectSequence
  error.value = ''; executionError.value = ''; execution.value = null; validation.value = null; selected.value = null; externalRunConsent.value = false; replayId.value = ''; imageError.value = false; note.value = ''
  try {
    const result = await casesApi.get(id)
    if (sequence !== selectSequence) return
    selected.value = result; selectedEvidenceId.value = result.evidence?.[0]?.evidence_id ?? ''
  } catch (e) { if (sequence === selectSequence) error.value = publicError(e) }
}
async function readFiles(event: Event, kind: 'csv' | 'image') {
  const input = event.target as HTMLInputElement, files = Array.from(input.files ?? [])
  registrationError.value = ''; reading.value = true
  try {
    if (kind === 'csv') { csv.value = null; if (files[0]) csv.value = await fileData(files[0], 'csv') }
    else {
      images.value = []
      if (files.length > uploadLimits.images) throw new Error('每个案例最多4幅原始图像；请保留明确的实体与证据对应关系。')
      images.value = await Promise.all(files.map(async (file, i) => ({ ...await fileData(file, 'image'), evidence_id: 'evidence-' + (i + 1) })))
    }
  } catch (e) { registrationError.value = publicError(e); input.value = '' }
  finally { reading.value = false }
}
async function register() {
  registrationError.value = ''; note.value = ''
  if (!ready.value) { registrationError.value = '请提供CSV、原始图像、完整观察规则、来源使用条件，并确认本地处理授权。'; return }
  registering.value = true
  try {
    const f = form.value
    const result = await casesApi.create({ asset_id: f.asset_id, asset_version: f.asset_version, asset_sha256: csv.value!.sha256, dataset_csv_base64: csv.value!.data_base64,
      task_id: f.task_id, entity_key: f.entity_key, entity_value: f.entity_value, field: f.field, observation_target: f.observation_target, observation_rule: f.observation_rule, capture_time: f.capture_time.trim() || null,
      source: { description: f.description, url: f.url.trim(), license: f.license, independence_declaration: f.independence_declaration },
      authorization: { local_processing: f.local, external_model_upload: f.external }, evidence: images.value.map(({ size, ...image }) => image) })
    await load(); await choose(result.case_id); note.value = '案例已在本地注册并绑定精确版本；来源声明尚不等于身份认证或事实确认。'
  } catch (e) { registrationError.value = publicError(e) }
  finally { registering.value = false }
}
async function validate() {
  if (!selected.value || !selectedEvidenceId.value) return
  running.value = true; executionError.value = ''; validation.value = null
  try { validation.value = await casesApi.validate(selected.value.case_id, selectedEvidenceId.value); selected.value = await casesApi.get(selected.value.case_id, caseAsset(selected.value)) }
  catch (e) { executionError.value = publicError(e) }
  finally { running.value = false }
}
async function execute(mode: 'LIVE' | 'REPLAY') {
  if (!selected.value || !selectedEvidenceId.value) return
  const bound = caseAsset(selected.value), caseId = selected.value.case_id
  running.value = true; executionError.value = ''; execution.value = null; note.value = ''
  try {
    const result = await casesApi.execute(caseId, { mode, asset_sha256: bound.asset_sha256, asset_version: bound.asset_version, evidence_id: selectedEvidenceId.value,
      external_upload_authorized: externalRunConsent.value, ...(mode === 'REPLAY' ? { run_id: replayId.value } : {}) })
    execution.value = result
    if (result.execution_mode !== mode || (mode === 'LIVE' && (result.status !== 'LIVE' || result.model_invoked !== true))) throw new Error('接口没有返回经确认的本次LIVE模型执行；未将结果标作新审阅成功。')
    note.value = mode === 'LIVE' ? '本次LIVE调用已返回；请核对提供方、模型、请求ID、原图哈希及新审阅结果。AI结论不是独立事实认证。' : 'REPLAY：仅加载该版本的历史执行记录，本次没有调用模型。'
  } catch (e) {
    executionError.value = publicError(e)
    if (e instanceof ApiError) {
      const payload = e.details as Row | undefined
      if (payload) execution.value = typeof payload.detail === 'object' ? payload.detail : payload
    }
  } finally {
    try { selected.value = await casesApi.get(caseId, bound) } catch (e) { executionError.value += (executionError.value ? '；' : '') + publicError(e) }
    externalRunConsent.value = false; running.value = false
  }
}
function selectEvidence() { imageError.value = false; validation.value = null; execution.value = null; executionError.value = ''; note.value = '' }
onMounted(load)
</script>

<template>
<section class="panel new-visual-intro"><div class="panel-heading"><div><h2>新数据集 · AI视觉证据审阅</h2><p class="muted">为具体实体和字段注册真实图像，按自己的观察规则审阅；不预设桥梁或50米规则。</p></div><button class="button secondary" :disabled="loading || running || registering" data-testid="cases-refresh" @click="load">刷新API状态</button></div>
 <p class="notice">图像与来源声明、本地完整性核验、AI窄范围判断、独立事实确认是不同层次。AI一致性不是真实性准确率；重复判读不增加独立物理样本。</p>
 <div class="live-capability" data-testid="live-capability"><StatusBadge :value="capabilities?.status ?? 'UNKNOWN'"/><span>LIVE {{capabilities?.live_available?'已配置':'当前不可用'}} · {{readable(capabilities?.reason)}}</span><span class="small">{{readable(capabilities?.adapter?.provider)}} / {{readable(capabilities?.adapter?.model)}}</span></div>
 <p class="footnote">未配置模型时保留LIVE_UNAVAILABLE。失败和超时不会自动切换到REPLAY；接口工程不等于已完成在线审阅。</p>
 <p v-if="loading" role="status">正在读取案例与模型能力…</p><p v-if="error" class="notice error" role="alert" data-testid="cases-error">{{error}}</p>
</section>
<section class="panel"><div class="panel-heading"><div><h2>1. 注册数据集与视觉证据</h2><p class="small muted">文件仅导入本地研究工作空间；外发模型需要单独授权，不接收API密钥。</p></div></div>
 <form @submit.prevent="register" data-testid="visual-case-form">
 <div class="case-form-grid"><label>数据集ID<input v-model="form.asset_id" aria-label="视觉数据集ID" maxlength="80" required placeholder="例如 local-sign-inventory"></label><label>固定版本<input v-model="form.asset_version" aria-label="视觉数据集版本" maxlength="120" required placeholder="例如 v1"></label><label>观察任务ID<input v-model="form.task_id" aria-label="视觉任务ID" maxlength="80" required placeholder="例如 sign-shape-check"></label>
 <label>CSV数据集<input type="file" accept=".csv,text/csv" aria-label="视觉案例CSV" @change="readFiles($event,'csv')"><small>≤2MiB / ≤200列；实体匹配键必须真实存在且唯一。</small></label><label>原始图像<input type="file" multiple accept=".png,.jpg,.jpeg,image/png,image/jpeg" aria-label="视觉证据图像" @change="readFiles($event,'image')"><small>最多4图；每图≤5MiB / 2000万像素；服务端另验文件内容。</small></label></div>
 <div v-if="csv" class="notice file-receipt" data-testid="csv-file-receipt"><strong>{{csv.filename}}</strong> · {{csv.size}}字节<code>{{csv.sha256}}</code><span>SHA-256由实际选择的原始字节计算；结构与匹配关系仍由服务端核验。</span></div>
 <div v-for="(image,index) in images" :key="index" class="case-image-input"><label>证据ID<input v-model="image.evidence_id" :aria-label="'证据ID'+(index+1)" required></label><div>{{image.filename}} · {{image.size}}字节<code>{{image.sha256}}</code></div></div>
 <div class="case-form-grid"><label>实体匹配字段<input v-model="form.entity_key" aria-label="视觉实体匹配字段" required placeholder="CSV中的唯一实体键"></label><label>实体键值<input v-model="form.entity_value" aria-label="视觉实体键值" required placeholder="指定一条现实实体记录"></label><label>待核验字段<input v-model="form.field" aria-label="视觉待核验字段" required placeholder="例如 sign_shape"></label></div>
 <div class="case-form-grid two"><label>窄范围观察目标<textarea v-model="form.observation_target" aria-label="视觉观察目标" required rows="2" placeholder="说明图像中能够观察的具体属性"></textarea></label><label>比较规则与无法判定条件<textarea v-model="form.observation_rule" aria-label="视觉观察规则" required rows="3" placeholder="明确可见范围、时间、允许差异、遮挡及歧义处理；不直接套用其他领域规则"></textarea></label></div>
 <div class="case-form-grid"><label>拍摄／采集时间（可未知）<input v-model="form.capture_time" aria-label="视觉证据拍摄时间" placeholder="ISO日期或时间；未知留空"></label><label>来源描述<input v-model="form.description" aria-label="视觉证据来源描述" required placeholder="原始产生者、取得过程"></label><label>来源URL（可选）<input v-model="form.url" aria-label="视觉证据来源URL" placeholder="https://..."></label></div>
 <div class="case-form-grid two"><label>许可证／研究使用条件<textarea v-model="form.license" aria-label="视觉证据使用条件" rows="2" required placeholder="合法取得与可用于本地研究的依据"></textarea></label><label>独立性声明及共同上游<textarea v-model="form.independence_declaration" aria-label="视觉证据独立性声明" rows="2" required placeholder="可填未知；来源声明不自动获得认证"></textarea></label></div>
 <div class="case-authorizations"><label><input v-model="form.local" type="checkbox" aria-label="允许本地处理">我有权使用上述CSV和图像，并授权在本地研究环境处理。</label><label><input v-model="form.external" type="checkbox" aria-label="允许注册案例外发模型">允许该案例的图像发送给服务端明确配置的模型提供方；执行时还需再次确认。</label></div>
 <p class="footnote">CSV版本由字节哈希固定。同ID同版本不能更换内容；新版本需注册新案例，不自动继承旧审阅。</p>
 <button class="button" data-testid="register-visual-case" :disabled="registering || reading || !ready">{{reading?'正在计算原始字节哈希…':registering?'正在注册与核验…':'注册新视觉案例'}}</button>
 <p v-if="registrationError" class="notice error" role="alert" data-testid="registration-error">{{registrationError}}</p>
 </form>
</section>
<section class="panel"><div class="panel-heading"><h2>2. 选择版本与核验原图</h2></div>
 <p v-if="!cases.length" class="muted" data-testid="no-visual-cases">暂无新注册案例。未导入图像不会生成视觉成功记录。</p><div class="case-list" role="group" aria-label="已注册视觉案例"><button v-for="item in cases" :key="item.case_id" class="button secondary" :aria-pressed="selected?.case_id===item.case_id" :data-testid="'case-'+item.case_id" :disabled="running" @click="choose(item.case_id)">{{caseAsset(item).asset_id}} · {{caseAsset(item).asset_version}}<small>{{item.case_id}}</small></button></div>
 <template v-if="selected"><dl class="details-grid case-binding" data-testid="new-case-binding"><dt>数据集 / 版本</dt><dd>{{caseAsset(selected).asset_id}} / {{caseAsset(selected).asset_version}}</dd><dt>实际数据SHA-256</dt><dd class="mono">{{caseAsset(selected).asset_sha256}}</dd><dt>协议SHA-256</dt><dd class="mono">{{readable(selected.protocol_sha256)}}</dd><dt>来源状态</dt><dd data-testid="source-status">{{readable(currentEvidence?.source_status ?? selected.source_status)}} · 来源声明与字节核验都不等于事实确认。</dd></dl>
 <div class="case-evidence-layout"><div><label>原始证据<select v-model="selectedEvidenceId" aria-label="选择新视觉证据" :disabled="running" @change="selectEvidence"><option v-for="item in evidence" :key="item.evidence_id" :value="item.evidence_id">{{item.evidence_id}} · {{item.filename}}</option></select></label><div class="case-image-frame"><img v-if="imageUrl && !imageError" :src="imageUrl" alt="当前精确版本所绑定的原始视觉证据" data-testid="new-case-evidence-image" @error="imageError=true"><div v-else class="state-box" role="alert">原始图像未加载，不能将其当作审阅成功。<button class="button secondary" @click="imageError=false">重试原图</button></div></div><dl class="details-grid"><dt>原始图SHA-256</dt><dd class="mono">{{readable(currentEvidence?.sha256)}}</dd><dt>像素SHA-256</dt><dd class="mono">{{readable(currentEvidence?.pixel_sha256)}}</dd><dt>实际尺寸</dt><dd>{{readable(currentEvidence?.width)}} × {{readable(currentEvidence?.height)}}</dd></dl><button class="button secondary" :disabled="running" data-testid="validate-new-evidence" @click="validate">核验文件完整性与版本绑定</button><JsonDetails v-if="validation" :data="validation" title="本地验证收据：不认证来源身份或事实内容"/></div>
 <div><h3>固定观察协议</h3><dl class="details-grid"><dt>匹配实体</dt><dd>{{readable(selected.protocol?.entity_key)}} = {{readable(selected.protocol?.entity_value)}}</dd><dt>字段</dt><dd>{{readable(selected.protocol?.field)}}</dd><dt>观察目标</dt><dd>{{readable(selected.protocol?.observation_target)}}</dd><dt>比较规则</dt><dd>{{readable(selected.protocol?.observation_rule)}}</dd><dt>拍摄／采集时间</dt><dd>{{readable(selected.protocol?.capture_time)}} · 未认证的声明时间不能升级为已核实。</dd></dl><JsonDetails :data="selected.protocol?.source" title="来源、许可证与独立性声明"/><JsonDetails :data="selected.protocol" title="完整固定协议与四态输出约束"/><p class="notice warning">AI只能分析此图、此版本、此实体和指定窄属性；不证明所有字段真实。来源身份认证与事实判断分开记录。</p></div></div>
 </template>
</section>
<section v-if="selected" class="panel"><h2>3. 执行AI视觉审阅与历史比较</h2><p class="small">LIVE读取本案真实图像并使用服务端模型适配器。REPLAY只读取以前的封存结果。</p><label class="case-consent"><input v-model="externalRunConsent" type="checkbox" aria-label="本次允许图像外发">我确认本次允许将所选图像发送给已配置的模型提供方（还须满足注册时授权）。</label>
 <div class="actions"><button class="button" data-testid="new-case-live" :disabled="running || !selectedEvidenceId" @click="execute('LIVE')">{{running?'正在执行，请勿重复提交…':'发起本次LIVE视觉审阅'}}</button><span class="small">{{capabilities?.live_available?'模型可用性以实际调用结果为准':'LIVE_UNAVAILABLE · 尚未配置可用模型，不能宣称实时审阅已成功'}}</span></div>
 <div v-if="replayable.length" class="actions replay-actions"><label>历史执行<select v-model="replayId" aria-label="选择视觉历史执行"><option value="" disabled>选择已完成的LIVE记录</option><option v-for="run in replayable" :key="run.run_id" :value="run.run_id">{{run.run_id}}</option></select></label><button class="button secondary" data-testid="new-case-replay" :disabled="running || !replayId" @click="execute('REPLAY')">REPLAY所选历史审阅</button></div>
 <p v-if="note" class="notice" role="status" data-testid="new-case-note">{{note}}</p><p v-if="executionError" class="notice error" role="alert" data-testid="new-case-execution-error">{{executionError}}</p>
 <div v-if="execution" class="case-execution" data-testid="new-case-execution"><div class="panel-heading"><h3>本次执行收据</h3><StatusBadge :value="execution.status"/></div><dl class="details-grid"><dt>模式</dt><dd>{{readable(execution.execution_mode)}}</dd><dt>本次调用模型</dt><dd>{{readable(execution.model_invoked)}}</dd><dt>自动回退</dt><dd>{{readable(execution.fallback_performed)}}</dd><dt>原因／错误类型</dt><dd>{{readable(execution.reason)}} / {{readable(execution.error_type)}}</dd><dt>提供方 / 模型</dt><dd>{{readable(execution.receipt?.provider)}} / {{readable(execution.receipt?.model)}}</dd><dt>请求ID / 调用时间</dt><dd>{{readable(execution.receipt?.request_id)}} / {{readable(execution.receipt?.called_at)}}</dd></dl><JsonDetails :data="execution" title="完整执行、失败或超时收据"/></div>
 <div class="table-wrap case-review-table" tabindex="0" role="region" aria-label="可横向滚动的逐项AI审阅结果"><table><thead><tr><th>审阅 / 执行者</th><th>原始证据</th><th>四态判断</th><th>观察依据与不确定性</th></tr></thead><tbody><tr v-for="review in reviews" :key="review.review_id ?? review.run_id"><td>{{readable(review.review_id ?? review.run_id)}}<small>{{readable(review.reviewer_type)}}</small></td><td>{{readable(review.evidence_id)}}</td><td><StatusBadge :value="review.outcome"/></td><td>{{readable(review.observation_basis)}}<p class="muted">{{readable(review.uncertainty_reason)}}</p></td></tr></tbody></table></div><p v-if="!reviews.length" class="muted" data-testid="no-new-reviews">该版本尚无已完成的AI审阅；未生成虚假的MATCH或真实性分数。</p><JsonDetails :data="reviews" title="全部原始审阅结果、分歧与结果哈希"/><JsonDetails :data="executions" title="全部执行历史：保留不可用、失败与超时"/>
</section>
<section v-if="selected" class="panel" data-testid="new-case-joint"><div class="panel-heading"><h2>4. 精确版本的可信—价值联合结论</h2><div class="actions"><a class="button secondary" :href="casesApi.reportUrl(selected.case_id,'md')" download data-testid="new-case-report-md">导出中文报告</a><a class="button secondary" :href="casesApi.reportUrl(selected.case_id,'json')" download data-testid="new-case-report-json">导出JSON证据链</a></div></div><div class="case-decision"><div><span>内容事实</span><strong data-testid="new-case-fact">{{readable(assessment.fact_status)}}</strong></div><div><span>条件可信判断</span><strong>{{readable(assessment.decision)}}</strong></div><div><span>指定任务价值</span><strong data-testid="new-case-value">{{readable(assessment.task_value?.status)}}</strong></div></div><p class="notice">没有独立事实真值时保持UNKNOWN，没有合法任务条件时保持NOT_EVALUATED。可信与任务价值并列解释，不将未校准的可信分乘为货币价格。</p><JsonDetails :data="selected.dataset" title="实际CSV结构与重复性：整行、去辅助ID内容、任务特征和事实状态分别记录"/><JsonDetails :data="assessment" title="来源验证、内容支持及不适用统计边界"/><JsonDetails :data="selected.deduplication" title="同源与同像素证据去重：重复AI审阅不增加物理样本"/><p class="notice warning" data-testid="new-case-coordination">协调状态：{{readable(selected.coordination_state)}}。AI意见不同不自动构成已验证证据冲突；自报收据不升级认证。</p><JsonDetails :data="assessment.supplementary_evidence_needed" title="分歧与后续补证要求"/><JsonDetails :data="selected.validations" title="实际可信验证收据；声明不作为认证依据"/><RouterLink class="button secondary" to="/task-measurement">进入新CSV真实任务价值测量</RouterLink></section>
</template>

<style scoped>
.new-visual-intro,.case-binding,.case-execution,.case-evidence-layout,.case-decision{overflow-wrap:anywhere;min-width:0;max-width:100%}.live-capability{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:18px 0}.case-form-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px;margin:16px 0}.case-form-grid.two{grid-template-columns:repeat(2,minmax(0,1fr))}label{display:flex;flex-direction:column;gap:7px;font-size:11px;font-weight:600;min-width:0}label small{font-size:10px;line-height:1.6;font-weight:400;color:#80929d}textarea{font:inherit;line-height:1.65;resize:vertical;min-height:75px;padding:10px;border:1px solid #d9e3e8;border-radius:5px;max-width:100%}.file-receipt{font-size:11px}.file-receipt code,.case-image-input code{display:block;font-size:10px;overflow-wrap:anywhere;margin:7px 0}.case-image-input{display:grid;grid-template-columns:180px minmax(0,1fr);gap:18px;padding:12px 0;border-bottom:1px solid #edf1f3;font-size:11px;align-items:center}.case-authorizations{display:grid;gap:12px;margin:22px 0}.case-authorizations label,.case-consent{flex-direction:row;align-items:flex-start;font-weight:400;line-height:1.7}.case-authorizations input,.case-consent input{width:auto;min-height:auto;flex-shrink:0;margin:3px 3px 0 0}.case-list{display:flex;flex-wrap:wrap;gap:10px;margin:15px 0}.case-list button{white-space:normal;overflow-wrap:anywhere;display:flex;flex-direction:column;text-align:left;align-items:flex-start;max-width:100%}.case-list small{display:block;font-size:9px;max-width:100%;overflow-wrap:anywhere}.case-list button[aria-pressed=true]{background:#edf7f3;border-color:#087c70;color:#087c70}.case-binding dd,.details-grid dd{min-width:0;overflow-wrap:anywhere}.case-evidence-layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:25px;margin-top:25px}.case-evidence-layout>div{min-width:0}.case-image-frame{margin:14px 0;background:#eef2f4;border:1px solid #dbe5ea;border-radius:6px;overflow:hidden}.case-image-frame img{width:100%;height:auto;display:block;max-height:650px;object-fit:contain}.case-consent{margin:15px 0}.replay-actions{margin:20px 0;align-items:flex-end}.replay-actions label{flex:1;min-width:0;max-width:100%}.replay-actions select{max-width:100%}.case-execution{padding:18px;border:1px solid #dbe5ea;border-radius:6px;margin:20px 0}.case-review-table{min-width:0;max-width:100%;overflow-x:auto;contain:inline-size}.case-review-table table{min-width:550px}.case-review-table td{max-width:350px;white-space:normal;overflow-wrap:anywhere}.case-review-table small{display:block;font-size:10px;margin-top:5px}.case-decision{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:20px 0}.case-decision>div{background:#f7fafb;border:1px solid #e0e9ed;padding:16px;border-radius:6px}.case-decision span{display:block;font-size:10px;color:#7a919d}.case-decision strong{display:block;font-size:14px;margin-top:9px;color:#3a5a69;overflow-wrap:anywhere}section:deep(.json-details),section:deep(pre),section:deep(summary){min-width:0;max-width:100%;overflow-wrap:anywhere}section:deep(pre){white-space:pre-wrap}button{max-width:100%}input,select{min-width:0;max-width:100%}.mono{font-size:10px}.case-evidence-layout .details-grid{grid-template-columns:100px minmax(0,1fr)}
@media(max-width:1050px){.case-form-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.case-evidence-layout{grid-template-columns:1fr}}
@media(max-width:650px){.case-form-grid,.case-form-grid.two,.case-decision,.case-image-input{grid-template-columns:1fr}.case-evidence-layout .details-grid,.case-binding{grid-template-columns:85px minmax(0,1fr)}.case-execution .details-grid{grid-template-columns:80px minmax(0,1fr)}.case-image-input{gap:10px}.replay-actions{flex-direction:column;align-items:stretch}.case-authorizations label,.case-consent{font-size:10px}}
</style>
