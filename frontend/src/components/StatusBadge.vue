<script setup>
import { computed } from 'vue'

const props = defineProps({
  status: {
    type: String,
    default: 'unknown',
  },
})

const statusLabels = {
  pending: '待处理',
  parsing: '解析中',
  parsed: '已解析',
  parse_failed: '解析失败',
  waiting: '等待中',
  processing: '处理中',
  chunks_ready: '已分块',
  embedding: '向量化中',
  embedded: '可检索',
  embedding_failed: '向量化失败',
  stale: '索引过期',
  answered: '已回答',
  insufficient_context: '资料不足',
  valid: '引用有效',
  missing: '缺少引用',
  invalid_reference: '引用异常',
  answer_error: '回答失败',
  unknown: '未知状态',
}

const statusTones = {
  parsed: 'info',
  chunks_ready: 'info',
  answered: 'success',
  embedded: 'success',
  valid: 'success',
  parsing: 'progress',
  processing: 'progress',
  embedding: 'progress',
  pending: 'neutral',
  waiting: 'neutral',
  insufficient_context: 'warning',
  missing: 'warning',
  stale: 'warning',
  parse_failed: 'danger',
  embedding_failed: 'danger',
  invalid_reference: 'danger',
  answer_error: 'danger',
}

const label = computed(() => statusLabels[props.status] ?? props.status)
const tone = computed(() => statusTones[props.status] ?? 'neutral')
</script>

<template>
  <span class="status-badge" :data-tone="tone">{{ label }}</span>
</template>
