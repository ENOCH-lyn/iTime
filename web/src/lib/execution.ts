import { api } from './api'
import type { Cursor, TaskDetail } from './tasks'
import type { TaskOccurrence } from './task-schedules'

export type FocusStatus = 'running' | 'paused' | 'finished' | 'abandoned'
export type FocusMode = 'stopwatch' | 'countdown'
export type FocusTransition = 'pause' | 'resume' | 'finish' | 'abandon'

export interface FocusStep {
  id: string
  title: string
  position: number
  completed: boolean
}

export interface FocusSession {
  id: string
  source: 'timer' | 'manual'
  task_id: string | null
  occurrence_key: string | null
  scheduled_block_id: string | null
  title: string
  task_title: string | null
  steps: FocusStep[]
  task_version: number | null
  occurrence_version: number | null
  task_status: 'open' | 'completed' | 'skipped' | null
  mode: FocusMode
  target_seconds: number | null
  status: FocusStatus
  started_at: string
  ended_at: string | null
  active_started_at: string | null
  accumulated_seconds: number
  effective_started_at: string | null
  effective_ended_at: string | null
  effective_duration_seconds: number | null
  time_zone: string
  version: number
  attention_required: boolean
  revised: boolean
  activity_day: string
  elapsed_seconds: number
  remaining_seconds: number | null
  overtime_seconds: number
  overtime: boolean
  server_now: string
}

export interface FocusEvent {
  id: string
  sequence: number
  kind: 'start' | 'pause' | 'resume' | 'finish' | 'abandon' | 'progress' | 'task_completed'
  occurred_at: string
  client_recorded_at: string | null
  source: 'online' | 'offline'
  details: Record<string, unknown>
  created_at: string
}

export interface ExecutionRevision {
  id: string
  session_version: number
  before_data: Record<string, unknown>
  after_data: Record<string, unknown>
  reason: string
  created_at: string
}

export interface FocusDetail {
  focus: FocusSession
  events: FocusEvent[]
  revisions: ExecutionRevision[]
  server_now: string
}

export interface FocusPageResult {
  items: FocusSession[]
  next_cursor: Cursor | null
  server_now: string
}

export interface FocusStartDraft {
  mode: FocusMode
  target_seconds: number | null
  title: string | null
  task_id: string | null
  occurrence_key: string | null
  scheduled_block_id: string | null
}

export interface FocusManualDraft {
  title: string
  task_id: string | null
  occurrence_key: string | null
  local_started_at: string
  local_ended_at: string
  time_zone: string
  start_utc_offset_minutes: number | null
  end_utc_offset_minutes: number | null
  effective_duration_seconds: number
}

export interface DifficultyRecord {
  id: string
  task_id: string | null
  focus_session_id: string | null
  occurrence_key: string | null
  task_title: string | null
  content: string
  category: string | null
  emotion: string | null
  occurred_at: string
  time_zone: string
  lifecycle: 'active' | 'trashed' | 'purged'
  deleted_at: string | null
  purge_after: string | null
  purged_at: string | null
  version: number
  created_at: string
  updated_at: string
}

export interface DifficultyDraft {
  task_id: string | null
  focus_session_id: string | null
  occurrence_key: string | null
  content: string
  category: string | null
  emotion: string | null
  occurred_at: string | null
}

export interface DifficultyPage {
  items: DifficultyRecord[]
  next_cursor: Cursor | null
}

export async function getCurrentFocus() {
  return api<{ focus: FocusSession | null; server_now: string }>(
    '/focus-sessions/current',
  )
}

export async function listFocusSessions(
  start: string,
  end: string,
  cursor?: Cursor | null,
) {
  const params = new URLSearchParams({ start, end, limit: '30' })
  if (cursor) {
    params.set('before_time', cursor.before_time)
    params.set('before_id', cursor.before_id)
  }
  return api<FocusPageResult>(`/focus-sessions?${params}`)
}

export async function getFocusSession(id: string) {
  return api<FocusDetail>(`/focus-sessions/${id}`)
}

export async function startFocus(operationId: string, draft: FocusStartDraft) {
  return api<{ operation_id: string; focus: FocusSession; server_now: string }>(
    '/focus-sessions',
    { operation_id: operationId, ...draft },
  )
}

