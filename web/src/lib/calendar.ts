import { api } from './api'
import type { Recurrence, TaskOccurrence } from './task-schedules'
import type { PlanningBlockProjection } from './plans'

export interface TimeSettings {
  initialized: boolean
  time_zone: string | null
  week_starts_on: 1
  activity_day_start_minute: 240
  version: number
  created_at: string | null
  updated_at: string | null
}

export interface TermPeriod {
  period_number: number
  name: string
  starts_at: string
  ends_at: string
}

export interface Term {
  id: string
  name: string
  start_date: string
  end_date: string
  first_week_monday: string
  time_zone: string
  period_count: number
  periods?: TermPeriod[]
  version: number
  created_at: string
  updated_at: string
}

export type CalendarLifecycle = 'active' | 'trashed' | 'purged'

export interface CalendarLifecycleFields {
  lifecycle: CalendarLifecycle
  deleted_at: string | null
  purge_after: string | null
  purged_at: string | null
  trash_remaining_days: number | null
}

export interface CalendarSeries extends CalendarLifecycleFields {
  id: string
  kind: 'course' | 'fixed'
  term_id: string | null
  predecessor_id: string | null
  source_id: string | null
  name: string
  teacher: string
  description: string
  location: string
  course_group_id: string | null
  busy: boolean
  source_type: 'manual' | 'import' | 'screenshot'
  time_zone: string
  start_date: string
  starts_at: string
  ends_at: string
  end_day_offset: 0 | 1
  start_period_number: number | null
  end_period_number: number | null
  ambiguous_utc_offset_minutes: number | null
  recurrence: Recurrence
  version: number
  created_at: string
  updated_at: string
}

export interface CalendarConflictLink {
  series_id: string | null
  subscription_event_id: string | null
  occurrence_key: string
  occurrence_type: 'base' | 'addition' | 'subscription'
  addition_id: string | null
  overlap_start: string
  overlap_end: string
}

export interface CalendarOccurrence {
  series_id: string | null
  series_version: number | null
  subscription_event_id?: string | null
  subscription_master_event_id?: string | null
  source_event_version?: number | null
  source_version?: number | null
  occurrence_key: string
  occurrence_type: 'base' | 'addition' | 'subscription'
  kind: 'course' | 'fixed'
  term_id: string | null
  source_id: string | null
  source_type: 'manual' | 'import' | 'screenshot' | 'subscription'
  name: string
  teacher: string
  description: string
  location: string
  course_group_id: string | null
  busy: boolean
  status: 'scheduled' | 'cancelled' | 'source_cancelled'
  source_status?: 'scheduled' | 'source_cancelled'
  time_zone: string
  all_day?: boolean
  original_local_start_date: string
  original_local_start_time: string | null
  local_start_date: string
  local_start_time: string | null
  local_end_date: string
  local_end_time: string | null
  start_at: string | null
  end_at: string | null
  warning_codes: string[]
  exception_id: string | null
  exception_version: number | null
  exception_action: 'cancelled' | 'moved' | 'overridden' | null
  addition_id: string | null
  addition_version: number | null
  source_occurrence_key: string | null
  conflict_with: CalendarConflictLink[]
}

export interface CalendarException {
  id: string
  series_id: string
  occurrence_key: string
  original_start_date: string
  action: 'cancelled' | 'moved' | 'overridden'
  moved_start_date: string | null
  moved_starts_at: string | null
  moved_ends_at: string | null
  moved_end_day_offset: 0 | 1 | null
  moved_time_zone: string | null
  moved_ambiguous_utc_offset_minutes: number | null
  override_name: string | null
  override_description: string | null
  override_location: string | null
  override_busy: boolean | null
  version: number
  created_at: string
  updated_at: string
}

export interface CalendarSubscriptionException {
  id: string
  master_event_id: string
  source_occurrence_key: string
  original_start_at: string
  original_end_at: string
  original_all_day: boolean
  action: 'cancelled' | 'moved' | 'overridden'
  moved_all_day: boolean | null
  moved_start_date: string | null
  moved_end_date: string | null
  moved_starts_at: string | null
  moved_ends_at: string | null
  moved_time_zone: string | null
  moved_ambiguous_utc_offset_minutes: number | null
  moved_start_at: string | null
  moved_end_at: string | null
  override_name: string | null
  override_description: string | null
  override_location: string | null
  override_busy: boolean | null
  version: number
  created_at: string
  updated_at: string
}

