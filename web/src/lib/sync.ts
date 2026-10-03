import { reactive, watch } from 'vue'
import { api, ApiError, failure, observeMutations, type Failure } from './api'
import { identity } from './auth'
import { focusQueueCount } from './focus-queue'
import {
  discardTaskOperation,
  loadTaskQueue,
  replayTaskQueue,
  type QueuedTaskOperation,
} from './sync-queue'

const POLL_INTERVAL_MS = 2_000
const MAX_EVENTS_PER_PULL = 1_000
const BACKOFF_MS = [2_000, 4_000, 8_000, 15_000]

export type SyncPhase =
  | 'offline'
  | 'connecting'
  | 'synced'
  | 'pending'
  | 'conflict'
  | 'error'

export interface SyncEvent {
  sequence: number
  entity_type: string
  entity_id: string
  action: string
  entity_version: number
  changed_at: string
}

interface SyncPage {
  events: SyncEvent[]
  next_cursor: string
  has_more: boolean
  reset_required: boolean
  reset_reason: 'initial' | 'cursor_expired' | 'cursor_ahead' | null
  server_time: string
}

export interface SyncStatus {
  phase: SyncPhase
  account_id: string | null
  last_success_at: string | null
  latest_change_at: string | null
  server_time: string | null
  cache_fallback_at: string | null
  reset_message: string
  pending_count: number
  conflict_count: number
  task_conflict: {
    operation: QueuedTaskOperation
    error: Failure | null
  } | null
  error: Failure | null
}

export const syncStatus = reactive<SyncStatus>({
  phase: navigator.onLine ? 'connecting' : 'offline',
  account_id: null,
  last_success_at: null,
  latest_change_at: null,
  server_time: null,
  cache_fallback_at: null,
  reset_message: '',
  pending_count: 0,
  conflict_count: 0,
  task_conflict: null,
  error: null,
})

let started = false
let inFlight: Promise<void> | null = null
let timer = 0
let failureCount = 0
let taskConflict = false
let stopIdentityWatch: (() => void) | null = null
let stopMutationObserver: (() => void) | null = null
const channel = typeof BroadcastChannel === 'undefined'
  ? null
  : new BroadcastChannel('itime-sync')

function cursorKey(accountId: string) {
  return `itime-sync-cursor:${accountId}`
}

function currentCursor() {
  return syncStatus.account_id === null
    ? null
    : sessionStorage.getItem(cursorKey(syncStatus.account_id))
}

function saveCursor(cursor: string) {
  if (syncStatus.account_id === null) return
  sessionStorage.setItem(cursorKey(syncStatus.account_id), cursor)
}

function clearTimer() {
  if (timer) window.clearTimeout(timer)
  timer = 0
}

function schedule(delay = POLL_INTERVAL_MS) {
  clearTimer()
  if (
    !started
    || syncStatus.account_id === null
    || !navigator.onLine
    || document.visibilityState !== 'visible'
  ) return
  timer = window.setTimeout(() => requestSync(), delay)
}

function dispatchEvents(events: SyncEvent[], resetRequired: boolean) {
  if (!events.length && !resetRequired) return
  window.dispatchEvent(new CustomEvent('itime:sync-events', {
    detail: { events, reset_required: resetRequired },
  }))
}

function resetState(accountId: string | null) {
  clearTimer()
  syncStatus.account_id = accountId
  syncStatus.last_success_at = null
  syncStatus.latest_change_at = null
  syncStatus.server_time = null
  syncStatus.cache_fallback_at = null
  syncStatus.reset_message = ''
  syncStatus.error = null
  taskConflict = false
  syncStatus.pending_count = accountId === null
    ? 0
    : loadTaskQueue(accountId).length + focusQueueCount(accountId)
  syncStatus.conflict_count = 0
  syncStatus.task_conflict = null
  failureCount = 0
  syncStatus.phase = navigator.onLine ? 'connecting' : 'offline'
}

