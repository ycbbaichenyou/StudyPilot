<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  initialQuery: {
    type: String,
    default: '',
  },
  canDebug: {
    type: Boolean,
    default: false,
  },
  disabledReason: {
    type: String,
    default: '',
  },
  retrievalResult: {
    type: Object,
    default: null,
  },
  contextResult: {
    type: Object,
    default: null,
  },
  loadingRetrieval: {
    type: Boolean,
    default: false,
  },
  loadingContext: {
    type: Boolean,
    default: false,
  },
  retrievalError: {
    type: String,
    default: '',
  },
  contextError: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['run-retrieval', 'run-context'])
const query = ref(props.initialQuery)
const topK = ref(5)
const maxContextCharacters = ref(6000)
const fieldError = ref('')

const isBusy = computed(
  () => props.loadingRetrieval || props.loadingContext,
)
const hasQuery = computed(() => Boolean(query.value.trim()))
const canRun = computed(
  () => props.canDebug && hasQuery.value && !isBusy.value,
)

function formatDistance(distance) {
  const numericDistance = Number(distance)
  return Number.isFinite(numericDistance) ? numericDistance.toFixed(4) : '—'
}

function sourceLabel(item) {
  const range =
    item.source_start === item.source_end
      ? `${item.source_start}`
      : `${item.source_start}–${item.source_end}`
  return `${item.source_type} ${range}`
}

function summarizeText(text, limit = 220) {
  const normalizedText = String(text ?? '').replace(/\s+/g, ' ').trim()
  return normalizedText.length > limit
    ? `${normalizedText.slice(0, limit)}…`
    : normalizedText
}

function requestPayload() {
  const cleanedQuery = query.value.trim()
  if (!cleanedQuery) {
    fieldError.value = '请输入用于调试的问题。'
    return null
  }
  if (!Number.isInteger(topK.value) || topK.value < 1 || topK.value > 20) {
    fieldError.value = 'top_k 必须是 1 到 20 之间的整数。'
    return null
  }
  if (
    !Number.isInteger(maxContextCharacters.value) ||
    maxContextCharacters.value < 1
  ) {
    fieldError.value = 'Context 字符预算必须是正整数。'
    return null
  }

  fieldError.value = ''
  return {
    query: cleanedQuery,
    topK: topK.value,
    maxContextCharacters: maxContextCharacters.value,
  }
}

function runRetrieval() {
  const payload = requestPayload()
  if (payload) {
    emit('run-retrieval', payload)
  }
}

function runContext() {
  const payload = requestPayload()
  if (payload) {
    emit('run-context', payload)
  }
}

watch(
  () => props.initialQuery,
  (nextQuery) => {
    if (nextQuery) {
      query.value = nextQuery
      fieldError.value = ''
    }
  },
)
</script>

<template>
  <section class="rag-debug-panel">
    <header class="debug-panel-heading">
      <div>
        <p class="section-kicker">Pipeline inspection</p>
        <h2>RAG Debug</h2>
      </div>
      <span class="debug-only-label">仅调试</span>
    </header>

    <div class="debug-controls">
      <label>
        调试问题
        <textarea
          v-model="query"
          rows="3"
          placeholder="输入问题，分别观察 Retrieval 与 Context 输出"
          :disabled="isBusy"
          @input="fieldError = ''"
        />
      </label>

      <div class="debug-parameter-grid">
        <label>
          top_k
          <input
            v-model.number="topK"
            type="number"
            min="1"
            max="20"
            step="1"
            :disabled="isBusy"
            @input="fieldError = ''"
          />
        </label>
        <label>
          Context 字符预算
          <input
            v-model.number="maxContextCharacters"
            type="number"
            min="1"
            step="1"
            :disabled="isBusy"
            @input="fieldError = ''"
          />
        </label>
      </div>

      <p v-if="!canDebug" class="debug-disabled-note">
        {{ disabledReason }}
      </p>
      <p v-if="fieldError" class="field-error" role="alert">
        {{ fieldError }}
      </p>

      <div class="debug-actions">
        <button
          class="button button-ghost"
          type="button"
          :disabled="!canRun"
          @click="runRetrieval"
        >
          {{ loadingRetrieval ? '正在检索…' : '运行 Retrieval' }}
        </button>
        <button
          class="button button-primary"
          type="button"
          :disabled="!canRun"
          @click="runContext"
        >
          {{ loadingContext ? '正在组装…' : '运行 Context' }}
        </button>
      </div>
    </div>

    <div class="debug-result-grid">
      <section class="debug-result-card">
        <header class="debug-result-heading">
          <div>
            <p class="section-kicker">Vector search</p>
            <h3>Retrieval Debug</h3>
          </div>
          <span v-if="retrievalResult" class="count-pill">
            {{ retrievalResult.results.length }}
          </span>
        </header>

        <p v-if="loadingRetrieval" class="debug-state-message">
          正在生成 Query Embedding 并检索 Chunk…
        </p>
        <p v-else-if="retrievalError" class="panel-error" role="alert">
          {{ retrievalError }}
        </p>
        <div
          v-else-if="retrievalResult && !retrievalResult.results.length"
          class="debug-empty-state"
        >
          本次检索没有返回 Chunk。
        </div>
        <ol
          v-else-if="retrievalResult"
          class="retrieval-debug-list"
        >
          <li
            v-for="(item, index) in retrievalResult.results"
            :key="item.chunk_id"
          >
            <div class="debug-item-heading">
              <span>#{{ index + 1 }}</span>
              <strong>{{ item.original_filename }}</strong>
              <small>distance {{ formatDistance(item.distance) }}</small>
            </div>
            <p>{{ summarizeText(item.text) }}</p>
            <footer>
              <span>{{ sourceLabel(item) }}</span>
              <span>chunk {{ item.chunk_id }}</span>
            </footer>
          </li>
        </ol>
        <p v-else class="debug-state-message">
          输入问题并运行 Retrieval，查看向量检索结果。
        </p>
      </section>

      <section class="debug-result-card">
        <header class="debug-result-heading">
          <div>
            <p class="section-kicker">Character budget</p>
            <h3>Context Debug</h3>
          </div>
          <span v-if="contextResult" class="count-pill">
            {{ contextResult.blocks.length }}
          </span>
        </header>

        <p v-if="loadingContext" class="debug-state-message">
          正在调用已有 Retrieval 并组装 Context…
        </p>
        <p v-else-if="contextError" class="panel-error" role="alert">
          {{ contextError }}
        </p>
        <template v-else-if="contextResult">
          <dl class="context-debug-metrics">
            <div>
              <dt>block 数量</dt>
              <dd>{{ contextResult.blocks.length }}</dd>
            </div>
            <div>
              <dt>used characters</dt>
              <dd>{{ contextResult.used_characters }}</dd>
            </div>
            <div>
              <dt>truncated</dt>
              <dd>{{ contextResult.truncated ? 'true' : 'false' }}</dd>
            </div>
          </dl>
          <div
            v-if="!contextResult.blocks.length"
            class="debug-empty-state"
          >
            本次检索没有可组装的 Context Block。
          </div>
          <ol v-else class="context-debug-list">
            <li
              v-for="block in contextResult.blocks"
              :key="block.citation_number"
            >
              <strong>{{ block.header }}</strong>
              <p>{{ block.text }}</p>
            </li>
          </ol>
        </template>
        <p v-else class="debug-state-message">
          运行 Context，查看字符预算和每个完整 Block。
        </p>
      </section>
    </div>
  </section>
</template>
