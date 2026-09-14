<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  knowledgeBaseId: {
    type: Number,
    required: true,
  },
  canAsk: {
    type: Boolean,
    default: false,
  },
  disabledReason: {
    type: String,
    default: '',
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

const emit = defineEmits(['submit'])
const query = ref('')
const fieldError = ref('')

function submitQuestion() {
  const cleanedQuery = query.value.trim()
  if (!cleanedQuery) {
    fieldError.value = '请输入一个问题。'
    return
  }
  if (!props.canAsk) {
    fieldError.value = props.disabledReason || '知识库当前还不能回答问题。'
    return
  }

  fieldError.value = ''
  emit('submit', cleanedQuery)
}

watch(
  () => props.knowledgeBaseId,
  () => {
    query.value = ''
    fieldError.value = ''
  },
)
</script>

<template>
  <section class="question-card">
    <div class="question-heading">
      <div>
        <p class="section-kicker">Ask your sources</p>
        <h2>向知识库提问</h2>
      </div>
      <span class="single-turn-label">单轮问答</span>
    </div>

    <form @submit.prevent="submitQuestion">
      <label class="visually-hidden" for="knowledge-question">输入问题</label>
      <textarea
        id="knowledge-question"
        v-model="query"
        rows="5"
        placeholder="例如：资料中如何定义增长率？"
        :disabled="loading"
        @input="fieldError = ''"
      />
      <div class="question-actions">
        <p class="question-hint">
          {{ canAsk ? '回答将附带实际引用的资料来源。' : disabledReason }}
        </p>
        <button
          class="button button-primary ask-button"
          type="submit"
          :disabled="loading || !canAsk"
        >
          {{ loading ? '正在检索并生成…' : '生成回答' }}
        </button>
      </div>
    </form>

    <p v-if="fieldError" class="field-error">{{ fieldError }}</p>
    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
  </section>
</template>
