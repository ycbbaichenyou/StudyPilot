<script setup>
import { ref, watch } from 'vue'

import DocumentPipeline from './DocumentPipeline.vue'
import StatusBadge from './StatusBadge.vue'

const MAX_FILE_SIZE = 20 * 1024 * 1024
const SUPPORTED_EXTENSIONS = ['.pdf', '.docx', '.txt', '.md']

const props = defineProps({
  knowledgeBase: {
    type: Object,
    required: true,
  },
  documents: {
    type: Array,
    required: true,
  },
  processingStates: {
    type: Object,
    required: true,
  },
  deletingIds: {
    type: Object,
    required: true,
  },
  loading: {
    type: Boolean,
    default: false,
  },
  uploading: {
    type: Boolean,
    default: false,
  },
  error: {
    type: String,
    default: '',
  },
})

const emit = defineEmits(['upload', 'process', 'delete'])
const selectedFile = ref(null)
const fileInput = ref(null)
const fileError = ref('')

function fileExtension(filename) {
  const dotIndex = filename.lastIndexOf('.')
  return dotIndex === -1 ? '' : filename.slice(dotIndex).toLowerCase()
}

function selectFile(event) {
  selectedFile.value = event.target.files?.[0] ?? null
  fileError.value = ''
}

function submitFile() {
  if (!selectedFile.value) {
    fileError.value = '请选择需要上传的学习资料。'
    return
  }
  if (!SUPPORTED_EXTENSIONS.includes(fileExtension(selectedFile.value.name))) {
    fileError.value = '仅支持 PDF、DOCX、TXT 和 Markdown 文件。'
    return
  }
  if (selectedFile.value.size > MAX_FILE_SIZE) {
    fileError.value = '文件不能超过 20 MiB。'
    return
  }

  fileError.value = ''
  emit('upload', selectedFile.value)
}

function formatFileSize(bytes) {
  if (bytes < 1024) {
    return `${bytes} B`
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KiB`
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`
}

function confirmDocumentDeletion(document) {
  if (
    props.deletingIds.has(document.id) ||
    props.processingStates[document.id]?.running
  ) {
    return
  }

  const confirmed = window.confirm(
    `确定删除文档“${document.original_filename}”吗？\n\n对应的解析内容、文本块和向量索引也会一并删除，且无法撤销。`,
  )
  if (confirmed) {
    emit('delete', document)
  }
}

watch(
  () => props.uploading,
  (uploading, wasUploading) => {
    if (wasUploading && !uploading && !props.error) {
      selectedFile.value = null
      if (fileInput.value) {
        fileInput.value.value = ''
      }
    }
  },
)

watch(
  () => props.knowledgeBase.id,
  () => {
    selectedFile.value = null
    fileError.value = ''
    if (fileInput.value) {
      fileInput.value.value = ''
    }
  },
)
</script>

<template>
  <section class="workspace-panel document-panel">
    <div class="section-heading">
      <div>
        <p class="section-kicker">Source library</p>
        <h2>学习资料</h2>
        <p>上传后依次完成解析、分块和向量化。</p>
      </div>
      <span class="count-pill">{{ documents.length }}</span>
    </div>

    <form class="upload-box" @submit.prevent="submitFile">
      <div class="upload-copy">
        <span class="upload-icon" aria-hidden="true">↑</span>
        <div>
          <strong>上传并构建索引</strong>
          <p>PDF、DOCX、TXT 或 Markdown，最大 20 MiB</p>
        </div>
      </div>
      <input
        ref="fileInput"
        class="file-input"
        type="file"
        accept=".pdf,.docx,.txt,.md"
        :disabled="uploading"
        @change="selectFile"
      />
      <div v-if="selectedFile" class="selected-file">
        <span>{{ selectedFile.name }}</span>
        <small>{{ formatFileSize(selectedFile.size) }}</small>
      </div>
      <p v-if="fileError" class="field-error">{{ fileError }}</p>
      <button
        class="button button-primary"
        type="submit"
        :disabled="uploading || !selectedFile"
      >
        {{ uploading ? '正在上传和处理…' : '上传并开始处理' }}
      </button>
    </form>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="loading-state">正在加载文档…</p>
    <div v-else-if="!documents.length" class="empty-state compact-empty">
      <span>01</span>
      <h3>还没有学习资料</h3>
      <p>上传一份资料，StudyPilot 会明确展示索引的每个阶段。</p>
    </div>

    <div v-else class="document-list">
      <article
        v-for="document in documents"
        :key="document.id"
        class="document-card"
        :class="{ deleting: deletingIds.has(document.id) }"
      >
        <div class="document-summary">
          <div class="file-type">{{ document.file_type.toUpperCase() }}</div>
          <div class="document-name">
            <strong>{{ document.original_filename }}</strong>
            <span>{{ formatFileSize(document.file_size) }}</span>
          </div>
          <div class="document-card-actions">
            <StatusBadge :status="document.embedding_status" />
            <button
              class="document-delete"
              type="button"
              :disabled="
                deletingIds.has(document.id) ||
                processingStates[document.id]?.running
              "
              @click="confirmDocumentDeletion(document)"
            >
              {{ deletingIds.has(document.id) ? '删除中…' : '删除文档' }}
            </button>
          </div>
        </div>
        <DocumentPipeline
          :document="document"
          :process-state="processingStates[document.id]"
          :deleting="deletingIds.has(document.id)"
          @process="emit('process', $event)"
        />
      </article>
    </div>
  </section>
</template>
