import { apiRequest } from './client.js'

export function listDocuments(knowledgeBaseId) {
  return apiRequest(`/api/knowledge-bases/${knowledgeBaseId}/documents`)
}

export function uploadDocument(knowledgeBaseId, file) {
  const formData = new FormData()
  formData.append('file', file)
  return apiRequest(`/api/knowledge-bases/${knowledgeBaseId}/documents`, {
    method: 'POST',
    body: formData,
  })
}

export function parseDocument(documentId) {
  return apiRequest(`/api/documents/${documentId}/parse`, {
    method: 'POST',
  })
}

export function buildDocumentChunks(documentId) {
  return apiRequest(`/api/documents/${documentId}/chunks`, {
    method: 'POST',
  })
}

export function buildDocumentEmbedding(documentId) {
  return apiRequest(`/api/documents/${documentId}/embedding`, {
    method: 'POST',
  })
}

export function deleteDocument(documentId) {
  return apiRequest(`/api/documents/${documentId}`, {
    method: 'DELETE',
  })
}
