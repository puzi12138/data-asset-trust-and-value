<script setup lang="ts">import { raw } from '../utils'; defineProps<{ data: unknown; title?: string }>()</script>
<template><details class="json-details"><summary>{{ title ?? '展开原始 API 证据' }}</summary><pre>{{ raw(data) }}</pre></details></template>