export async function transitionFocus(
  focus: Pick<FocusSession, 'id' | 'version'>,
  operationId: string,
  action: FocusTransition,
  clientRecordedAt: string | null = null,
) {
  return api<{ operation_id: string; focus: FocusSession; server_now: string }>(
    `/focus-sessions/${focus.id}/transitions`,
    {
      operation_id: operationId,
      expected_version: focus.version,
      action,
      client_recorded_at: clientRecordedAt,
    },
  )
}

export async function updateFocusProgress(
  focus: Pick<
    FocusSession,
    'id' | 'version' | 'task_version' | 'occurrence_version'
  >,
  operationId: string,
  completedStepIds: string[],
  clientRecordedAt: string | null = null,
) {
  return api<{
    operation_id: string
    focus: FocusSession
    task: TaskDetail
    occurrence: TaskOccurrence | null
    server_now: string
  }>(
    `/focus-sessions/${focus.id}/progress`,
    {
      operation_id: operationId,
      expected_version: focus.version,
      expected_task_version: focus.task_version,
      expected_occurrence_version: focus.occurrence_version,
      completed_step_ids: completedStepIds,
      client_recorded_at: clientRecordedAt,
    },
    'PUT',
  )
}

export async function completeFocusTask(
  focus: FocusSession,
  operationId: string,
) {
  return api<{
    operation_id: string
    focus: FocusSession
    task: TaskDetail
    occurrence: TaskOccurrence | null
    server_now: string
  }>(
    `/focus-sessions/${focus.id}/complete-task`,
    {
      operation_id: operationId,
      expected_version: focus.version,
      expected_task_version: focus.task_version,
      expected_occurrence_version: focus.occurrence_version,
    },
  )
}

export async function createManualFocus(
  operationId: string,
  draft: FocusManualDraft,
) {
  return api<{ operation_id: string; focus: FocusSession; server_now: string }>(
    '/focus-sessions/manual',
    { operation_id: operationId, ...draft },
  )
}

export async function reviseFocus(
  focus: FocusSession,
  operationId: string,
  draft: FocusManualDraft,
  reason: string,
) {
  return api<{ operation_id: string; focus: FocusSession; server_now: string }>(
    `/focus-sessions/${focus.id}`,
    {
      operation_id: operationId,
      expected_version: focus.version,
      ...draft,
      reason,
    },
    'PUT',
  )
}

export async function listDifficulties(
  scope: 'active' | 'trashed',
  cursor?: Cursor | null,
) {
  const params = new URLSearchParams({ scope, limit: '30' })
  if (cursor) {
    params.set('before_time', cursor.before_time)
    params.set('before_id', cursor.before_id)
  }
  return api<DifficultyPage>(`/difficulties?${params}`)
}

export async function getDifficulty(id: string) {
  return (await api<{ difficulty: DifficultyRecord }>(
    `/difficulties/${id}`,
  )).difficulty
}

export async function createDifficulty(
  operationId: string,
  draft: DifficultyDraft,
) {
  return (await api<{ operation_id: string; difficulty: DifficultyRecord }>(
    '/difficulties',
    { operation_id: operationId, ...draft },
  )).difficulty
}

export async function updateDifficulty(
  difficulty: Pick<DifficultyRecord, 'id' | 'version'>,
  operationId: string,
  draft: DifficultyDraft,
) {
  return (await api<{ operation_id: string; difficulty: DifficultyRecord }>(
    `/difficulties/${difficulty.id}`,
    {
      operation_id: operationId,
      expected_version: difficulty.version,
      ...draft,
    },
    'PUT',
  )).difficulty
}

export async function deleteDifficulty(
  difficulty: Pick<DifficultyRecord, 'id' | 'version'>,
  operationId: string,
) {
  return (await api<{ operation_id: string; difficulty: DifficultyRecord }>(
    `/difficulties/${difficulty.id}`,
    {
      operation_id: operationId,
      expected_version: difficulty.version,
    },
    'DELETE',
  )).difficulty
}

export async function restoreDifficulty(
  difficulty: Pick<DifficultyRecord, 'id' | 'version'>,
  operationId: string,
) {
  return (await api<{ operation_id: string; difficulty: DifficultyRecord }>(
    `/difficulties/${difficulty.id}/restore`,
    {
      operation_id: operationId,
      expected_version: difficulty.version,
    },
  )).difficulty
}
