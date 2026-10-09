import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { api, failure, type Failure } from '../lib/api'
import {
  clearSession,
  confirmDiscardPendingOperations,
  notifySessionChange,
} from '../lib/auth'

export function useAppShell() {
  const router = useRouter()
  const pendingLogout = ref(false)
  const logoutError = ref<Failure | null>(null)

  async function logout() {
    if (pendingLogout.value || !confirmDiscardPendingOperations()) return
    pendingLogout.value = true
    logoutError.value = null
    try {
      await api('/auth/logout', {})
      clearSession({ discardLocal: true })
      notifySessionChange()
      await router.replace('/login')
    } catch (reason) {
      logoutError.value = failure(reason)
    } finally {
      pendingLogout.value = false
    }
  }

  return { pendingLogout, logoutError, logout }
}