export interface SubscriptionExceptionFields {
  action: CalendarSubscriptionException['action']
  moved_all_day: boolean | null
  moved_start_date: string | null
  moved_end_date: string | null
  moved_starts_at: string | null
  moved_ends_at: string | null
  moved_time_zone: string | null
  moved_ambiguous_utc_offset_minutes: number | null
  override_name: string | null
  override_description: string | null
  override_location: string | null
  override_busy: boolean | null
}

export interface SubscriptionExceptionWriteResult {
  operation_id: string
  source_version: number
  exception: CalendarSubscriptionException | null
  occurrence: CalendarOccurrence
  conflicts: CalendarWriteResult['conflicts']
}

export interface CalendarAddition {
  id: string
  series_id: string
  occurrence_key: string
  source_occurrence_key: string
  source_original_start_date: string
  status: 'scheduled' | 'cancelled'
  local_start_date: string
  starts_at: string
  ends_at: string
  end_day_offset: 0 | 1
  time_zone: string
  ambiguous_utc_offset_minutes: number | null
  override_name: string | null
  override_description: string | null
  override_location: string | null
  override_busy: boolean | null
  version: number
  created_at: string
  updated_at: string
}

export interface SeriesDraft {
  kind: CalendarSeries['kind']
  term_id: string | null
  name: string
  teacher: string
  description: string
  location: string
  course_group_id?: string | null
  busy: boolean
  time_zone: string | null
  start_date: string | null
  starts_at: string | null
  ends_at: string | null
  end_day_offset: 0 | 1
  start_period_number: number | null
  end_period_number: number | null
  ambiguous_utc_offset_minutes: number | null
  recurrence: Recurrence
}

export interface CalendarConflict {
  occurrence_key: string
  occurrence_type: 'base' | 'addition' | 'subscription'
  conflicting_series_id: string | null
  conflicting_subscription_event_id?: string | null
  conflicting_occurrence_key: string
  conflicting_occurrence_type: 'base' | 'addition' | 'subscription'
  overlap_start: string
  overlap_end: string
}

export interface CalendarWriteResult {
  series: CalendarSeries
  warnings: { code: string; occurrence_key: string; message: string }[]
  conflicts: { items: CalendarConflict[]; has_more: boolean }
}

export interface CalendarRange {
  start: string
  end: string
}

export interface UnifiedCalendarData {
  time_zone: string
  calendar: CalendarOccurrence[]
  tasks: TaskOccurrence[]
}

export interface CalendarDisplayItem {
  id: string
  source: 'calendar' | 'task' | 'plan'
  date: string
  end_date: string
  time: string | null
  end_time: string | null
  title: string
  subtitle: string
  state: 'scheduled' | 'cancelled' | 'source_cancelled' | 'open' | 'completed' | 'skipped'
  kind: 'course' | 'fixed' | 'task' | 'plan'
  conflict: boolean
  calendar?: CalendarOccurrence
  task?: TaskOccurrence
  plan?: PlanningBlockProjection
}

export async function getTimeSettings() {
  return (await api<{ settings: TimeSettings }>('/time-settings')).settings
}

export async function saveTimeSettings(
  settings: TimeSettings,
  timeZone: string,
  operationId: string = crypto.randomUUID(),
) {
  return (await api<{ settings: TimeSettings }>('/time-settings', {
    operation_id: operationId,
    expected_version: settings.version,
    time_zone: timeZone,
    week_starts_on: 1,
    activity_day_start_minute: 240,
  }, 'PUT')).settings
}

export async function listTerms() {
  const items: Term[] = []
  let cursor: { before_time: string; before_id: string } | null = null
  do {
    const params = new URLSearchParams({ limit: '100' })
    if (cursor) {
      params.set('before_time', cursor.before_time)
      params.set('before_id', cursor.before_id)
    }
    const page = await api<{
      items: Term[]
      next_cursor: { before_time: string; before_id: string } | null
    }>(`/terms?${params}`)
    items.push(...page.items)
    cursor = page.next_cursor
  } while (cursor)
  return items
}

export async function getTerm(id: string) {
  return (await api<{ term: Term }>(`/terms/${id}`)).term
}

export async function saveTerm(
  term: Term | null,
  fields: Pick<Term, 'name' | 'start_date' | 'end_date' | 'first_week_monday'>,
  timeZone: string,
  operationId: string = crypto.randomUUID(),
) {
  const body = {
    operation_id: operationId,
    ...(term ? { expected_version: term.version } : {}),
    ...fields,
    time_zone: timeZone,
  }
  return (await api<{ term: Term }>(
    term ? `/terms/${term.id}` : '/terms',
    body,
    term ? 'PUT' : 'POST',
  )).term
}

