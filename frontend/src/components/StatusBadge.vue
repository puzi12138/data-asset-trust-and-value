<script setup lang="ts">
import { computed } from 'vue'
const props = defineProps<{ value?: unknown }>()
const label = computed(() => String(props.value ?? 'UNKNOWN'))
const tone = computed(() => /FAIL|REJECT|REVOK|EXPIRE|INVALID|DENIED|NEGATIVE/.test(label.value) ? 'danger' : /SUCCESS|ACTIVE|EVALUATED|VERIFIED|PASS|HEALTHY|^OK$/.test(label.value) && !/NOT_|UN/.test(label.value) ? 'success' : /PENDING|UNKNOWN|UNCERTAIN|INSUFFICIENT|DISPUT/.test(label.value) ? 'warning' : 'neutral')
</script>
<template><span class="badge" :class="tone">{{ label }}</span></template>
