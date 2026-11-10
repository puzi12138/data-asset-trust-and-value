<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, type Asset, type Selection } from '../api'
const props = defineProps<{ modelValue: Selection; label?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: Selection]; loaded: [assets: Asset[]] }>()
const assets = ref<Asset[]>([]), error = ref('')
const ids = computed(() => [...new Set(assets.value.map(a => a.asset_id))])
const versions = computed(() => assets.value.filter(a => a.asset_id === props.modelValue.asset_id))
function changeAsset(event: Event) { const id = (event.target as HTMLSelectElement).value; emit('update:modelValue', { asset_id: id, version: assets.value.find(a => a.asset_id === id)?.version ?? '' }) }
onMounted(async () => { try { assets.value = await api.assets(); emit('loaded', assets.value); if (!props.modelValue.asset_id && assets.value.length) emit('update:modelValue', { asset_id: assets.value[0].asset_id, version: assets.value[0].version }) } catch(e) { error.value = (e as Error).message } })
</script>
<template><div class="asset-select"><label>{{ label ?? '数据资产' }}<select aria-label="数据资产" :value="modelValue.asset_id" @change="changeAsset"><option value="" disabled>请选择资产</option><option v-for="id in ids" :key="id" :value="id">{{ id }}</option></select></label><label>精确版本<select aria-label="精确版本" :value="modelValue.version" @change="emit('update:modelValue', { asset_id: modelValue.asset_id, version: ($event.target as HTMLSelectElement).value })"><option value="" disabled>请选择版本</option><option v-for="a in versions" :key="a.version" :value="a.version">{{ a.version }} · {{ a.state ?? '已注册' }}</option></select></label><p v-if="error" class="inline-error" role="alert">{{ error }}</p></div></template>
