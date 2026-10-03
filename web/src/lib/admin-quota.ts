import { api } from './api'
import type { ManagedUser, Page } from './admin-management'
import type {
  QuotaDaily,
  QuotaLedgerKind,
  QuotaReservation,
  QuotaUsageSource,
} from './quota'

export type QuotaPolicyStatus = 'draft' | 'published' | 'retired'

export interface QuotaPolicy {
  id: string
  version: number
  currency: string
  daily_allowance: string
  reservation_estimator_version: string
  status: QuotaPolicyStatus
  current: boolean
  created_by_user_id: string | null
  published_by_user_id: string | null
  created_at: string
  updated_at: string
  published_at: string | null
  retired_at: string | null
}

export interface AdminQuotaUserSummary {
  user: {
    id: string
    email: string
    status: 'active' | 'suspended' | 'pending_deletion'
    ai_access: 'allowed' | 'frozen'
  }
  configured: boolean
  current: QuotaDaily | null
  reservations: QuotaReservation[]
}

export interface QuotaAdjustment {
  id: string
  operation_id: string
  target_user_id: string
  daily_allowance_id: string
  activity_date: string
  mode: 'credit' | 'debit'
  amount: string
  delta: string
  currency: string
  reason: string
  actor_admin_id: string | null
  request_id: string
  created_at: string
}

export interface AdminQuotaLedgerEntry {
  id: string
  user: {
    id: string
    email: string
  }
  activity_date: string
  kind: QuotaLedgerKind
  delta: string
  currency: string
  usage_source: QuotaUsageSource | null
  job: {
    id: string
    capability: 'calendar_screenshot' | 'conversation'
  } | null
  adjustment: {
    id: string
    operation_id: string
    mode: 'credit' | 'debit'
    amount: string
    reason: string
    actor: {
      id: string
      email: string
    } | null
    request_id: string
  } | null
  created_at: string
}

export function listQuotaPolicies(cursor: Page<QuotaPolicy>['next_cursor'] = null) {
  const query = new URLSearchParams({ limit: '30' })
  if (cursor) {
    query.set('before_time', cursor.before_time)
    query.set('before_id', cursor.before_id)
  }
  return api<Page<QuotaPolicy>>(`/admin/quota/policies?${query}`)
}

export function createQuotaPolicy(input: {
  operation_id: string
  currency: string
  daily_allowance: string
}) {
  return api<QuotaPolicy>('/admin/quota/policies', input)
}

export function publishQuotaPolicy(policyId: string, operationId: string) {
  return api<QuotaPolicy>(
    `/admin/quota/policies/${policyId}/publish`,
    { operation_id: operationId },
  )
}

export function searchQuotaUsers(
  search: string,
  cursor: Page<ManagedUser>['next_cursor'] = null,
) {
  const query = new URLSearchParams({
    search,
    role: 'user',
    limit: '20',
  })
  if (cursor) {
    query.set('before_time', cursor.before_time)
    query.set('before_id', cursor.before_id)
  }
  return api<Page<ManagedUser>>(`/admin/users?${query}`)
}

export function getAdminUserQuota(userId: string, activityDate?: string) {
  const query = new URLSearchParams()
  if (activityDate) query.set('activity_date', activityDate)
  const suffix = query.size ? `?${query}` : ''
  return api<AdminQuotaUserSummary>(
    `/admin/quota/users/${userId}${suffix}`,
  )
}

export function createQuotaAdjustment(
  userId: string,
  input: {
    operation_id: string
    expected_daily_version: number
    activity_date: string
    mode: 'credit' | 'debit'
    amount: string
    reason: string
  },
) {
  return api<{ adjustment: QuotaAdjustment }>(
    `/admin/quota/users/${userId}/adjustments`,
    input,
  )
}

export function listAdminQuotaLedger(filters: {
  userId?: string
  activityDate?: string
  kind?: QuotaLedgerKind
  currency?: string
  operationId?: string
  cursor?: Page<AdminQuotaLedgerEntry>['next_cursor']
}) {
  const query = new URLSearchParams({ limit: '30' })
  if (filters.userId) query.set('user_id', filters.userId)
  if (filters.activityDate) query.set('activity_date', filters.activityDate)
  if (filters.kind) query.set('kind', filters.kind)
  if (filters.currency) query.set('currency', filters.currency)
  if (filters.operationId) query.set('operation_id', filters.operationId)
  if (filters.cursor) {
    query.set('before_time', filters.cursor.before_time)
    query.set('before_id', filters.cursor.before_id)
  }
  return api<Page<AdminQuotaLedgerEntry>>(`/admin/quota/ledger?${query}`)
}
