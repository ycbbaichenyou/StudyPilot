import { ref, unref, watch } from 'vue'

import { getErrorMessage } from '../api/client.js'
import {
  assembleKnowledgeBaseContext,
  searchKnowledgeBase,
} from '../api/rag.js'

export function useRagDebug(knowledgeBaseId) {
  const retrievalResult = ref(null)
  const contextResult = ref(null)
  const isLoadingRetrieval = ref(false)
  const isLoadingContext = ref(false)
  const retrievalError = ref('')
  const contextError = ref('')

  async function runRetrieval({ query, topK }) {
    const currentKnowledgeBaseId = unref(knowledgeBaseId)
    if (!currentKnowledgeBaseId || isLoadingRetrieval.value) {
      return null
    }

    isLoadingRetrieval.value = true
    retrievalError.value = ''
    retrievalResult.value = null
    try {
      const result = await searchKnowledgeBase(currentKnowledgeBaseId, {
        query,
        topK,
      })
      if (currentKnowledgeBaseId === unref(knowledgeBaseId)) {
        retrievalResult.value = result
      }
      return result
    } catch (error) {
      if (currentKnowledgeBaseId === unref(knowledgeBaseId)) {
        retrievalError.value = getErrorMessage(
          error,
          'Retrieval 调试请求失败，请稍后重试。',
        )
      }
      return null
    } finally {
      isLoadingRetrieval.value = false
    }
  }

  async function runContext({ query, topK, maxContextCharacters }) {
    const currentKnowledgeBaseId = unref(knowledgeBaseId)
    if (!currentKnowledgeBaseId || isLoadingContext.value) {
      return null
    }

    isLoadingContext.value = true
    contextError.value = ''
    contextResult.value = null
    try {
      const result = await assembleKnowledgeBaseContext(
        currentKnowledgeBaseId,
        {
          query,
          topK,
          maxContextCharacters,
        },
      )
      if (currentKnowledgeBaseId === unref(knowledgeBaseId)) {
        contextResult.value = result
      }
      return result
    } catch (error) {
      if (currentKnowledgeBaseId === unref(knowledgeBaseId)) {
        contextError.value = getErrorMessage(
          error,
          'Context 调试请求失败，请稍后重试。',
        )
      }
      return null
    } finally {
      isLoadingContext.value = false
    }
  }

  watch(
    () => unref(knowledgeBaseId),
    () => {
      retrievalResult.value = null
      contextResult.value = null
      retrievalError.value = ''
      contextError.value = ''
    },
  )

  return {
    retrievalResult,
    contextResult,
    isLoadingRetrieval,
    isLoadingContext,
    retrievalError,
    contextError,
    runRetrieval,
    runContext,
  }
}