async function pullChanges() {
  const accountId = syncStatus.account_id
  if (accountId === null || !navigator.onLine) {
    syncStatus.phase = 'offline'
    return
  }
  syncStatus.phase = 'connecting'
  let cursor = currentCursor()
  let recoveredInvalidCursor = false
  const collected: SyncEvent[] = []
  let resetRequired = false
  try {
    const replay = await replayTaskQueue(accountId)
    taskConflict = replay.conflict !== null
    syncStatus.task_conflict = replay.conflict === null
      ? null
      : {
          operation: replay.conflict,
          error: replay.conflict_error,
        }
    syncStatus.pending_count = replay.pending.length + focusQueueCount(accountId)
    syncStatus.conflict_count = taskConflict ? 1 : 0
    while (collected.length < MAX_EVENTS_PER_PULL) {
      const query = new URLSearchParams({ limit: '100' })
      if (cursor !== null) query.set('cursor', cursor)
      let page: SyncPage
      try {
        page = await api<SyncPage>(`/sync/changes?${query}`)
      } catch (error) {
        if (
          error instanceof ApiError
          && error.info.code === 'invalid_sync_cursor'
          && !recoveredInvalidCursor
        ) {
          sessionStorage.removeItem(cursorKey(accountId))
          cursor = null
          recoveredInvalidCursor = true
          continue
        }
        throw error
      }
      if (syncStatus.account_id !== accountId) return
      cursor = page.next_cursor
      saveCursor(cursor)
      syncStatus.server_time = page.server_time
      collected.push(...page.events)
      if (page.reset_required) {
        resetRequired = true
        syncStatus.reset_message = (
          recoveredInvalidCursor
            ? '同步基线已重新建立。'
            : page.reset_reason === 'cursor_expired'
              ? '同步记录已到期，当前页面已重新核对。'
              : page.reset_reason === 'cursor_ahead'
                ? '服务数据已恢复，当前页面已重新核对。'
                : ''
        )
      }
      if (!page.has_more || page.reset_required) break
    }
    for (const event of collected) {
      if (
        syncStatus.latest_change_at === null
        || Date.parse(event.changed_at) > Date.parse(syncStatus.latest_change_at)
      ) syncStatus.latest_change_at = event.changed_at
    }
    syncStatus.last_success_at = new Date().toISOString()
    syncStatus.error = null
    failureCount = 0
    syncStatus.phase = syncStatus.conflict_count > 0
      ? 'conflict'
      : syncStatus.pending_count > 0
        ? 'pending'
        : 'synced'
    dispatchEvents(collected, resetRequired)
    if (collected.length) channel?.postMessage({ type: 'events' })
    schedule()
  } catch (error) {
    if (syncStatus.account_id !== accountId) return
    syncStatus.error = failure(error)
    syncStatus.phase = navigator.onLine ? 'error' : 'offline'
    const delay = BACKOFF_MS[Math.min(failureCount, BACKOFF_MS.length - 1)]
    failureCount += 1
    schedule(delay)
  }
}

export function requestSync() {
  if (!started || syncStatus.account_id === null) return
  if (!navigator.onLine) {
    syncStatus.phase = 'offline'
    clearTimer()
    return
  }
  if (inFlight !== null) return
  inFlight = pullChanges().finally(() => {
    inFlight = null
  })
}

export function discardTaskConflict() {
  const accountId = syncStatus.account_id
  const conflict = syncStatus.task_conflict
  if (accountId === null || conflict === null) return
  discardTaskOperation(accountId, conflict.operation.operation_id)
  taskConflict = false
  syncStatus.task_conflict = null
  syncStatus.conflict_count = 0
  requestSync()
}

function handleOnline() {
  syncStatus.phase = 'connecting'
  requestSync()
}

function handleOffline() {
  clearTimer()
  syncStatus.phase = 'offline'
}

function handleVisible() {
  if (document.visibilityState === 'visible') requestSync()
  else clearTimer()
}

function handleCacheFallback(event: Event) {
  const detail = (event as CustomEvent<{ cached_at?: string }>).detail
  syncStatus.cache_fallback_at = detail?.cached_at ?? new Date().toISOString()
}

function handleBroadcast() {
  requestSync()
}

function handleQueueChanged() {
  const accountId = syncStatus.account_id
  if (accountId === null) return
  syncStatus.pending_count = loadTaskQueue(accountId).length
    + focusQueueCount(accountId)
  syncStatus.conflict_count = taskConflict ? 1 : 0
  if (syncStatus.phase !== 'offline' && syncStatus.phase !== 'connecting') {
    syncStatus.phase = syncStatus.conflict_count
      ? 'conflict'
      : syncStatus.pending_count
        ? 'pending'
        : 'synced'
  }
  if (navigator.onLine) requestSync()
}

export function startSyncCoordinator() {
  if (started) return
  started = true
  stopIdentityWatch = watch(identity, session => {
    resetState(session?.user.id ?? null)
    if (session) requestSync()
  }, { immediate: true })
  stopMutationObserver = observeMutations(() => {
    channel?.postMessage({ type: 'mutation' })
    requestSync()
  })
  window.addEventListener('online', handleOnline)
  window.addEventListener('offline', handleOffline)
  window.addEventListener('focus', handleOnline)
  window.addEventListener('itime:cache-fallback', handleCacheFallback)
  window.addEventListener('itime:sync-queue-changed', handleQueueChanged)
  document.addEventListener('visibilitychange', handleVisible)
  channel?.addEventListener('message', handleBroadcast)
}

export function stopSyncCoordinator() {
  if (!started) return
  started = false
  clearTimer()
  stopIdentityWatch?.()
  stopIdentityWatch = null
  stopMutationObserver?.()
  stopMutationObserver = null
  window.removeEventListener('online', handleOnline)
  window.removeEventListener('offline', handleOffline)
  window.removeEventListener('focus', handleOnline)
  window.removeEventListener('itime:cache-fallback', handleCacheFallback)
  window.removeEventListener('itime:sync-queue-changed', handleQueueChanged)
  document.removeEventListener('visibilitychange', handleVisible)
  channel?.removeEventListener('message', handleBroadcast)
}
