import { ref } from 'vue'
import { api, setCsrf, type Failure, failure, ApiError } from './api'
import { setCacheAccount } from './data-cache'

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

export function clearSession() {
  revision += 1
  identity.value = null
  setCsrf('')
  void setCacheAccount(null)
}

export function notifySessionChange() { channel?.postMessage('changed') }
channel?.addEventListener('message', () => {
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
