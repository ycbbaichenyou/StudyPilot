import { apiRequest } from './client.js'

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
