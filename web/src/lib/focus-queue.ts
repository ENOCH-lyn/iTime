import { ApiError, type Failure } from './api'
import type { FocusSession, FocusTransition } from './execution'

const QUEUE_PREFIX = 'itime-focus-queue'
const MAX_QUEUE_ITEMS = 100
const MAX_QUEUE_AGE_MS = 7 * 24 * 60 * 60 * 1000
const MAX_FUTURE_SKEW_MS = 5 * 60 * 1000
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
const TRANSITIONS = new Set(['pause', 'resume', 'finish', 'abandon'])
const ACTIONS = new Set([...TRANSITIONS, 'progress'])

export type QueuedFocusAction = FocusTransition | 'progress'

export interface QueuedFocusOperation {
  operation_id: string
  session_id: string
  expected_version: number
  action: QueuedFocusAction
  client_recorded_at: string
  payload: Record<string, unknown>
  created_at: string
}

export interface ReplayResult {
  session: FocusSession | null
  pending: QueuedFocusOperation[]
  conflict: QueuedFocusOperation | null
  conflict_error: Failure | null
}

export type SendFocusOperation = (
  item: QueuedFocusOperation,
) => Promise<FocusSession>

export class FocusQueueError extends Error {
  constructor(
    public code: 'focus_queue_full' | 'focus_queue_unavailable' | 'invalid_focus_operation',
    message: string,
  ) {
    super(message)
  }
}

function queueKey(userId: string, sessionId: string) {
  return `${QUEUE_PREFIX}:${userId}:${sessionId}`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isInteger(value: unknown, minimum: number) {
  return Number.isInteger(value) && Number(value) >= minimum
}

function validInstant(value: unknown, now: number) {
  if (typeof value !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return false
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) && timestamp <= now + MAX_FUTURE_SKEW_MS
}

function validProgressPayload(payload: Record<string, unknown>) {
  const keys = Object.keys(payload).sort()
  if (
    keys.join(',') !== [
      'completed_step_ids',
      'expected_occurrence_version',
      'expected_task_version',
    ].join(',')
  ) return false
  if (!isInteger(payload.expected_task_version, 1)) return false
  if (
    payload.expected_occurrence_version !== null
    && !isInteger(payload.expected_occurrence_version, 0)
  ) return false
  if (
    !Array.isArray(payload.completed_step_ids)
    || payload.completed_step_ids.length > 100
    || !payload.completed_step_ids.every(value =>
      typeof value === 'string' && UUID_PATTERN.test(value))
    || new Set(payload.completed_step_ids).size !== payload.completed_step_ids.length
  ) return false
  return true
}

function validPayload(action: QueuedFocusAction, payload: Record<string, unknown>) {
  return action === 'progress'
    ? validProgressPayload(payload)
    : Object.keys(payload).length === 0
}

function parseOperation(
  value: unknown,
  sessionId: string,
  now: number,
): QueuedFocusOperation | null {
  if (!isRecord(value)) return null
  const action = value.action
  if (
    typeof action !== 'string'
    || !ACTIONS.has(action)
    || typeof value.operation_id !== 'string'
    || !UUID_PATTERN.test(value.operation_id)
    || value.session_id !== sessionId
    || !UUID_PATTERN.test(sessionId)
    || !isInteger(value.expected_version, 1)
    || !validInstant(value.client_recorded_at, now)
    || !validInstant(value.created_at, now)
    || now - Date.parse(value.created_at as string) > MAX_QUEUE_AGE_MS
    || !isRecord(value.payload)
    || !validPayload(action as QueuedFocusAction, value.payload)
  ) return null
  return {
    operation_id: value.operation_id,
    session_id: value.session_id,
    expected_version: Number(value.expected_version),
    action: action as QueuedFocusAction,
    client_recorded_at: value.client_recorded_at as string,
    payload: value.payload,
    created_at: value.created_at as string,
  }
}

function writeQueue(
  userId: string,
  sessionId: string,
  items: QueuedFocusOperation[],
) {
  try {
    const key = queueKey(userId, sessionId)
    if (items.length) localStorage.setItem(key, JSON.stringify(items))
    else localStorage.removeItem(key)
  } catch {
    throw new FocusQueueError(
      'focus_queue_unavailable',
      '浏览器无法保存离线操作，请联网后重试。',
    )
  }
}

export function loadFocusQueue(
  userId: string,
  sessionId: string,
): QueuedFocusOperation[] {
  if (!UUID_PATTERN.test(userId) || !UUID_PATTERN.test(sessionId)) return []
  let parsed: unknown
  try {
    const stored = localStorage.getItem(queueKey(userId, sessionId))
    if (stored === null) return []
    parsed = JSON.parse(stored)
  } catch {
    writeQueue(userId, sessionId, [])
    return []
  }
  if (!Array.isArray(parsed)) {
    writeQueue(userId, sessionId, [])
    return []
  }
  const now = Date.now()
  const operationIds = new Set<string>()
  const valid: QueuedFocusOperation[] = []
  for (const value of parsed) {
    const operation = parseOperation(value, sessionId, now)
    if (
      operation === null
      || operationIds.has(operation.operation_id)
      || valid.length >= MAX_QUEUE_ITEMS
    ) continue
    operationIds.add(operation.operation_id)
    valid.push(operation)
  }
  if (
    valid.length !== parsed.length
    || JSON.stringify(valid) !== JSON.stringify(parsed)
  ) writeQueue(userId, sessionId, valid)
  return valid
}

export function listFocusQueueSessions(userId: string) {
  if (!UUID_PATTERN.test(userId)) return []
  const prefix = `${QUEUE_PREFIX}:${userId}:`
  const sessions: { sessionId: string; createdAt: number }[] = []
  try {
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index)
      if (!key?.startsWith(prefix)) continue
      const sessionId = key.slice(prefix.length)
      if (!UUID_PATTERN.test(sessionId)) continue
      const items = loadFocusQueue(userId, sessionId)
      if (!items.length) continue
      sessions.push({
        sessionId,
        createdAt: Date.parse(items[items.length - 1].created_at),
      })
    }
  } catch {
    return []
  }
  return sessions
    .sort((left, right) => right.createdAt - left.createdAt)
    .map(item => item.sessionId)
}

