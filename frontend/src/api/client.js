const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim() ?? ''
const API_BASE_URL = configuredBaseUrl.replace(/\/+$/, '')

export class ApiError extends Error {
  constructor(message, { status = 0, data = null, cause = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.data = data
    this.cause = cause
  }
}

function errorMessageFromPayload(payload, status) {
  if (typeof payload?.detail === 'string') {
    return payload.detail
  }

  if (Array.isArray(payload?.detail)) {
    const messages = payload.detail
      .map((item) => item?.msg)
      .filter((message) => typeof message === 'string')
    if (messages.length) {
      return messages.join('；')
    }
  }

  if (typeof payload === 'string' && payload.trim()) {
    return payload.trim()
  }

  return `请求失败（HTTP ${status}）`
}

async function readResponseBody(response) {
  const text = await response.text()
  if (!text) {
    return null
  }

  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

export async function apiRequest(
  path,
  { method = 'GET', body = null, headers = {}, signal } = {},
) {
  const requestHeaders = new Headers(headers)
  const isFormData = body instanceof FormData
  let requestBody = body

  if (body !== null && !isFormData) {
    requestHeaders.set('Content-Type', 'application/json')
    requestBody = JSON.stringify(body)
  }

  let response
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      body: requestBody,
      headers: requestHeaders,
      signal,
    })
  } catch (cause) {
    throw new ApiError('无法连接到 StudyPilot 后端，请确认服务已经启动。', {
      cause,
    })
  }

  const payload = await readResponseBody(response)
  if (!response.ok) {
    throw new ApiError(errorMessageFromPayload(payload, response.status), {
      status: response.status,
      data: payload,
    })
  }

  return payload
}

export function getErrorMessage(error, fallback = '操作失败，请稍后重试。') {
  if (error instanceof Error && error.message) {
    return error.message
  }
  return fallback
}
