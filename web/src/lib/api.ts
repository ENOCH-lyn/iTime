import { getCachedResponse, putCachedResponse } from './data-cache'

export interface Failure {
  code: string
  message: string
  request_id: string | null
  saved: 'not_saved' | 'saved' | 'unknown'
  retryable: boolean
  details?: {
    fields?: { path: string; type: string }[]
    current_version?: number
    candidate_offsets_minutes?: number[]
    [key: string]: unknown
  }
}

export class ApiError extends Error {
  constructor(public info: Failure, public status = 0) {
    super(info.message)
  }
}

let csrf = ''
let adminCsrf = ''
const mutationObservers = new Set<(path: string, method: string) => void>()
export function setCsrf(value: string) { csrf = value }
export function setAdminCsrf(value: string) { adminCsrf = value }
export function observeMutations(observer: (path: string, method: string) => void) {
  mutationObservers.add(observer)
  return () => mutationObservers.delete(observer)
}

// Credentials stay in HttpOnly cookies; CSRF and account data stay in memory.
export async function api<T>(
  path: string,
  body?: unknown,
  method = body === undefined ? 'GET' : 'POST',
  timeoutMs = 15_000,
): Promise<T> {
  const requestId = crypto.randomUUID()
  const csrfToken = path.startsWith('/admin/') ? adminCsrf : csrf
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  let responseReceived = false
  try {
    const response = await fetch(`/api/v1${path}`, {
      method, credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
      headers: {
        'X-Request-ID': requestId,
        ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...(method === 'GET' || !csrfToken ? {} : { 'X-CSRF-Token': csrfToken }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    responseReceived = true
    const result = await response.json().catch(() => null)
    if (!response.ok) {
      if (result?.error?.message) throw new ApiError(result.error, response.status)
      throw new ApiError({
        code: 'service_unavailable', message: '服务暂时不可用，请稍后重试。',
        request_id: response.headers.get('X-Request-ID') ?? requestId,
        saved: method === 'GET' ? 'not_saved' : 'unknown', retryable: true,
      }, response.status)
    }
    if (result === null) throw new Error('invalid_response')
    if (method === 'GET') void putCachedResponse(path, result)
    else {
      for (const observer of mutationObservers) observer(path, method)
    }
    return result as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (method === 'GET' && !responseReceived && !navigator.onLine) {
      const cached = await getCachedResponse<T>(path)
      if (cached !== null) {
        window.dispatchEvent(new CustomEvent('itime:cache-fallback', {
          detail: { path, cached_at: cached.cached_at },
        }))
        return cached.value
      }
    }
    throw new ApiError({
      code: controller.signal.aborted ? 'request_timeout' : 'network_error',
      message: controller.signal.aborted
        ? '等待响应超时。请检查当前状态后再重试。'
        : '无法连接服务。请检查网络后重试，已填写的内容仍留在本页。',
      request_id: requestId,
      saved: method === 'GET' ? 'not_saved' : 'unknown', retryable: true,
    })
  } finally { window.clearTimeout(timer) }
}

export interface UploadApiOptions {
  timeoutMs?: number
  signal?: AbortSignal
  timeoutMessage?: string
  networkMessage?: string
}

export async function uploadApi<T>(
  path: string,
  file: File,
  operationId: string,
  options: UploadApiOptions = {},
): Promise<T> {
  const requestId = crypto.randomUUID()
  const controller = new AbortController()
  let timedOut = false
  const timer = window.setTimeout(() => {
    timedOut = true
    controller.abort()
  }, options.timeoutMs ?? 30_000)
  const externalAbort = () => controller.abort()
  if (options.signal?.aborted) controller.abort()
  else options.signal?.addEventListener('abort', externalAbort, { once: true })
  try {
    const response = await fetch(`/api/v1${path}`, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
      headers: {
        'Content-Type': file.type || 'application/octet-stream',
        'X-CSRF-Token': csrf,
        'X-Operation-ID': operationId,
        'X-Request-ID': requestId,
      },
      body: file,
    })
    const result = await response.json().catch(() => null)
    if (!response.ok) {
      if (result?.error?.message) throw new ApiError(result.error, response.status)
      throw new ApiError({
        code: 'service_unavailable',
        message: '服务暂时不可用，请稍后重试。',
        request_id: response.headers.get('X-Request-ID') ?? requestId,
        saved: 'unknown',
        retryable: true,
      }, response.status)
    }
    if (result === null) throw new Error('invalid_response')
    return result as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    const externallyAborted = options.signal?.aborted && !timedOut
    throw new ApiError({
      code: timedOut
        ? 'request_timeout'
        : externallyAborted
          ? 'request_cancelled'
          : 'network_error',
      message: timedOut
        ? (options.timeoutMessage ?? '文件处理超时。请检查导入历史后再重试。')
        : externallyAborted
          ? '上传已停止，请先检查服务端状态。'
          : (options.networkMessage ?? '无法连接服务。文件尚未确认导入，请检查网络后重试。'),
      request_id: requestId,
      saved: 'unknown',
      retryable: true,
    })
  } finally {
    window.clearTimeout(timer)
    options.signal?.removeEventListener('abort', externalAbort)
  }
}

export async function downloadApi(
  path: string,
  timeoutMs = 30_000,
): Promise<{ blob: Blob; filename: string }> {
  const requestId = crypto.randomUUID()
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`/api/v1${path}`, {
      method: 'GET',
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
      headers: {
        'X-CSRF-Token': csrf,
        'X-Request-ID': requestId,
      },
    })
    if (!response.ok) {
      const result = await response.json().catch(() => null)
      if (result?.error?.message) throw new ApiError(result.error, response.status)
      throw new ApiError({
        code: 'service_unavailable',
        message: '文件暂时无法下载，请稍后重试。',
        request_id: response.headers.get('X-Request-ID') ?? requestId,
        saved: 'not_saved',
        retryable: true,
      }, response.status)
    }
    const disposition = response.headers.get('Content-Disposition') ?? ''
    const matched = disposition.match(/filename="([^"]+)"/i)
    return {
      blob: await response.blob(),
      filename: matched?.[1] ?? 'itime-export.zip',
    }
  } catch (error) {
    if (error instanceof ApiError) throw error
    throw new ApiError({
      code: controller.signal.aborted ? 'request_timeout' : 'network_error',
      message: controller.signal.aborted
        ? '下载等待超时，请检查导出状态后重试。'
        : '无法下载文件，请检查网络后重试。',
      request_id: requestId,
      saved: 'not_saved',
      retryable: true,
    })
  } finally {
    window.clearTimeout(timer)
  }
}

export function failure(error: unknown): Failure {
  return error instanceof ApiError ? error.info : {
    code: 'unexpected_error', message: '操作未能完成，请稍后重试。',
    request_id: null, saved: 'unknown', retryable: true,
  }
}

export function localFailure(message: string): Failure {
  return { code: 'invalid_input', message, request_id: null, saved: 'not_saved', retryable: false }
}

export interface Policy {
  password_min_length: number
  password_max_length: number
  session_hours: number
  verification_minutes: number
  password_reset_minutes: number
  mail_cooldown_seconds: number
  registration_mode: 'open' | 'invite'
  require_email_verification: boolean
}

export function passwordError(value: string, policy: Policy) {
  // Python counts Unicode code points; use the same units as the server.
  const length = Array.from(value).length
  return length < policy.password_min_length || length > policy.password_max_length
    ? `请输入 ${policy.password_min_length}—${policy.password_max_length} 个字符。` : ''
}
