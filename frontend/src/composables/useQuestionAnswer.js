import { ref, unref, watch } from 'vue'

import { answerQuestion } from '../api/rag.js'
import { getErrorMessage } from '../api/client.js'

export function useQuestionAnswer(knowledgeBaseId) {
  const answerResult = ref(null)
  const isAnswering = ref(false)
  const answerError = ref('')

  async function askQuestion(query) {
    const currentKnowledgeBaseId = unref(knowledgeBaseId)
    if (!currentKnowledgeBaseId) {
      answerError.value = '请先选择知识库。'
      return null
    }

    isAnswering.value = true
    answerError.value = ''
    answerResult.value = null
    try {
      const result = await answerQuestion(currentKnowledgeBaseId, { query })
      if (currentKnowledgeBaseId === unref(knowledgeBaseId)) {
        answerResult.value = result
      }
      return result
    } catch (error) {
      answerError.value = getErrorMessage(error, '问题回答失败。')
      return null
    } finally {
      isAnswering.value = false
    }
  }

  watch(
    () => unref(knowledgeBaseId),
    () => {
      answerResult.value = null
      answerError.value = ''
    },
  )

  return {
    answerResult,
    isAnswering,
    answerError,
    askQuestion,
  }
}
