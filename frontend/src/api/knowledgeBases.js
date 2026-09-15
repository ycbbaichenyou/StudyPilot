import { apiRequest } from './client.js'

export function listKnowledgeBases() {
  return apiRequest('/api/knowledge-bases')
}

export function createKnowledgeBase({ name, description }) {
  return apiRequest('/api/knowledge-bases', {
    method: 'POST',
    body: {
      name,
      description: description || null,
    },
  })
}

export function deleteKnowledgeBase(knowledgeBaseId) {
  return apiRequest(`/api/knowledge-bases/${knowledgeBaseId}`, {
    method: 'DELETE',
  })
}
