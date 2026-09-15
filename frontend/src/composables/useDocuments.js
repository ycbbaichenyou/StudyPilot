import { reactive, ref, unref, watch } from 'vue'

import { ApiError, getErrorMessage } from '../api/client.js'
import {
  buildDocumentChunks,
  buildDocumentEmbedding,
  deleteDocument as deleteDocumentRequest,
  listDocuments,
  parseDocument,
  uploadDocument,
} from '../api/documents.js'

function createProcessingState() {
  return {
    running: false,
    step: 'idle',
    chunkCount: null,
    error: '',
  }
}

export function useDocuments(knowledgeBaseId) {
  const documents = ref([])
  const isLoadingDocuments = ref(false)
  const isUploadingDocument = ref(false)
  const deletingDocumentIds = reactive(new Set())
  const documentError = ref('')
  const processingStates = reactive({})
  let loadSequence = 0

  function clearProcessingStates() {
    for (const documentId of Object.keys(processingStates)) {
      delete processingStates[documentId]
    }
  }

  function processingStateFor(documentId) {
    if (!processingStates[documentId]) {
      processingStates[documentId] = createProcessingState()
    }
    return processingStates[documentId]
  }

  function upsertDocument(document) {
    const existingIndex = documents.value.findIndex(
      (item) => item.id === document.id,
    )
    if (existingIndex === -1) {
      documents.value = [...documents.value, document].sort(
        (left, right) => left.id - right.id,
      )
      return
    }

    const nextDocuments = [...documents.value]
    nextDocuments[existingIndex] = {
      ...nextDocuments[existingIndex],
      ...document,
    }
    documents.value = nextDocuments
  }

  async function loadDocuments({ quiet = false } = {}) {
    const currentKnowledgeBaseId = unref(knowledgeBaseId)
    const currentLoadSequence = ++loadSequence
    if (!currentKnowledgeBaseId) {
      documents.value = []
      return
    }

    if (!quiet) {
      isLoadingDocuments.value = true
      documentError.value = ''
    }

    try {
      const items = await listDocuments(currentKnowledgeBaseId)
      if (
        currentLoadSequence === loadSequence &&
        currentKnowledgeBaseId === unref(knowledgeBaseId)
      ) {
        documents.value = items
      }
    } catch (error) {
      if (!quiet && currentLoadSequence === loadSequence) {
        documentError.value = getErrorMessage(
          error,
          '文档列表加载失败。',
        )
      }
    } finally {
      if (!quiet && currentLoadSequence === loadSequence) {
        isLoadingDocuments.value = false
      }
    }
  }

  async function refreshAfterFailure() {
    await loadDocuments({ quiet: true })
  }

  async function processDocument(document) {
    const state = processingStateFor(document.id)
    if (state.running || deletingDocumentIds.has(document.id)) {
      return null
    }

    state.running = true
    state.error = ''
    state.chunkCount = null
    documentError.value = ''

    try {
      let currentDocument = document
      if (currentDocument.status !== 'parsed') {
        state.step = 'parsing'
        currentDocument = await parseDocument(document.id)
        upsertDocument(currentDocument)
        if (currentDocument.status === 'parse_failed') {
          throw new ApiError(
            currentDocument.parse_error || '文档解析失败，请重试。',
            { data: currentDocument },
          )
        }
        if (currentDocument.status !== 'parsed') {
          throw new ApiError('文档尚未完成解析。', {
            data: currentDocument,
          })
        }
      }

      const retryEmbeddingOnly =
        currentDocument.embedding_status === 'embedding_failed'
      if (!retryEmbeddingOnly) {
        state.step = 'chunking'
        const chunkResult = await buildDocumentChunks(document.id)
        state.chunkCount = chunkResult.chunks.length
        if (!chunkResult.chunks.length) {
          throw new ApiError('文档没有可用于向量化的文本块。', {
            data: chunkResult,
          })
        }
      }

      state.step = 'embedding'
      const embeddingResult = await buildDocumentEmbedding(document.id)
      upsertDocument({
        id: document.id,
        embedding_status: embeddingResult.embedding_status,
        embedding_error: embeddingResult.embedding_error,
        embedded_at: embeddingResult.embedded_at,
      })
      if (embeddingResult.embedding_status === 'embedding_failed') {
        throw new ApiError(
          embeddingResult.embedding_error || '文档向量化失败，请重试。',
          { data: embeddingResult },
        )
      }
      if (embeddingResult.embedding_status !== 'embedded') {
        throw new ApiError('文档向量状态异常，请重试。', {
          data: embeddingResult,
        })
      }

      state.step = 'completed'
      return embeddingResult
    } catch (error) {
      state.step = 'failed'
      state.error = getErrorMessage(error, '文档处理失败。')
      documentError.value = state.error
      await refreshAfterFailure()
      return null
    } finally {
      state.running = false
    }
  }

  async function uploadAndProcessDocument(file) {
    const currentKnowledgeBaseId = unref(knowledgeBaseId)
    if (!currentKnowledgeBaseId) {
      documentError.value = '请先选择知识库。'
      return null
    }

    if (isUploadingDocument.value) {
      return null
    }

    isUploadingDocument.value = true
    documentError.value = ''
    try {
      const uploaded = await uploadDocument(currentKnowledgeBaseId, file)
      if (currentKnowledgeBaseId === unref(knowledgeBaseId)) {
        upsertDocument(uploaded)
      }
      await processDocument(uploaded)
      return uploaded
    } catch (error) {
      documentError.value = getErrorMessage(error, '文档上传失败。')
      return null
    } finally {
      isUploadingDocument.value = false
    }
  }

  async function deleteDocument(document) {
    const documentId = document.id
    const processingState = processingStateFor(documentId)
    if (
      deletingDocumentIds.has(documentId) ||
      processingState.running
    ) {
      return false
    }

    deletingDocumentIds.add(documentId)
    processingState.error = ''
    documentError.value = ''
    try {
      await deleteDocumentRequest(documentId)
      delete processingStates[documentId]
      await loadDocuments()
      return true
    } catch (error) {
      const message = getErrorMessage(error, '文档删除失败。')
      processingState.error = message
      documentError.value = message
      return false
    } finally {
      deletingDocumentIds.delete(documentId)
    }
  }

  watch(
    () => unref(knowledgeBaseId),
    () => {
      documents.value = []
      documentError.value = ''
      clearProcessingStates()
      loadDocuments()
    },
    { immediate: true },
  )

  return {
    documents,
    isLoadingDocuments,
    isUploadingDocument,
    deletingDocumentIds,
    documentError,
    processingStates,
    loadDocuments,
    uploadAndProcessDocument,
    processDocument,
    deleteDocument,
  }
}
