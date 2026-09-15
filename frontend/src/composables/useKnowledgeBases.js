import { computed, reactive, ref } from 'vue'

import {
  createKnowledgeBase as createKnowledgeBaseRequest,
  deleteKnowledgeBase as deleteKnowledgeBaseRequest,
  listKnowledgeBases,
} from '../api/knowledgeBases.js'
import { getErrorMessage } from '../api/client.js'

export function useKnowledgeBases() {
  const knowledgeBases = ref([])
  const selectedKnowledgeBaseId = ref(null)
  const isLoadingKnowledgeBases = ref(false)
  const isCreatingKnowledgeBase = ref(false)
  const deletingKnowledgeBaseIds = reactive(new Set())
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
    if (isCreatingKnowledgeBase.value) {
      return null
    }

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

  async function deleteKnowledgeBase(knowledgeBaseId) {
    if (deletingKnowledgeBaseIds.has(knowledgeBaseId)) {
      return false
    }

    deletingKnowledgeBaseIds.add(knowledgeBaseId)
    knowledgeBaseError.value = ''
    try {
      await deleteKnowledgeBaseRequest(knowledgeBaseId)
      knowledgeBases.value = knowledgeBases.value.filter(
        (item) => item.id !== knowledgeBaseId,
      )
      if (selectedKnowledgeBaseId.value === knowledgeBaseId) {
        selectedKnowledgeBaseId.value = knowledgeBases.value[0]?.id ?? null
      }
      await loadKnowledgeBases()
      return true
    } catch (error) {
      knowledgeBaseError.value = getErrorMessage(
        error,
        '知识库删除失败。',
      )
      return false
    } finally {
      deletingKnowledgeBaseIds.delete(knowledgeBaseId)
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
    deletingKnowledgeBaseIds,
    knowledgeBaseError,
    loadKnowledgeBases,
    createKnowledgeBase,
    deleteKnowledgeBase,
    selectKnowledgeBase,
  }
}