export function appendFocusOperation(
  userId: string,
  item: QueuedFocusOperation,
) {
  const valid = parseOperation(item, item.session_id, Date.now())
  if (valid === null) {
    throw new FocusQueueError(
      'invalid_focus_operation',
      '此操作不能加入离线队列，请刷新后重试。',
    )
  }
  const items = loadFocusQueue(userId, item.session_id)
  if (items.length >= MAX_QUEUE_ITEMS) {
    throw new FocusQueueError(
      'focus_queue_full',
      '离线队列最多保留 100 项，请联网同步后继续。',
    )
  }
  if (items.some(existing => existing.operation_id === item.operation_id)) return
  writeQueue(userId, item.session_id, [...items, valid])
}

export function replaceFirstFocusOperation(
  userId: string,
  sessionId: string,
  item: QueuedFocusOperation,
) {
  const items = loadFocusQueue(userId, sessionId)
  if (!items.length || items[0].operation_id !== item.operation_id) {
    throw new FocusQueueError(
      'invalid_focus_operation',
      '待同步操作已经变化，请刷新后重试。',
    )
  }
  const valid = parseOperation(item, sessionId, Date.now())
  if (valid === null) {
    throw new FocusQueueError(
      'invalid_focus_operation',
      '此操作不能重新应用，请采用服务端状态。',
    )
  }
  writeQueue(userId, sessionId, [valid, ...items.slice(1)])
}

export function clearFocusQueue(userId: string, sessionId: string) {
  writeQueue(userId, sessionId, [])
}

