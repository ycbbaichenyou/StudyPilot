import { apiRequest } from './client.js'

export function searchKnowledgeBase(
  knowledgeBaseId,
  { query, topK = 5 },
) {
  return apiRequest(`/api/knowledge-bases/${knowledgeBaseId}/search`, {
    method: 'POST',
    body: {
      query,
      top_k: topK,
    },
  })
}

export function assembleKnowledgeBaseContext(
  knowledgeBaseId,
  { query, topK = 5, maxContextCharacters = 6000 },
) {
  return apiRequest(`/api/knowledge-bases/${knowledgeBaseId}/context`, {
    method: 'POST',
    body: {
      query,
      top_k: topK,
      max_context_characters: maxContextCharacters,
    },
  })
}

export function answerQuestion(
  knowledgeBaseId,
  { query, topK = 5, maxContextCharacters = 6000 },
) {
  return apiRequest(`/api/knowledge-bases/${knowledgeBaseId}/answer`, {
    method: 'POST',
    body: {
      query,
      top_k: topK,
      max_context_characters: maxContextCharacters,
    },
  })
}
