import { ref } from 'vue'
import { ApiError, api, failure, setAdminCsrf, type Failure } from './api'

export interface AdminSession {
  admin: { id: string; email: string; is_admin: boolean }
  session_id: string
  auth_method: 'password' | 'totp' | 'recovery'
  expires_at: string
  csrf_token: string
}

export const adminIdentity = ref<AdminSession | null>(null)
export const adminAuthError = ref<Failure | null>(null)
export const adminAuthLoading = ref(false)
let inFlight: Promise<void> | null = null
let revision = 0
const channel = typeof BroadcastChannel === 'undefined'
  ? null : new BroadcastChannel('itime-admin-auth')

export function acceptAdminSession(session: AdminSession) {
  revision += 1
  adminIdentity.value = session
  setAdminCsrf(session.csrf_token)
  adminAuthError.value = null
}

export function clearAdminSession() {
  revision += 1
  adminIdentity.value = null
  setAdminCsrf('')
}

export function notifyAdminSessionChange() { channel?.postMessage('changed') }
channel?.addEventListener('message', () => {
  revision += 1
  if (adminIdentity.value) {
    if (inFlight) void inFlight.then(() => refreshAdminSession())
    else void refreshAdminSession()
  }
})

export function refreshAdminSession(): Promise<void> {
  if (inFlight) return inFlight
  adminAuthLoading.value = true
  adminAuthError.value = null
  const startedAt = revision
  inFlight = (async () => {
    try {
      const session = await api<AdminSession>('/admin/auth/me')
      if (revision === startedAt) acceptAdminSession(session)
    } catch (error) {
      if (revision !== startedAt) return
      if (error instanceof ApiError && (
        error.status === 401 || error.info.code === 'account_suspended'
      )) clearAdminSession()
      adminAuthError.value = failure(error)
    } finally {
      adminAuthLoading.value = false
      inFlight = null
    }
  })()
  return inFlight
}