export async function replaceTermPeriods(
  term: Term,
  periods: TermPeriod[],
  operationId: string = crypto.randomUUID(),
) {
  return (await api<{ term: Term }>(`/terms/${term.id}/periods`, {
    operation_id: operationId,
    expected_version: term.version,
    periods,
  }, 'PUT')).term
}

export async function listSeries(
  lifecycle: Exclude<CalendarLifecycle, 'purged'> = 'active',
) {
  const items: CalendarSeries[] = []
  let cursor: { before_time: string; before_id: string } | null = null
  do {
    const params = new URLSearchParams({ limit: '100', lifecycle })
    if (cursor) {
      params.set('before_time', cursor.before_time)
      params.set('before_id', cursor.before_id)
    }
    const page = await api<{
      items: CalendarSeries[]
      next_cursor: { before_time: string; before_id: string } | null
    }>(`/calendar/series?${params}`)
    items.push(...page.items)
    cursor = page.next_cursor
  } while (cursor)
  return items
}

export async function getSeries(id: string) {
  return (await api<{ series: CalendarSeries }>(`/calendar/series/${id}`)).series
}

export async function trashCalendarSeries(
  series: Pick<CalendarSeries, 'id' | 'version'>,
  operationId: string = crypto.randomUUID(),
) {
  return (await api<{ operation_id: string; series: CalendarSeries }>(
    `/calendar/series/${series.id}`,
    {
      operation_id: operationId,
      expected_version: series.version,
    },
    'DELETE',
  )).series
}

export async function restoreCalendarSeries(
  series: Pick<CalendarSeries, 'id' | 'version'>,
  operationId: string = crypto.randomUUID(),
) {
  return (await api<{ operation_id: string; series: CalendarSeries }>(
    `/calendar/series/${series.id}/restore`,
    {
      operation_id: operationId,
      expected_version: series.version,
    },
  )).series
}

export async function saveSeries(
  series: CalendarSeries | null,
  fields: SeriesDraft,
  operationId: string = crypto.randomUUID(),
) {
  return api<CalendarWriteResult>(
    series ? `/calendar/series/${series.id}` : '/calendar/series',
    {
      operation_id: operationId,
      ...(series ? { expected_version: series.version } : {}),
      ...fields,
    },
    series ? 'PUT' : 'POST',
  )
}

export async function splitSeries(
  series: CalendarSeries,
  occurrenceKey: string,
  replacement: SeriesDraft,
  operationId: string = crypto.randomUUID(),
) {
  return api<{
    predecessor: CalendarSeries
    successor: CalendarSeries
    moved_exception_count: number
    moved_addition_count: number
    warnings: CalendarWriteResult['warnings']
    conflicts: CalendarWriteResult['conflicts']
  }>(`/calendar/series/${series.id}/split`, {
    operation_id: operationId,
    expected_version: series.version,
    occurrence_key: occurrenceKey,
    replacement,
  })
}

export async function listCalendarOccurrences(range: CalendarRange) {
  const params = new URLSearchParams({ start: range.start, end: range.end })
  return api<{
    start: string
    end: string
    time_zone: string
    items: CalendarOccurrence[]
  }>(`/calendar/occurrences?${params}`)
}

export async function listCalendarExceptions(seriesId: string) {
  return api<{ series_version: number; items: CalendarException[] }>(
    `/calendar/series/${seriesId}/exceptions`,
  )
}

export async function saveOccurrenceException(
  occurrence: CalendarOccurrence,
  fields: {
    action: CalendarException['action']
    moved_start_date: string | null
    moved_starts_at: string | null
    moved_ends_at: string | null
    moved_end_day_offset: 0 | 1 | null
    moved_time_zone: string | null
    moved_ambiguous_utc_offset_minutes: number | null
    override_name: string | null
    override_description: string | null
    override_location: string | null
    override_busy: boolean | null
  },
  operationId: string = crypto.randomUUID(),
) {
  if (!occurrence.series_id || occurrence.series_version === null) {
    throw new Error('calendar_series_not_editable')
  }
  const existing = occurrence.exception_id
    ? (await listCalendarExceptions(occurrence.series_id)).items.find(
      item => item.id === occurrence.exception_id,
    )
    : null
  const path = existing
    ? `/calendar/series/${occurrence.series_id}/exceptions/${existing.id}`
    : `/calendar/series/${occurrence.series_id}/exceptions`
  return api<{
    series: CalendarSeries
    exception: CalendarException
    conflicts: CalendarWriteResult['conflicts']
  }>(path, {
    operation_id: operationId,
    expected_series_version: occurrence.series_version,
    ...(existing
      ? { expected_version: existing.version }
      : { occurrence_key: occurrence.occurrence_key }),
    ...fields,
  }, existing ? 'PUT' : 'POST')
}

