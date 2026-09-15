const configuredBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim() ?? ''
const API_BASE_URL = configuredBaseUrl.replace(/\/+$/, '')

const STATUS_MESSAGES = {
  400: '请求内容有误',
  404: '请求的资源不存在',
  409: '资源当前状态不允许此操作',
  413: '上传文件超过大小限制',
  422: '请求参数校验失败',
  502: '模型服务暂时不可用',
  503: '检索或存储服务暂时不可用',
}

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
  const statusMessage = STATUS_MESSAGES[status] ?? `请求失败（HTTP ${status}）`
  let detail = ''

  if (typeof payload?.detail === 'string') {
    detail = payload.detail
  }

  if (!detail && Array.isArray(payload?.detail)) {
    const messages = payload.detail
      .map((item) => item?.msg)
      .filter((message) => typeof message === 'string')
    if (messages.length) {
      detail = messages.join('；')
    }
  }

  if (!detail && typeof payload === 'string' && payload.trim()) {
    detail = payload.trim()
  }

  return detail ? `${statusMessage}：${detail}` : statusMessage
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

  let payload
  try {
    payload = await readResponseBody(response)
  } catch (cause) {
    throw new ApiError('无法读取服务器响应，请稍后重试。', {
      status: response.status,
      cause,
    })
  }
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
