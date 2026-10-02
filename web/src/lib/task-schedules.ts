import { api } from './api'
import type { TaskDetail, TaskSummary } from './tasks'

export type RecurrenceFrequency =
  | 'none'
  | 'daily'
  | 'weekdays'
  | 'weekly'
  | 'monthly'
  | 'yearly'

export interface Recurrence {
  frequency: RecurrenceFrequency
  interval: number
  weekdays: number[]
  month: number | null
  day_of_month: number | null
  until: string | null
  count: number | null
  week_numbers: number[]
}

export interface TaskSchedule {
  configured: boolean
  preferences_saved: boolean
  predecessor_task_id: string | null
  due_kind: 'none' | 'date' | 'datetime'
  due_date: string | null
  last_occurrence_date: string | null
  due_time: string | null
  due_at: string | null
  time_zone: string | null
  ambiguous_utc_offset_minutes: number | null
  splittable: boolean
  minimum_block_minutes: number
  recurrence: Recurrence
}

export interface TaskScheduleDraft {
  due_kind: TaskSchedule['due_kind']
  due_date: string | null
  due_time: string | null
  time_zone: string | null
  ambiguous_utc_offset_minutes: number | null
  splittable: boolean
  minimum_block_minutes: number
  recurrence: Recurrence
}

export interface TaskOccurrence {
  task_id: string
  task_version: number
  occurrence_state_id: string | null
  occurrence_version: number
  occurrence_key: string
  kind: 'base' | 'makeup'
  makeup_for_key: string | null
  title: string
  description: string
  project_id: string | null
  estimated_minutes: number | null
  priority: number
  splittable: boolean
  minimum_block_minutes: number
  due_kind: 'date' | 'datetime'
  original_due_date: string
  due_date: string
  due_time: string | null
  due_at: string | null
  overdue_after: string | null
  time_zone: string
  status: 'open' | 'completed' | 'skipped'
  completed_at: string | null
  step_progress: { total: number; completed: number }
  completed_step_ids: string[]
  warning_codes: string[]
}

export interface ScheduleWarning {
  code: string
  occurrence_key: string
  message: string
}

export function emptyRecurrence(): Recurrence {
  return {
    frequency: 'none',
    interval: 1,
    weekdays: [],
    month: null,
    day_of_month: null,
    until: null,
    count: null,
    week_numbers: [],
  }
}

export async function updateTaskSchedule(
  task: Pick<TaskSummary, 'id' | 'version'>,
  operationId: string,
  fields: TaskScheduleDraft,
) {
  return api<{ operation_id: string; task: TaskDetail; warnings: ScheduleWarning[] }>(
    `/tasks/${task.id}/schedule`,
    {
      operation_id: operationId,
      expected_task_version: task.version,
      ...fields,
    },
    'PUT',
  )
}

export async function listTaskOccurrences(start: string, end: string) {
  const params = new URLSearchParams({ start, end })
  return api<{
    start: string
    end: string
    time_zone: string
    items: TaskOccurrence[]
  }>(`/task-occurrences?${params}`)
}

export async function updateTaskOccurrenceStatus(
  occurrence: TaskOccurrence,
  operationId: string,
  status: TaskOccurrence['status'],
  completedStepIds = occurrence.completed_step_ids,
) {
  return api<{ operation_id: string; task: TaskDetail; occurrence: TaskOccurrence }>(
    `/tasks/${occurrence.task_id}/occurrences/status`,
    {
      operation_id: operationId,
      expected_task_version: occurrence.task_version,
      expected_occurrence_version: occurrence.occurrence_version,
      occurrence_key: occurrence.occurrence_key,
      status,
      completed_step_ids: completedStepIds,
    },
    'PUT',
  )
}

export async function rescheduleTaskOccurrence(
  occurrence: TaskOccurrence,
  operationId: string,
  dueDate: string,
  dueTime: string | null,
) {
  return api<{ operation_id: string; task: TaskDetail; occurrence: TaskOccurrence }>(
    `/tasks/${occurrence.task_id}/occurrences/schedule`,
    {
      operation_id: operationId,
      expected_task_version: occurrence.task_version,
      expected_occurrence_version: occurrence.occurrence_version,
      occurrence_key: occurrence.occurrence_key,
      due_date: dueDate,
      due_time: dueTime,
      ambiguous_utc_offset_minutes: null,
    },
    'PUT',
  )
}