export async function saveSubscriptionOccurrenceException(
  occurrence: CalendarOccurrence,
  fields: SubscriptionExceptionFields,
  operationId: string = crypto.randomUUID(),
) {
  if (
    !occurrence.source_id
    || !occurrence.subscription_master_event_id
    || !occurrence.source_occurrence_key
    || occurrence.source_version == null
  ) {
    throw new Error('calendar_subscription_occurrence_not_editable')
  }
  const path = occurrence.exception_id
    ? `/calendar/subscriptions/${occurrence.source_id}/exceptions/${occurrence.exception_id}`
    : `/calendar/subscriptions/${occurrence.source_id}/exceptions`
  return api<SubscriptionExceptionWriteResult>(path, {
    operation_id: operationId,
    expected_source_version: occurrence.source_version,
    ...(occurrence.exception_id
      ? { expected_version: occurrence.exception_version }
      : {
          master_event_id: occurrence.subscription_master_event_id,
          source_occurrence_key: occurrence.source_occurrence_key,
        }),
    ...fields,
  }, occurrence.exception_id ? 'PUT' : 'POST')
}

export async function resetSubscriptionOccurrenceException(
  occurrence: CalendarOccurrence,
  operationId: string = crypto.randomUUID(),
) {
  if (
    !occurrence.source_id
    || !occurrence.exception_id
    || occurrence.exception_version == null
    || occurrence.source_version == null
  ) {
    throw new Error('calendar_subscription_exception_not_editable')
  }
  return api<SubscriptionExceptionWriteResult>(
    `/calendar/subscriptions/${occurrence.source_id}/exceptions/${occurrence.exception_id}`,
    {
      operation_id: operationId,
      expected_source_version: occurrence.source_version,
      expected_version: occurrence.exception_version,
    },
    'DELETE',
  )
}

export async function listCalendarAdditions(seriesId: string) {
  return api<{ series_version: number; items: CalendarAddition[] }>(
    `/calendar/series/${seriesId}/additions`,
  )
}

export async function createCalendarAddition(
  occurrence: CalendarOccurrence,
  fields: {
    local_start_date: string
    starts_at: string
    ends_at: string
    end_day_offset: 0 | 1
    time_zone: string | null
    ambiguous_utc_offset_minutes: number | null
    override_name: string | null
    override_description: string | null
    override_location: string | null
    override_busy: boolean | null
  },
  operationId: string = crypto.randomUUID(),
) {
  if (!occurrence.series_id || occurrence.series_version === null) {
    throw new Error('calendar_series_not_editable')
  }
  return api<{
    series: CalendarSeries
    addition: CalendarAddition
    conflicts: CalendarWriteResult['conflicts']
  }>(`/calendar/series/${occurrence.series_id}/additions`, {
    operation_id: operationId,
    expected_series_version: occurrence.series_version,
    source_occurrence_key: occurrence.occurrence_key,
    status: 'scheduled',
    ...fields,
  })
}

export async function updateCalendarAddition(
  occurrence: CalendarOccurrence,
  status: CalendarAddition['status'],
  operationId: string = crypto.randomUUID(),
) {
  if (!occurrence.series_id || occurrence.series_version === null) {
    throw new Error('calendar_series_not_editable')
  }
  const additions = await listCalendarAdditions(occurrence.series_id)
  const addition = additions.items.find(item => item.id === occurrence.addition_id)
  if (!addition) throw new Error('calendar_addition_not_found')
  return api<{
    series: CalendarSeries
    addition: CalendarAddition
    conflicts: CalendarWriteResult['conflicts']
  }>(`/calendar/series/${occurrence.series_id}/additions/${addition.id}`, {
    operation_id: operationId,
    expected_series_version: occurrence.series_version,
    expected_version: addition.version,
    status,
    local_start_date: addition.local_start_date,
    starts_at: addition.starts_at,
    ends_at: addition.ends_at,
    end_day_offset: addition.end_day_offset,
    time_zone: addition.time_zone,
    ambiguous_utc_offset_minutes: addition.ambiguous_utc_offset_minutes,
    override_name: addition.override_name,
    override_description: addition.override_description,
    override_location: addition.override_location,
    override_busy: addition.override_busy,
  }, 'PUT')
}
