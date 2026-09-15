<script setup>
import { computed, onMounted } from 'vue'

import AnswerPanel from '../components/AnswerPanel.vue'
import DocumentPanel from '../components/DocumentPanel.vue'
import KnowledgeBasePanel from '../components/KnowledgeBasePanel.vue'
import QuestionPanel from '../components/QuestionPanel.vue'
import { useDocuments } from '../composables/useDocuments.js'
import { useKnowledgeBases } from '../composables/useKnowledgeBases.js'
import { useQuestionAnswer } from '../composables/useQuestionAnswer.js'

const {
  knowledgeBases,
  selectedKnowledgeBase,
  selectedKnowledgeBaseId,
  isLoadingKnowledgeBases,
  isCreatingKnowledgeBase,
  deletingKnowledgeBaseIds,
  knowledgeBaseError,
  loadKnowledgeBases,
  createKnowledgeBase,
  deleteKnowledgeBase,
  selectKnowledgeBase,
} = useKnowledgeBases()

const {
  documents,
  isLoadingDocuments,
  isUploadingDocument,
  deletingDocumentIds,
  documentError,
  processingStates,
  uploadAndProcessDocument,
  processDocument,
  deleteDocument,
} = useDocuments(selectedKnowledgeBaseId)

const { answerResult, isAnswering, answerError, askQuestion } =
  useQuestionAnswer(selectedKnowledgeBaseId)

const hasEmbeddedDocument = computed(() =>
  documents.value.some(
    (document) => document.embedding_status === 'embedded',
  ),
)
const questionDisabledReason = computed(() => {
  if (isLoadingDocuments.value) {
    return '正在检查知识库中的文档。'
  }
  if (!documents.value.length) {
    return '先上传并处理至少一份学习资料。'
  }
  return '至少需要一份状态为“可检索”的文档。'
})

onMounted(loadKnowledgeBases)
</script>

<template>
  <div class="app-shell">
    <KnowledgeBasePanel
      :knowledge-bases="knowledgeBases"
      :selected-id="selectedKnowledgeBaseId"
      :loading="isLoadingKnowledgeBases"
      :creating="isCreatingKnowledgeBase"
      :deleting-ids="deletingKnowledgeBaseIds"
      :error="knowledgeBaseError"
      @select="selectKnowledgeBase"
      @create="createKnowledgeBase"
      @delete="deleteKnowledgeBase"
    />

    <main class="workspace-main">
      <header class="workspace-header">
        <div>
          <p class="section-kicker">Study workspace</p>
          <h1>{{ selectedKnowledgeBase?.name || '准备你的学习空间' }}</h1>
          <p>
            {{
              selectedKnowledgeBase?.description ||
              '把课程资料变成可检索、可追踪来源的学习助手。'
            }}
          </p>
        </div>
        <div class="rag-flow" aria-label="RAG 工作流">
          <span>资料</span><b>→</b><span>检索</span><b>→</b><span>回答</span>
        </div>
      </header>

      <div v-if="!selectedKnowledgeBase" class="empty-workspace">
        <span class="empty-workspace-number">00</span>
        <h2>创建一个知识库开始学习</h2>
        <p>知识库会把相关资料、索引状态和单轮问答组织在一起。</p>
      </div>

      <div v-else class="workspace-grid">
        <DocumentPanel
          :knowledge-base="selectedKnowledgeBase"
          :documents="documents"
          :processing-states="processingStates"
          :deleting-ids="deletingDocumentIds"
          :loading="isLoadingDocuments"
          :uploading="isUploadingDocument"
          :error="documentError"
          @upload="uploadAndProcessDocument"
          @process="processDocument"
          @delete="deleteDocument"
        />

        <div class="question-column">
          <QuestionPanel
            :knowledge-base-id="selectedKnowledgeBase.id"
            :can-ask="hasEmbeddedDocument"
            :disabled-reason="questionDisabledReason"
            :loading="isAnswering"
            :error="answerError"
            @submit="askQuestion"
          />
          <AnswerPanel :result="answerResult" />
          <div v-if="!answerResult && !isAnswering" class="answer-placeholder">
            <span>[1]</span>
            <h3>答案会出现在这里</h3>
            <p>StudyPilot 会保留模型实际使用且有效的引用来源。</p>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>
