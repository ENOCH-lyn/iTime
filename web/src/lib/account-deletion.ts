import { api } from './api'
import type { Session } from './auth'

export interface AccountDeletion {
  id: string
  status: 'pending' | 'processing' | 'cancelled' | 'completed'
  requested_at: string
  purge_after: string
  cancelled_at: string | null
  completed_at: string | null
  can_cancel: boolean
}

export interface AccountDeletionStopped {
  ai_jobs: number
  exports: number
  subscription_refreshes: number
  focus_sessions: number
  emails: number
  temporary_media: number
}

export function requestAccountDeletion(input: {
  operation_id: string
  email_confirmation: string
  current_password: string
}) {
  return api<{
    operation_id: string
    deletion: AccountDeletion
    stopped: AccountDeletionStopped
    saved: 'saved'
  }>('/account-deletion', input)
}

export function cancelAccountDeletion(input: {
  email: string
  password: string
  device_name: string
}) {
  return api<{
    status: 'cancelled'
    deletion: AccountDeletion
    session: Session
  }>('/account-deletion/cancel', input)
}
