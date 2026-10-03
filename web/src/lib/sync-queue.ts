import { api, ApiError, type Failure } from './api'
import type { TaskDetail } from './tasks'
import type { TaskOccurrence } from './task-schedules'

const QUEUE_PREFIX = 'itime-sync-queue'
const MAX_QUEUE_ITEMS = 100
const MAX_QUEUE_AGE_MS = 7 * 24 * 60 * 60 * 1000
const MAX_FUTURE_SKEW_MS = 5 * 60 * 1000
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

interface TaskStepCompletion {
  schema_version: 1
  kind: 'task_step_completion'
  operation_id: string
  task_id: string
  step_id: string
  expected_task_version: number
  base_completed: boolean
  completed: boolean
  created_at: string
}

interface TaskOccurrenceStepCompletion {
  schema_version: 1
  kind: 'task_occurrence_step_completion'
  operation_id: string
  task_id: string
  occurrence_key: string
  step_id: string
  expected_task_version: number
  expected_occurrence_version: number
  base_completed: boolean
  completed: boolean
  created_at: string
}

export type QueuedTaskOperation =
  | TaskStepCompletion
  | TaskOccurrenceStepCompletion

export interface TaskSyncResult {
  operation_id: string
  merge: 'applied' | 'rebased' | 'converged'
  task: TaskDetail
  occurrence?: TaskOccurrence
}

export interface TaskQueueReplay {
  pending: QueuedTaskOperation[]
  conflict: QueuedTaskOperation | null
  conflict_error: Failure | null
  latest: TaskSyncResult | null
}

export class SyncQueueError extends Error {
  constructor(
    public code:
      | 'sync_queue_full'
      | 'sync_queue_unavailable'
      | 'invalid_sync_operation',
    message: string,
  ) {
    super(message)
  }
}

function queueKey(userId: string) {
  return `${QUEUE_PREFIX}:${userId}`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function validInstant(value: unknown, current: number) {
  if (typeof value !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) {
    return false
  }
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp)
    && timestamp <= current + MAX_FUTURE_SKEW_MS
    && current - timestamp <= MAX_QUEUE_AGE_MS
}

function validVersion(value: unknown) {
  return Number.isInteger(value) && Number(value) >= 0
}

function parseOperation(
  value: unknown,
  current: number,
): QueuedTaskOperation | null {
  if (!isRecord(value)) return null
  if (
    value.schema_version !== 1
    || !['task_step_completion', 'task_occurrence_step_completion'].includes(
      String(value.kind),
    )
    || typeof value.operation_id !== 'string'
    || !UUID_PATTERN.test(value.operation_id)
    || typeof value.task_id !== 'string'
    || !UUID_PATTERN.test(value.task_id)
    || typeof value.step_id !== 'string'
    || !UUID_PATTERN.test(value.step_id)
    || !validVersion(value.expected_task_version)
    || typeof value.base_completed !== 'boolean'
    || typeof value.completed !== 'boolean'
    || value.base_completed === value.completed
    || !validInstant(value.created_at, current)
  ) return null
  if (value.kind === 'task_occurrence_step_completion') {
    if (
      typeof value.occurrence_key !== 'string'
      || value.occurrence_key.length < 10
      || value.occurrence_key.length > 80
      || !validVersion(value.expected_occurrence_version)
    ) return null
    return value as unknown as TaskOccurrenceStepCompletion
  }
  return value as unknown as TaskStepCompletion
}

function writeQueue(userId: string, items: QueuedTaskOperation[]) {
  try {
    const key = queueKey(userId)
    if (items.length) localStorage.setItem(key, JSON.stringify(items))
    else localStorage.removeItem(key)
    window.dispatchEvent(new CustomEvent('itime:sync-queue-changed'))
  } catch {
    throw new SyncQueueError(
      'sync_queue_unavailable',
      '浏览器无法保存离线操作，请联网后重试。',
    )
  }
}

export function loadTaskQueue(userId: string): QueuedTaskOperation[] {
  if (!UUID_PATTERN.test(userId)) return []
  let value: unknown
  try {
    const stored = localStorage.getItem(queueKey(userId))
    if (stored === null) return []
    value = JSON.parse(stored)
  } catch {
    writeQueue(userId, [])
    return []
  }
  if (!Array.isArray(value)) {
    writeQueue(userId, [])
    return []
  }
  const current = Date.now()
  const operationIds = new Set<string>()
  const valid: QueuedTaskOperation[] = []
  for (const item of value) {
    const operation = parseOperation(item, current)
    if (
      operation === null
      || operationIds.has(operation.operation_id)
      || valid.length >= MAX_QUEUE_ITEMS
    ) continue
    operationIds.add(operation.operation_id)
    valid.push(operation)
  }
  if (JSON.stringify(valid) !== JSON.stringify(value)) writeQueue(userId, valid)
  return valid
}

export function appendTaskOperation(
  userId: string,
  item: QueuedTaskOperation,
) {
  const operation = parseOperation(item, Date.now())
  if (operation === null) {
    throw new SyncQueueError(
      'invalid_sync_operation',
      '此操作不能加入离线队列，请刷新后重试。',
    )
  }
  const items = loadTaskQueue(userId)
  if (items.some(existing => existing.operation_id === item.operation_id)) return
  if (items.length >= MAX_QUEUE_ITEMS) {
    throw new SyncQueueError(
      'sync_queue_full',
      '离线队列最多保留 100 项，请联网同步后继续。',
    )
  }
  writeQueue(userId, [...items, operation])
}

export function clearTaskQueue(userId: string) {
  writeQueue(userId, [])
}

export function discardTaskOperation(userId: string, operationId: string) {
  const items = loadTaskQueue(userId)
  writeQueue(
    userId,
    items.filter(item => item.operation_id !== operationId),
  )
}

async function sendOperation(item: QueuedTaskOperation) {
  if (item.kind === 'task_step_completion') {
    return api<TaskSyncResult>(
      `/sync/tasks/${item.task_id}/steps/${item.step_id}/completion`,
      {
        operation_id: item.operation_id,
        expected_task_version: item.expected_task_version,
        base_completed: item.base_completed,
        completed: item.completed,
      },
      'PUT',
    )
  }
  return api<TaskSyncResult>(
    '/sync/task-occurrences/steps/completion',
    {
      operation_id: item.operation_id,
      task_id: item.task_id,
      occurrence_key: item.occurrence_key,
      step_id: item.step_id,
      expected_task_version: item.expected_task_version,
      expected_occurrence_version: item.expected_occurrence_version,
      base_completed: item.base_completed,
      completed: item.completed,
    },
    'PUT',
  )
}

export async function replayTaskQueue(userId: string): Promise<TaskQueueReplay> {
  let pending = loadTaskQueue(userId)
  let latest: TaskSyncResult | null = null
  while (pending.length) {
    const item = pending[0]
    try {
      latest = await sendOperation(item)
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        return {
          pending,
          conflict: item,
          conflict_error: error.info,
          latest,
        }
      }
      throw error
    }
    const current = loadTaskQueue(userId)
    if (current[0]?.operation_id !== item.operation_id) {
      throw new SyncQueueError(
        'invalid_sync_operation',
        '待同步操作已经变化，请刷新后核对。',
      )
    }
    writeQueue(userId, current.slice(1))
    pending = loadTaskQueue(userId)
  }
  return { pending, conflict: null, conflict_error: null, latest }
}
