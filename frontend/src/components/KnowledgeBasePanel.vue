<script setup>
import { ref, watch } from 'vue'

const props = defineProps({
  knowledgeBases: {
    type: Array,
    required: true,
  },
  selectedId: {
    type: Number,
    default: null,
  },
  loading: {
    type: Boolean,
    default: false,
  },
  creating: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['select', 'create'])
const name = ref('')
const description = ref('')
const formError = ref('')

function submitKnowledgeBase() {
  const cleanedName = name.value.trim()
  if (!cleanedName) {
    formError.value = '请输入知识库名称。'
    return
  }

  formError.value = ''
  emit('create', {
    name: cleanedName,
    description: description.value.trim(),
  })
}

watch(
  () => props.creating,
  (creating, wasCreating) => {
    if (wasCreating && !creating && !props.error) {
      name.value = ''
      description.value = ''
    }
  },
)
</script>

<template>
  <aside class="knowledge-sidebar" aria-label="知识库导航">
    <div class="brand-lockup">
      <span class="brand-mark">SP</span>
      <div>
        <strong>StudyPilot</strong>
        <span>RAG Learning Workspace</span>
      </div>
    </div>

    <section class="sidebar-section">
      <div class="section-heading compact-heading">
        <div>
          <p class="section-kicker">Knowledge bases</p>
          <h2>知识库</h2>
        </div>
        <span class="count-pill">{{ knowledgeBases.length }}</span>
      </div>

      <p v-if="loading" class="muted-message">正在加载知识库…</p>
      <p v-else-if="!knowledgeBases.length" class="empty-sidebar">
        创建第一个知识库，开始整理学习资料。
      </p>

      <div v-else class="knowledge-list">
        <button
          v-for="knowledgeBase in knowledgeBases"
          :key="knowledgeBase.id"
          class="knowledge-item"
          :class="{ active: knowledgeBase.id === selectedId }"
          type="button"
          :aria-pressed="knowledgeBase.id === selectedId"
          @click="emit('select', knowledgeBase.id)"
        >
          <span class="knowledge-icon">KB</span>
          <span class="knowledge-copy">
            <strong>{{ knowledgeBase.name }}</strong>
            <small>{{ knowledgeBase.description || '暂无描述' }}</small>
          </span>
        </button>
      </div>
    </section>

    <form class="create-knowledge-form" @submit.prevent="submitKnowledgeBase">
      <p class="section-kicker">New workspace</p>
      <label for="knowledge-base-name">新建知识库</label>
      <input
        id="knowledge-base-name"
        v-model="name"
        name="name"
        maxlength="255"
        placeholder="例如：宏观经济学"
        :disabled="creating"
      />
      <textarea
        v-model="description"
        name="description"
        rows="2"
        placeholder="简短描述（可选）"
        :disabled="creating"
      />
      <p v-if="formError" class="field-error">{{ formError }}</p>
      <button class="button button-secondary full-width" :disabled="creating">
        {{ creating ? '创建中…' : '创建知识库' }}
      </button>
    </form>

    <p v-if="error" class="panel-error sidebar-error" role="alert">
      {{ error }}
    </p>
  </aside>
</template>
