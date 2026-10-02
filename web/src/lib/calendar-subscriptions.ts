import { api } from './api'

export type SubscriptionLifecycleStatus = 'active' | 'paused'
export type SubscriptionRefreshStatus = 'idle' | 'queued' | 'running' | 'error'
export type SubscriptionRefreshRunStatus =
  | 'running'
  | 'succeeded'
  | 'not_modified'
  | 'failed'
  | 'stale'
export type SubscriptionRefreshTrigger = 'initial' | 'manual' | 'automatic'
export type SubscriptionEventParseStatus = 'valid' | 'stale_error' | 'error'
export type SubscriptionEventSourceStatus = 'active' | 'cancelled'
export type SubscriptionOverrideField = 'name' | 'description' | 'location' | 'busy'
export type SubscriptionOverrideValue = string | boolean

export interface SubscriptionError {
  code: string
  message: string
}

export interface CalendarSubscription {
  id: string
  name: string
  url_display: string
  host: string
  final_host: string | null
  default_time_zone: string
  lifecycle_status: SubscriptionLifecycleStatus
  refresh_status: SubscriptionRefreshStatus
  refresh_interval_minutes: number
  next_refresh_at: string
  last_attempt_at: string | null
  last_success_at: string | null
  last_change_at: string | null
  consecutive_failures: number
  last_error: SubscriptionError | null
  generation: number
  version: number
  state_version: number
  event_count?: number
  error_event_count?: number
  created_at: string
  updated_at: string
}

export interface SubscriptionItemError extends SubscriptionError {
  uid?: string
  recurrence_id?: string | null
  position?: number
  [key: string]: unknown
}

export interface CalendarSubscriptionRefresh {
  id: string
  source_id: string
  attempt_number: number
  trigger: SubscriptionRefreshTrigger
  status: SubscriptionRefreshRunStatus
  http_status: number | null
  final_host: string | null
  event_count: number
  changed_count: number
  cancelled_count: number
  error_count: number
  item_errors: SubscriptionItemError[]
  error: SubscriptionError | null
  started_at: string
  finished_at: string | null
}

export type SubscriptionEventFields = Partial<Record<
  SubscriptionOverrideField,
  SubscriptionOverrideValue
>>

export interface SubscriptionFieldDifference {
  field: SubscriptionOverrideField
  source_at_override: SubscriptionOverrideValue | null
  source_current: SubscriptionOverrideValue | null
  local_value: SubscriptionOverrideValue
}

export interface CalendarSubscriptionEvent {
  id: string
  uid: string
  recurrence_id: string | null
  parse_status: SubscriptionEventParseStatus
  source_status: SubscriptionEventSourceStatus
  source_fields: SubscriptionEventFields
  effective_fields: SubscriptionEventFields
  override_fields: SubscriptionOverrideField[]
  differences: SubscriptionFieldDifference[]
  error: SubscriptionError | null
  version: number
  override_version: number
  updated_at: string
}

export interface SubscriptionEventCursor {
  before_event_time: string
  before_event_id: string
}

export interface CalendarSubscriptionDetail {
  subscription: CalendarSubscription
  events: CalendarSubscriptionEvent[]
  event_has_more: boolean
  next_event_cursor: SubscriptionEventCursor | null
  refreshes: CalendarSubscriptionRefresh[]
}

export interface CalendarSubscriptionDraft {
  name: string
  url: string
  refresh_interval_minutes: number
}

export interface CalendarSubscriptionUpdate {
  name: string
  refresh_interval_minutes: number
  url?: string
}

export interface CalendarSubscriptionOverrideUpdate {
  values: SubscriptionEventFields
  reset_fields: SubscriptionOverrideField[]
}

interface SubscriptionResponse {
  operation_id: string
  subscription: CalendarSubscription
}

interface SubscriptionEventResponse {
  operation_id: string
  event: CalendarSubscriptionEvent
}

export async function listCalendarSubscriptions() {
  return (await api<{ items: CalendarSubscription[] }>(
    '/calendar/subscriptions',
  )).items
}

export async function getCalendarSubscription(
  sourceId: string,
  cursor: SubscriptionEventCursor | null = null,
  eventLimit = 50,
) {
  const params = new URLSearchParams({ event_limit: String(eventLimit) })
  if (cursor) {
    params.set('before_event_time', cursor.before_event_time)
    params.set('before_event_id', cursor.before_event_id)
  }
  return api<CalendarSubscriptionDetail>(
    `/calendar/subscriptions/${sourceId}?${params}`,
  )
}

export async function createCalendarSubscription(
  draft: CalendarSubscriptionDraft,
  operationId: string = crypto.randomUUID(),
) {
  return (await api<SubscriptionResponse>('/calendar/subscriptions', {
    operation_id: operationId,
    ...draft,
  })).subscription
}

export async function updateCalendarSubscription(
  subscription: CalendarSubscription,
  fields: CalendarSubscriptionUpdate,
  operationId: string = crypto.randomUUID(),
) {
  return (await api<SubscriptionResponse>(
    `/calendar/subscriptions/${subscription.id}`,
    {
      operation_id: operationId,
      expected_version: subscription.version,
      ...fields,
    },
    'PUT',
  )).subscription
}

export async function refreshCalendarSubscription(
  sourceId: string,
  operationId: string = crypto.randomUUID(),
) {
  return (await api<SubscriptionResponse>(
    `/calendar/subscriptions/${sourceId}/refresh`,
    { operation_id: operationId },
  )).subscription
}

async function setCalendarSubscriptionLifecycle(
  subscription: CalendarSubscription,
  action: 'pause' | 'resume',
  operationId: string,
) {
  return (await api<SubscriptionResponse>(
    `/calendar/subscriptions/${subscription.id}/${action}`,
    {
      operation_id: operationId,
      expected_version: subscription.version,
    },
  )).subscription
}

export async function pauseCalendarSubscription(
  subscription: CalendarSubscription,
  operationId: string = crypto.randomUUID(),
) {
  return setCalendarSubscriptionLifecycle(subscription, 'pause', operationId)
}

export async function resumeCalendarSubscription(
  subscription: CalendarSubscription,
  operationId: string = crypto.randomUUID(),
) {
  return setCalendarSubscriptionLifecycle(subscription, 'resume', operationId)
}

export async function updateCalendarSubscriptionOverride(
  sourceId: string,
  event: CalendarSubscriptionEvent,
  update: CalendarSubscriptionOverrideUpdate,
  operationId: string = crypto.randomUUID(),
) {
  return (await api<SubscriptionEventResponse>(
    `/calendar/subscriptions/${sourceId}/events/${event.id}/overrides`,
    {
      operation_id: operationId,
      expected_version: event.override_version,
      ...update,
    },
    'PUT',
  )).event
}
