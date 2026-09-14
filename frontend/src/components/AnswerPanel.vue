<script setup>
import { computed } from 'vue'

import CitationList from './CitationList.vue'
import StatusBadge from './StatusBadge.vue'

const props = defineProps({
  result: {
    type: Object,
    default: null,
  },
})

const citationMessage = computed(() => {
  const messages = {
    valid: '回答中的引用编号均能对应到本次上下文。',
    missing: '模型回答没有给出引用，请谨慎核对内容。',
    invalid_reference: '模型使用了不存在的引用编号，答案已保留供核查。',
  }
  return messages[props.result?.citation_status] ?? ''
})
</script>

<template>
  <section v-if="result" class="answer-card" aria-live="polite">
    <div class="answer-header">
      <div>
        <p class="section-kicker">Grounded answer</p>
        <h2>回答</h2>
      </div>
      <StatusBadge :status="result.status" />
    </div>

    <div v-if="result.status === 'insufficient_context'" class="empty-answer">
      <strong>现有资料不足以回答这个问题</strong>
      <p>可以补充并完成文档索引，或换一种更贴近资料内容的问法。</p>
    </div>

    <template v-else>
      <p class="answer-text">{{ result.answer }}</p>
      <div class="citation-integrity" :data-status="result.citation_status">
        <StatusBadge :status="result.citation_status" />
        <span>{{ citationMessage }}</span>
      </div>
      <CitationList :citations="result.citations" />
    </template>

    <footer class="answer-meta">
      <span>使用上下文 {{ result.used_context_characters }} 字符</span>
      <span v-if="result.context_truncated">上下文已按预算截断</span>
    </footer>
  </section>
</template>
