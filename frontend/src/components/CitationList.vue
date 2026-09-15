<script setup>
import { computed } from 'vue'

import {
  formatDistance,
  formatSourceLocation,
} from '../utils/ragPresentation.js'
import StatusBadge from './StatusBadge.vue'

const props = defineProps({
  citations: {
    type: Array,
    required: true,
  },
  citationStatus: {
    type: String,
    required: true,
  },
})

function documentType(citation) {
  const filename = citation.original_filename ?? ''
  const extension = filename.includes('.')
    ? filename.split('.').pop().toLowerCase()
    : ''
  const labels = {
    pdf: 'PDF',
    docx: 'DOCX',
    txt: 'TXT',
    md: 'Markdown',
  }
  return labels[extension] ?? 'Document'
}

const statusMessage = computed(() => {
  const messages = {
    valid: '回答中的引用都能对应到本次检索上下文。',
    invalid_reference: '回答包含无法匹配到上下文的引用编号，请谨慎核对。',
    missing: '模型回答没有提供引用，当前没有可展示的来源。',
  }
  return messages[props.citationStatus] ?? ''
})
</script>

<template>
  <section class="citation-section" :data-status="citationStatus">
    <div class="citation-heading">
      <div>
        <h3>引用来源</h3>
        <span>有效引用数量：{{ citations.length }}</span>
      </div>
      <StatusBadge :status="citationStatus" />
    </div>
    <p
      class="citation-state-note"
      :data-status="citationStatus"
    >
      {{ statusMessage }}
    </p>
    <ol v-if="citations.length" class="citation-list">
      <li
        v-for="citation in citations"
        :key="citation.citation_number"
        :value="citation.citation_number"
      >
        <span class="citation-number">[{{ citation.citation_number }}]</span>
        <div class="citation-copy">
          <strong>{{ citation.original_filename }}</strong>
          <span class="citation-source">
            <b>{{ documentType(citation) }}</b>
            <span>{{ formatSourceLocation(citation) }}</span>
          </span>
        </div>
        <div class="citation-metrics">
          <StatusBadge :status="citationStatus" />
          <small>distance {{ formatDistance(citation.distance) }}</small>
        </div>
      </li>
    </ol>
  </section>
</template>
