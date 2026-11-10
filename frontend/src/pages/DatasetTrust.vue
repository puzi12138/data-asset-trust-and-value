<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, request, type Row, type Selection } from '../api'
import AssetSelect from '../components/AssetSelect.vue'
import AsyncPanel from '../components/AsyncPanel.vue'
import JsonDetails from '../components/JsonDetails.vue'
import ValueResult from '../components/ValueResult.vue'
const mode=ref('csv'), selected=ref<Selection>({asset_id:'',version:''}), file=ref<File|null>(null)
const imported=ref<Row|null>(null), result=ref<Row|null>(null), source=ref(''), separator=ref(',')
const schema=ref('auto')
const loading=ref(false), error=ref(''), tasks=ref<Row[]>([]), task=ref('')
const names:Record<string,string>={structure:'结构质量',provenance:'来源可验证性',accuracy:'内容准确性证据',timeliness:'数据时效性',representativeness:'数据代表性',evidence_sufficiency:'证据充分性'}
const dimensions=computed(()=>Object.entries(result.value?.assessment?.dimensions??{}) as [string,Row][])
const fusion=computed(()=>result.value?.assessment?.fusion??{})
const pct=(value:unknown)=>typeof value==='number'?(100*value).toFixed(1)+'%':'UNKNOWN'
function changed(){result.value=null;error.value='';imported.value=null}
function pick(event:Event){file.value=(event.target as HTMLInputElement).files?.[0]??null;changed()}
async function evaluate(){loading.value=true;error.value='';result.value=null
 try {
  if(mode.value==='csv'){
   if(!file.value)throw new Error('请先选择一个本地 UTF-8 CSV。')
   if(file.value.size>2097152)throw new Error('CSV 超过 2 MiB；请使用明确记录的研究子集。')
   const query=new URLSearchParams({name:file.value.name,separator:separator.value})
   const response=await fetch('/api/datasets/import?'+query,{method:'POST',headers:{'Content-Type':'text/csv','X-Research-Request':'1'},body:file.value})
   const data=await response.json();if(!response.ok)throw new Error(String(data.detail??'本地导入失败'))
   imported.value=data
   result.value=await request(`/datasets/${data.dataset_id}/assess`,{source_declaration:source.value,schema_name:schema.value,task_id:task.value||null})
  }else{
   if(!selected.value.asset_id||!selected.value.version)throw new Error('请选择已登记的精确资产版本。')
   result.value=await request('/datasets/assess-asset',{...selected.value,task_id:task.value||null})
  }
 }catch(e){error.value=(e as Error).message}finally{loading.value=false}
}
onMounted(async()=>{try{tasks.value=(await api.tasks()).tasks??[]}catch(e){error.value=(e as Error).message}})
</script>
<template>
 <section class="panel">
  <h2>以固定版本的数据集为评估对象</h2>
  <p class="notice">签名只能验证来源绑定与完整性，不能证明内容真实；格式通过不能证明标签准确。缺失证据保留 UNKNOWN，不直接当作负面证据。可信度、任务效用与履约信用分别报告。</p>
  <fieldset :disabled="loading" style="border:0;padding:0;margin:0;min-width:0"><div class="form-grid"><label>输入方式<select v-model="mode" aria-label="可信评估输入方式" @change="changed"><option value="csv">导入本地 CSV</option><option value="asset">选择已交付资产</option></select></label><label>目标任务（可选）<select v-model="task" aria-label="可信评估目标任务" @change="result=null"><option value="">不指定任务 · 只评可信度</option><option v-for="t in tasks" :key="t.task_id" :data="t.task_id">{{t.task_id}}</option></select></label></div>
  <template v-if="mode==='csv'"><div class="form-grid"><label>UTF-8 CSV 文件<input aria-label="本地CSV文件" type="file" accept=".csv,text/csv" @change="pick"></label><label>分隔符<select v-model="separator" aria-label="CSV分隔符" @change="changed"><option value=",">逗号</option><option value=";">分号</option><option :value="'\t'">制表符</option></select></label><label>来源声明（不视为核实）<input v-model="source" @input="result=null" aria-label="来源声明" maxlength="500" placeholder="可留空；声明不会变成可信公钥或事实核验"></label><label>结构规则<select v-model="schema" aria-label="结构规则" @change="result=null"><option value="auto">按完整字段集识别 Bank / Wine，否则通用</option><option value="generic">通用 CSV（未提供领域规则）</option><option value="bank">Bank Marketing 显式规则（字段集须匹配）</option><option value="wine">Wine Quality 显式规则（字段集须匹配）</option></select></label></div><p class="footnote">仅保存在本地研究工作空间；不上传公网或第三方。限 2 MiB / 20,000 行 / 128 字段。通用 CSV 的格式规则与一致性规则未独立提供，保留 NOT_VERIFIED；领域规则适用也不代表内容真实。</p></template>
  <template v-else><AssetSelect v-model="selected" @update:model-value="result=null"/><p class="footnote">先通过原有“授权与交付”完成精确版本交付。只评估有 Verifier 签名收据、实际收到且哈希匹配的字节，不读取提供方路径。</p></template>
  <div class="actions"><button class="button" :disabled="loading" @click="evaluate">{{loading?'正在检查证据…':'执行数据集可信评估'}}</button></div></fieldset>
 </section>
 <AsyncPanel :loading="loading" :error="error" @retry="evaluate">
 <template v-if="result">
  <section class="panel" data-testid="dataset-assessment"><div class="panel-heading"><h2>多维可信评价</h2><a class="button secondary" :href="`#/dataset-audit?report=${result.report_id}`">使用此报告建立风险审计</a><div class="actions"><a class="button secondary" :href="`/api/datasets/reports/${result.report_id}/export`" download>导出可信报告 JSON</a><a class="button secondary" :href="`/api/datasets/reports/${result.report_id}/print`" target="_blank" rel="noopener">打开可打印报告</a></div></div>
   <dl class="details-grid"><dt>固定数据集 / 版本</dt><dd>{{result.dataset.dataset_id}} / {{result.dataset.version}}</dd><dt>内容 SHA-256</dt><dd style="overflow-wrap:anywhere">{{result.dataset.content_hash}}</dd><dt>实测规模</dt><dd>{{result.dataset.row_count}} 行 × {{result.dataset.column_count}} 字段</dd><dt>综合结论</dt><dd><strong>{{fusion.status}}</strong> · {{fusion.point_index===null?'证据不足，拒绝确定性综合结论':fusion.point_index}}</dd><dt>识别区间</dt><dd>[{{pct(fusion.interval_lower)}}, {{pct(fusion.interval_upper)}}] · 不是事实真实概率或置信区间</dd><dt>证据覆盖</dt><dd>{{pct(fusion.evidence_coverage)}} · 已观测指标投影 {{pct(fusion.observed_projection)}}</dd></dl>
   <div class="notice warning"><strong>拒判或限制原因</strong><ul><li v-for="(reason,i) in fusion.refusal_reasons" :key="i">{{reason}}</li></ul></div>
   <p class="footnote">小屏可在表格内横向滚动查看全部列。</p><div class="table-wrap"><table style="min-width:700px"><thead><tr><th>维度</th><th>实测/证据得分</th><th>验证状态</th><th>证据类型 / 含义</th></tr></thead><tbody><tr v-for="[key,d] in dimensions" :key="key"><td><strong>{{names[key]??key}}</strong><JsonDetails :data="d" title="指标、来源、缺失证据与风险"/></td><td>{{pct(d.score)}}</td><td>{{d.status}}</td><td>{{d.evidence_class}}<p>{{d.meaning}}</p></td></tr></tbody></table></div>
   <JsonDetails :data="result.dataset.schema" title="实际 Schema"/><JsonDetails :data="result.assessment.baselines" title="静态质量与经典融合基线（不等于真实概率）"/><JsonDetails :data="result.assessment.risks" title="风险提示"/><JsonDetails :data="result.assessment.missing_evidence" title="全部缺失证据"/>
  </section>
  <section class="panel"><h2>任务价值：独立维度</h2><p class="notice">可信指数不乘以价值。内容风险若已体现于任务实验，不再次简单折扣。没有精确资产、版本、哈希、目标任务及经济条件匹配的测量，返回 NOT_EVALUATED。</p><ValueResult :report="result.value"/><JsonDetails :data="result.value" title="任务效用、履约可靠性与条件收益原始结果"/></section>
  <section class="panel"><h2>计算依据与证据追溯</h2><JsonDetails :data="result" title="完整方法版本、证据、指标和限制"/></section>
 </template><div v-else class="panel empty-intro"><h3>等待数据集输入</h3><p>即使没有标签、任务或来源证据，也能测量结构属性；未知属性保持未知。</p></div>
 </AsyncPanel>
</template>