export async function replayFocusQueue(
  userId: string,
  sessionId: string,
  send: SendFocusOperation,
): Promise<ReplayResult> {
  let pending = loadFocusQueue(userId, sessionId)
  let session: FocusSession | null = null
  while (pending.length) {
    const item = pending[0]
    try {
      session = await send(item)
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        return {
          session,
          pending,
          conflict: item,
          conflict_error: error.info,
        }
      }
      throw error
    }
    const latest = loadFocusQueue(userId, sessionId)
    if (latest[0]?.operation_id !== item.operation_id) {
      throw new FocusQueueError(
        'invalid_focus_operation',
        '待同步操作已经变化，请刷新后核对。',
      )
    }
    writeQueue(userId, sessionId, latest.slice(1))
    pending = loadFocusQueue(userId, sessionId)
  }
  return { session, pending, conflict: null, conflict_error: null }
}

function elapsedAt(focus: FocusSession, occurredAt: Date) {
  if (focus.status !== 'running' || focus.active_started_at === null) {
    return focus.accumulated_seconds
  }
  return focus.accumulated_seconds + Math.max(
    0,
    Math.floor(
      (occurredAt.getTime() - Date.parse(focus.active_started_at)) / 1000,
    ),
  )
}

function withTimerState(focus: FocusSession, elapsed: number): FocusSession {
  const remaining = focus.target_seconds === null
    ? null
    : focus.target_seconds - elapsed
  return {
    ...focus,
    elapsed_seconds: elapsed,
    remaining_seconds: remaining === null ? null : Math.max(0, remaining),
    overtime_seconds: remaining === null ? 0 : Math.max(0, -remaining),
    overtime: remaining !== null && remaining <= 0,
  }
}

export function applyQueuedFocusOperation(
  focus: FocusSession,
  item: QueuedFocusOperation,
): FocusSession {
  if (focus.id !== item.session_id || focus.version !== item.expected_version) {
    throw new FocusQueueError(
      'invalid_focus_operation',
      '待同步操作与当前专注版本不一致，请联网核对。',
    )
  }
  if (item.action === 'progress') {
    if (!['running', 'paused'].includes(focus.status) || focus.task_id === null) {
      throw new FocusQueueError(
        'invalid_focus_operation',
        '当前专注不能离线更新步骤。',
      )
    }
    const completed = new Set(item.payload.completed_step_ids as string[])
    const changed = focus.steps.some(step => step.completed !== completed.has(step.id))
    if (!changed) return focus
    return {
      ...focus,
      steps: focus.steps.map(step => ({ ...step, completed: completed.has(step.id) })),
      version: focus.version + 1,
      task_version: focus.occurrence_key === null
        ? (focus.task_version ?? 0) + 1
        : focus.task_version,
      occurrence_version: focus.occurrence_key === null
        ? focus.occurrence_version
        : (focus.occurrence_version ?? 0) + 1,
    }
  }

  const occurredAt = new Date(item.client_recorded_at)
  const elapsed = elapsedAt(focus, occurredAt)
  if (item.action === 'pause' && focus.status === 'running') {
    return withTimerState({
      ...focus,
      status: 'paused',
      active_started_at: null,
      accumulated_seconds: elapsed,
      version: focus.version + 1,
    }, elapsed)
  }
  if (item.action === 'resume' && focus.status === 'paused') {
    return withTimerState({
      ...focus,
      status: 'running',
      active_started_at: item.client_recorded_at,
      version: focus.version + 1,
    }, elapsed)
  }
  if (
    (item.action === 'finish' || item.action === 'abandon')
    && ['running', 'paused'].includes(focus.status)
  ) {
    return withTimerState({
      ...focus,
      status: item.action === 'finish' ? 'finished' : 'abandoned',
      ended_at: item.client_recorded_at,
      active_started_at: null,
      accumulated_seconds: elapsed,
      effective_started_at: focus.started_at,
      effective_ended_at: item.client_recorded_at,
      effective_duration_seconds: elapsed,
      version: focus.version + 1,
    }, elapsed)
  }
  throw new FocusQueueError(
    'invalid_focus_operation',
    '当前专注状态不允许此离线操作。',
  )
}
