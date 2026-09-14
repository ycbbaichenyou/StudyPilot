<script setup>
defineProps({
  citations: {
    type: Array,
    required: true,
  },
})

function sourceLabel(citation) {
  const labels = {
    page: '页',
    paragraph: '段落',
    line: '行',
    line_range: '行',
  }
  const label = labels[citation.source_type] ?? citation.source_type
  const range =
    citation.source_start === citation.source_end
      ? `${citation.source_start}`
      : `${citation.source_start}–${citation.source_end}`
  return `${label} ${range}`
}
</script>

<template>
  <section v-if="citations.length" class="citation-section">
    <div class="citation-heading">
      <h3>引用来源</h3>
      <span>{{ citations.length }} 条</span>
    </div>
    <ol class="citation-list">
      <li
        v-for="citation in citations"
        :key="citation.citation_number"
        :value="citation.citation_number"
      >
        <span class="citation-number">[{{ citation.citation_number }}]</span>
        <div class="citation-copy">
          <strong>{{ citation.original_filename }}</strong>
          <span>{{ sourceLabel(citation) }}</span>
        </div>
        <small>distance {{ citation.distance.toFixed(4) }}</small>
      </li>
    </ol>
  </section>
</template>
