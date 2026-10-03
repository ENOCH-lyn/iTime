import { ref } from 'vue'
import { api, setCsrf, type Failure, failure, ApiError } from './api'
import { clearAccountCache, setCacheAccount } from './data-cache'
import { clearFocusQueues, focusQueueCount } from './focus-queue'
import { clearTaskQueue, loadTaskQueue } from './sync-queue'

export interface Session {
  user: {
    id: string
    email: string
    email_verified: boolean
    ai_access: 'allowed' | 'frozen'
    is_admin: boolean
    created_at: string
  }
  session_id: string
  expires_at: string
  csrf_token: string
}

export const identity = ref<Session | null>(null)
export const authError = ref<Failure | null>(null)
export const authLoading = ref(false)
let inFlight: Promise<void> | null = null
let revision = 0
const channel = typeof BroadcastChannel === 'undefined' ? null : new BroadcastChannel('itime-auth')

export function acceptSession(session: Session) {
  revision += 1
  identity.value = session
  setCsrf(session.csrf_token)
  void setCacheAccount(session.user.id)
  authError.value = null
}

export function pendingLocalOperationCount() {
  const userId = identity.value?.user.id
  return userId
    ? loadTaskQueue(userId).length + focusQueueCount(userId)
    : 0
}

export function confirmDiscardPendingOperations() {
  const count = pendingLocalOperationCount()
  return count === 0 || window.confirm(
    `本设备还有 ${count} 项操作未同步。退出后这些操作将被清除，确定继续吗？`,
  )
}

export function discardAccountLocalData(userId: string) {
  clearTaskQueue(userId)
  clearFocusQueues(userId)
  sessionStorage.removeItem(`itime-sync-cursor:${userId}`)
  void clearAccountCache(userId)
}

export function clearSession(options: { discardLocal?: boolean } = {}) {
  revision += 1
  const userId = identity.value?.user.id ?? null
  identity.value = null
  setCsrf('')
  if (options.discardLocal && userId) {
    discardAccountLocalData(userId)
  }
  void setCacheAccount(null)
}

export function notifySessionChange() { channel?.postMessage('changed') }
export function notifyAccountDiscard(userId: string) {
  channel?.postMessage({ type: 'discard', user_id: userId })
}
channel?.addEventListener('message', event => {
  if (
    typeof event.data === 'object'
    && event.data !== null
    && event.data.type === 'discard'
    && typeof event.data.user_id === 'string'
  ) {
    const userId = event.data.user_id
    discardAccountLocalData(userId)
    if (identity.value?.user.id === userId) clearSession()
    return
  }
  // A cookie rotation in another tab invalidates any in-flight account snapshot.
  revision += 1
  if (identity.value) {
    if (inFlight) void inFlight.then(() => refreshSession())
    else void refreshSession()
  }
})

export function refreshSession(): Promise<void> {
  if (inFlight) return inFlight
  authLoading.value = true
  authError.value = null
  const startedAt = revision
  inFlight = (async () => {
    try {
      const session = await api<Session>('/auth/me')
      if (revision === startedAt) acceptSession(session)
    }
    catch (error) {
      if (revision !== startedAt) return
      if (error instanceof ApiError && (error.status === 401 || error.info.code === 'account_suspended')) {
        clearSession()
      }
      authError.value = failure(error)
    } finally {
      authLoading.value = false
      inFlight = null
    }
  })()
  return inFlight
}
