<script setup>
import { computed } from 'vue'

import StatusBadge from './StatusBadge.vue'

const props = defineProps({
  document: {
    type: Object,
    required: true,
  },
  processState: {
    type: Object,
    default: () => ({}),
  },
})

const emit = defineEmits(['process'])

const isReady = computed(
  () => props.document.embedding_status === 'embedded',
)
const isBusy = computed(
  () =>
    props.processState.running ||
    props.document.status === 'parsing' ||
    props.document.embedding_status === 'embedding',
)
const chunkStatus = computed(() => {
  if (props.processState.step === 'chunking') {
    return 'processing'
  }
  if (
    props.processState.chunkCount !== null &&
    props.processState.chunkCount !== undefined
  ) {
    return 'chunks_ready'
  }
  if (
    ['embedded', 'embedding_failed'].includes(
      props.document.embedding_status,
    )
  ) {
    return 'chunks_ready'
  }
  return 'waiting'
})
const actionLabel = computed(() => {
  if (isBusy.value) {
    const labels = {
      parsing: '正在解析…',
      chunking: '正在分块…',
      embedding: '正在向量化…',
    }
    return labels[props.processState.step] ?? '处理中…'
  }
  if (props.document.status === 'parse_failed') {
    return '重试解析与索引'
  }
  if (['embedding_failed', 'stale'].includes(props.document.embedding_status)) {
    return '重试构建索引'
  }
  return '继续处理'
})
const displayedError = computed(() => {
  if (props.processState.error) {
    return props.processState.error
  }
  if (props.document.status === 'parse_failed') {
    return props.document.parse_error || '文档解析失败。'
  }
  if (['embedding_failed', 'stale'].includes(props.document.embedding_status)) {
    return props.document.embedding_error || '文档索引当前不可用。'
  }
  return ''
})
</script>

<template>
  <div class="document-pipeline">
    <div class="pipeline-track" aria-label="文档处理进度">
      <div class="pipeline-step">
        <span class="step-number">1</span>
        <span class="step-copy">
          <small>Parse</small>
          <StatusBadge :status="document.status" />
        </span>
      </div>
      <span class="pipeline-line" aria-hidden="true"></span>
      <div class="pipeline-step">
        <span class="step-number">2</span>
        <span class="step-copy">
          <small>Chunk</small>
          <StatusBadge :status="chunkStatus" />
        </span>
      </div>
      <span class="pipeline-line" aria-hidden="true"></span>
      <div class="pipeline-step">
        <span class="step-number">3</span>
        <span class="step-copy">
          <small>Embedding</small>
          <StatusBadge :status="document.embedding_status" />
        </span>
      </div>
    </div>

    <p v-if="processState.chunkCount !== null" class="pipeline-detail">
      已生成 {{ processState.chunkCount }} 个文本块。
    </p>
    <p v-if="displayedError" class="inline-error" role="alert">
      {{ displayedError }}
    </p>

    <div class="pipeline-action">
      <p v-if="isReady" class="ready-message">
        文档已经可以参与知识库问答。
      </p>
      <button
        v-else
        class="button button-ghost"
        type="button"
        :disabled="isBusy"
        @click="emit('process', document)"
      >
        {{ actionLabel }}
      </button>
    </div>
  </div>
</template>
