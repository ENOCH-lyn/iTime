export type RegistrationMode = 'open' | 'invite'
export type InvitationStatus = 'pending' | 'consumed' | 'expired' | 'revoked'
export type AccountStatus = 'active' | 'suspended'
export type AIAccess = 'allowed' | 'frozen'

export interface RegistrationPolicy {
  mode: RegistrationMode
  version: number
  updated_at: string
}

export interface Invitation {
  id: string
  email: string
  status: InvitationStatus
  created_at: string
  expires_at: string
  consumed_at: string | null
  revoked_at: string | null
  invite_url?: string
}

export interface ManagedUser {
  id: string
  email: string
  email_verified: boolean
  status: AccountStatus
  ai_access: AIAccess
  is_admin: boolean
  version: number
  created_at: string
}

export interface Cursor {
  before_time: string
  before_id: string
}

export interface Page<T> {
  items: T[]
  next_cursor: Cursor | null
}

export function localDate(value: string) {
  return new Date(value).toLocaleString('zh-CN', {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}
