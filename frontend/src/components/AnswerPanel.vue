<script setup>
import { computed } from 'vue'

import { renderBasicMarkdown } from '../utils/markdown.js'
import CitationList from './CitationList.vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps({
  result: {
    type: Object,
    default: null,
  },
  loading: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: '',
  },
})

const renderedAnswer = computed(() =>
  renderBasicMarkdown(props.result?.answer ?? ''),
)
const displayedStatus = computed(() => {
  if (props.loading) {
    return 'processing'
  }
  if (props.error) {
    return 'answer_error'
  }
  return props.result?.status ?? 'unknown'
})
</script>

<template>
  <section
    v-if="loading || error || result"
    class="answer-card"
    aria-live="polite"
  >
    <div class="answer-header">
      <div>
        <p class="section-kicker">Grounded answer</p>
        <h2>回答</h2>
      </div>
      <StatusBadge :status="displayedStatus" />
    </div>

    <div v-if="loading" class="answer-loading">
      <strong>正在准备基于资料的回答</strong>
      <div class="answer-loading-steps">
        <span><i></i>正在检索资料...</span>
        <span><i></i>正在生成回答...</span>
      </div>
    </div>

    <div v-else-if="error" class="answer-error-state" role="alert">
      <strong>回答生成失败</strong>
      <p>{{ error }}</p>
      <small>请检查服务状态后重新提交，当前不会保存失败的问题。</small>
    </div>

    <div
      v-else-if="result.status === 'insufficient_context'"
      class="empty-answer"
    >
      <strong>当前资料不足，未找到可用于回答的内容</strong>
      <p>建议上传更多相关文档并完成索引，或换一种更贴近资料的问法。</p>
    </div>

    <template v-else>
      <div class="markdown-answer" v-html="renderedAnswer"></div>
      <CitationList
        :citations="result.citations"
        :citation-status="result.citation_status"
      />
    </template>

    <footer v-if="result" class="answer-meta">
      <span>使用上下文 {{ result.used_context_characters }} 字符</span>
      <span v-if="result.context_truncated" class="context-truncated">
        上下文已按预算截断
      </span>
    </footer>

    <details v-if="result" class="answer-debug-details">
      <summary>
        <span>Answer Debug</span>
        <small>查看原始响应指标</small>
      </summary>
      <dl class="answer-debug-metrics">
        <div>
          <dt>status</dt>
          <dd>{{ result.status }}</dd>
        </div>
        <div :data-status="result.citation_status">
          <dt>citation_status</dt>
          <dd>{{ result.citation_status }}</dd>
        </div>
        <div>
          <dt>context_truncated</dt>
          <dd>{{ result.context_truncated ? 'true' : 'false' }}</dd>
        </div>
        <div>
          <dt>used_context_characters</dt>
          <dd>{{ result.used_context_characters }}</dd>
        </div>
        <div>
          <dt>citation_count</dt>
          <dd>{{ result.citations.length }}</dd>
        </div>
      </dl>
    </details>
  </section>
</template>
