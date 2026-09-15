<script setup>
import { computed, onMounted, ref } from 'vue'

import AnswerPanel from '../components/AnswerPanel.vue'
import DocumentPanel from '../components/DocumentPanel.vue'
import KnowledgeBasePanel from '../components/KnowledgeBasePanel.vue'
import QuestionPanel from '../components/QuestionPanel.vue'
import RagDebugPanel from '../components/RagDebugPanel.vue'
import { useDocuments } from '../composables/useDocuments.js'
import { useKnowledgeBases } from '../composables/useKnowledgeBases.js'
import { useQuestionAnswer } from '../composables/useQuestionAnswer.js'
import { useRagDebug } from '../composables/useRagDebug.js'

const workspaceMode = ref('normal')

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
const {
  retrievalResult,
  contextResult,
  isLoadingRetrieval,
  isLoadingContext,
  retrievalError,
  contextError,
  runRetrieval,
  runContext,
} = useRagDebug(selectedKnowledgeBaseId)

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
        <div class="workspace-header-actions">
          <div class="rag-flow" aria-label="RAG 工作流">
            <span>资料</span><b>→</b><span>检索</span><b>→</b><span>回答</span>
          </div>
          <div class="workspace-mode-toggle" role="group" aria-label="工作台模式">
            <button
              type="button"
              :class="{ active: workspaceMode === 'normal' }"
              :aria-pressed="workspaceMode === 'normal'"
              @click="workspaceMode = 'normal'"
            >
              普通模式
            </button>
            <button
              type="button"
              :class="{ active: workspaceMode === 'debug' }"
              :aria-pressed="workspaceMode === 'debug'"
              @click="workspaceMode = 'debug'"
            >
              Debug 模式
            </button>
          </div>
        </div>
      </header>

      <div v-if="!selectedKnowledgeBase" class="empty-workspace">
        <span class="empty-workspace-number">00</span>
        <h2>创建一个知识库开始学习</h2>
        <p>知识库会把相关资料、索引状态和单轮问答组织在一起。</p>
      </div>

      <div
        v-else
        :class="
          workspaceMode === 'normal' ? 'workspace-grid' : 'debug-workspace-grid'
        "
      >
        <DocumentPanel
          v-if="workspaceMode === 'normal'"
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

        <RagDebugPanel
          v-else
          :initial-query="answerResult?.query || ''"
          :can-debug="hasEmbeddedDocument"
          :disabled-reason="questionDisabledReason"
          :retrieval-result="retrievalResult"
          :context-result="contextResult"
          :loading-retrieval="isLoadingRetrieval"
          :loading-context="isLoadingContext"
          :retrieval-error="retrievalError"
          :context-error="contextError"
          @run-retrieval="runRetrieval"
          @run-context="runContext"
        />

        <div
          class="question-column"
          :class="{ 'debug-answer-column': workspaceMode === 'debug' }"
        >
          <header v-if="workspaceMode === 'debug'" class="debug-answer-heading">
            <div>
              <p class="section-kicker">Generation response</p>
              <h2>Answer Debug</h2>
            </div>
            <span>使用正常 Answer API</span>
          </header>
          <QuestionPanel
            :knowledge-base-id="selectedKnowledgeBase.id"
            :can-ask="hasEmbeddedDocument"
            :disabled-reason="questionDisabledReason"
            :loading="isAnswering"
            @submit="askQuestion"
          />
          <AnswerPanel
            :result="answerResult"
            :loading="isAnswering"
            :error="answerError"
          />
          <div
            v-if="!answerResult && !isAnswering && !answerError"
            class="answer-placeholder"
          >
            <span>[1]</span>
            <h3>答案会出现在这里</h3>
            <p>StudyPilot 会保留模型实际使用且有效的引用来源。</p>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>
