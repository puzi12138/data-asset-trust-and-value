<script setup lang="ts">
import { computed } from 'vue'
import { number } from '../utils'
const props = defineProps<{ points: { x: string | number; y: number; lower?: number; upper?: number; reason?: string }[]; title: string; unit?: string; fixedRange?: [number, number] }>()
const bounds = computed(() => { if (props.fixedRange) return props.fixedRange; const vals = props.points.flatMap(p => [p.y, p.lower ?? p.y, p.upper ?? p.y]); const min = Math.min(0, ...vals), max = Math.max(0, ...vals); const pad = (max-min)*.1 || 1; return [min-pad, max+pad] })
const sx = (i: number) => 55 + i / Math.max(1, props.points.length-1) * 615
const sy = (v: number) => 205 - (v-bounds.value[0]) / (bounds.value[1]-bounds.value[0]) * 175
const line = computed(() => props.points.map((p,i) => `${sx(i)},${sy(p.y)}`).join(' '))
const band = computed(() => props.points.map((p,i) => `${sx(i)},${sy(p.upper ?? p.y)}`).concat([...props.points].reverse().map((p,i) => `${sx(props.points.length-1-i)},${sy(p.lower ?? p.y)}`)).join(' '))
</script>
<template><div class="chart"><h3>{{ title }}</h3><p class="muted small">{{ unit ?? '模型条件结果' }} · 悬停节点查看依据</p><div v-if="!points.length" class="state-box">暂无可绘制数据</div><svg v-else viewBox="0 0 700 250" role="img" :aria-label="title"><line x1="55" x2="670" y1="205" y2="205" stroke="#d8e0e6"/><template v-for="f in [0,.5,1]" :key="f"><line x1="55" x2="670" :y1="30+f*175" :y2="30+f*175" stroke="#e6ebef"/><text x="48" :y="35+f*175" text-anchor="end" class="chart-label">{{ number(bounds[1]-f*(bounds[1]-bounds[0]), 2) }}</text></template><polygon :points="band" fill="#d3eae5" opacity=".65"/><polyline :points="line" fill="none" stroke="#087c70" stroke-width="2.5"/><circle v-for="(p,i) in points" :key="i" :cx="sx(i)" :cy="sy(p.y)" r="4" fill="#087c70"><title>{{ p.x }}：{{ number(p.y,4) }}；{{ p.reason ?? '' }}</title></circle><text x="55" y="234" class="chart-label">{{ String(points[0].x).slice(0,22) }}</text><text x="670" y="234" text-anchor="end" class="chart-label">{{ String(points[points.length-1].x).slice(0,22) }}</text></svg></div></template>
