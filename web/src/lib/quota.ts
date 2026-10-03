import { api } from './api'

export type QuotaLedgerKind =
  | 'usage_charge'
  | 'admin_credit'
  | 'admin_debit'

export type QuotaUsageSource = 'provider' | 'estimated'

export interface QuotaDaily {
  id: string | null
  activity_date: string
  time_zone: string
  activity_day_start_minute: number
  window_start_at: string
  window_end_at: string
  policy_version: number
  currency: string
  base: string
  adjustment: string
  charged: string
  reserved: string
  available: string
  version: number
}

export interface QuotaReservation {
  id: string
  job_id: string
  capability: 'calendar_screenshot' | 'conversation'
  reserved: string
  accepted_at: string
}

export interface QuotaSummary {
  configured: boolean
  current: QuotaDaily | null
  reservations: QuotaReservation[]
}

export interface QuotaLedgerEntry {
  id: string
  activity_date: string
  kind: QuotaLedgerKind
  delta: string
  currency: string
  usage_source: QuotaUsageSource | null
  job: {
    id: string
    capability: 'calendar_screenshot' | 'conversation'
  } | null
  created_at: string
}

export interface QuotaLedgerPage {
  items: QuotaLedgerEntry[]
  next_cursor: {
    before_time: string
    before_id: string
  } | null
}

export function getQuota() {
  return api<QuotaSummary>('/quota')
}

export function listQuotaLedger(
  kind: QuotaLedgerKind | 'all',
  cursor: QuotaLedgerPage['next_cursor'] = null,
) {
  const query = new URLSearchParams({ limit: '30' })
  if (kind !== 'all') query.set('kind', kind)
  if (cursor) {
    query.set('before_time', cursor.before_time)
    query.set('before_id', cursor.before_id)
  }
  return api<QuotaLedgerPage>(`/quota/ledger?${query}`)
}
