import { computed, ref } from 'vue'

import {
  createKnowledgeBase as createKnowledgeBaseRequest,
  listKnowledgeBases,
} from '../api/knowledgeBases.js'
import { getErrorMessage } from '../api/client.js'

export function useKnowledgeBases() {
  const knowledgeBases = ref([])
  const selectedKnowledgeBaseId = ref(null)
  const isLoadingKnowledgeBases = ref(false)
  const isCreatingKnowledgeBase = ref(false)
  const knowledgeBaseError = ref('')

  const selectedKnowledgeBase = computed(() =>
    knowledgeBases.value.find(
      (knowledgeBase) => knowledgeBase.id === selectedKnowledgeBaseId.value,
    ),
  )

  async function loadKnowledgeBases() {
    isLoadingKnowledgeBases.value = true
    knowledgeBaseError.value = ''
    try {
      const items = await listKnowledgeBases()
      knowledgeBases.value = items
      const selectedStillExists = items.some(
        (item) => item.id === selectedKnowledgeBaseId.value,
      )
      if (!selectedStillExists) {
        selectedKnowledgeBaseId.value = items[0]?.id ?? null
      }
    } catch (error) {
      knowledgeBaseError.value = getErrorMessage(
        error,
        '知识库列表加载失败。',
      )
    } finally {
      isLoadingKnowledgeBases.value = false
    }
  }

  async function createKnowledgeBase(payload) {
    isCreatingKnowledgeBase.value = true
    knowledgeBaseError.value = ''
    try {
      const created = await createKnowledgeBaseRequest(payload)
      knowledgeBases.value = [...knowledgeBases.value, created]
      selectedKnowledgeBaseId.value = created.id
      return created
    } catch (error) {
      knowledgeBaseError.value = getErrorMessage(error, '知识库创建失败。')
      return null
    } finally {
      isCreatingKnowledgeBase.value = false
    }
  }

  function selectKnowledgeBase(knowledgeBaseId) {
    selectedKnowledgeBaseId.value = knowledgeBaseId
    knowledgeBaseError.value = ''
  }

  return {
    knowledgeBases,
    selectedKnowledgeBase,
    selectedKnowledgeBaseId,
    isLoadingKnowledgeBases,
    isCreatingKnowledgeBase,
    knowledgeBaseError,
    loadKnowledgeBases,
    createKnowledgeBase,
    selectKnowledgeBase,
  }
}
