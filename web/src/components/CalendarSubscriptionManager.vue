<script setup lang="ts">
import {
  computed,
  nextTick,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from 'vue'
import {
  CalendarClock,
  ChevronDown,
  CirclePause,
  CirclePlay,
  CloudDownload,
  History,
  Link,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Trash2,
} from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import { failure, localFailure, type Failure } from '../lib/api'
import {
  createCalendarSubscription,
  getCalendarSubscription,
  listCalendarSubscriptions,
  pauseCalendarSubscription,
  refreshCalendarSubscription,
  resumeCalendarSubscription,
  trashCalendarSubscription,
  updateCalendarSubscription,
  updateCalendarSubscriptionOverride,
  type CalendarSubscription,
  type CalendarSubscriptionDetail,
  type CalendarSubscriptionEvent,
  type CalendarSubscriptionOverrideUpdate,
  type SubscriptionEventFields,
  type SubscriptionOverrideField,
} from '../lib/calendar-subscriptions'
import { syncStatus } from '../lib/sync'

const props = defineProps<{
  active: boolean
  targetSourceId?: string | null
  targetEventId?: string | null
}>()

const emit = defineEmits<{
  changed: [message: string]
  'target-consumed': []
}>()

type PendingAction = 'create' | 'update' | 'refresh' | 'lifecycle' | 'delete' | 'override'
type RetryAction =
  | 'list'
  | 'detail'
  | 'create'
  | 'update'
  | 'refresh'
  | 'lifecycle'
  | 'delete'
  | 'override'
  | 'events'
  | null
type SourceConflictAction = 'update' | 'pause' | 'resume'
type Operation = { id: string; fingerprint: string }

interface SourceConflict {
  action: SourceConflictAction
  latest: CalendarSubscription | null
  readError: Failure | null
}

interface OverrideConflict {
  latest: CalendarSubscriptionEvent | null
  update: CalendarSubscriptionOverrideUpdate
  resetField: SubscriptionOverrideField | null
  readError: Failure | null
}

const overrideFieldOrder: SubscriptionOverrideField[] = [
  'name',
  'description',
  'location',
  'busy',
]
const overrideFieldLabels: Record<SubscriptionOverrideField, string> = {
  name: '名称',
  description: '说明',
  location: '地点',
  busy: '占用状态',
}
const refreshStatusLabels: Record<CalendarSubscription['refresh_status'], string> = {
  idle: '已就绪',
  queued: '排队中',
  running: '刷新中',
  error: '刷新失败',
}
const eventStatusLabels: Record<CalendarSubscriptionEvent['parse_status'], string> = {
  valid: '正常',
  stale_error: '保留旧版本',
  error: '解析失败',
}
const refreshRunLabels = {
  running: '进行中',
  succeeded: '已更新',
  not_modified: '无变化',
  failed: '失败',
  stale: '已被新设置取代',
} as const
const refreshTriggerLabels = {
  initial: '首次',
  manual: '手动',
  automatic: '自动',
} as const

const subscriptions = ref<CalendarSubscription[]>([])
const detail = ref<CalendarSubscriptionDetail | null>(null)
const selectedSourceId = ref('')
const selectedEvent = ref<CalendarSubscriptionEvent | null>(null)
const listLoading = ref(false)
const detailLoading = ref(false)
const eventsLoading = ref(false)
const pending = ref<PendingAction | null>(null)
const conflictLoading = ref(false)
const error = ref<Failure | null>(null)
const success = ref('')
const pollingNotice = ref('')
const retryAction = ref<RetryAction>(null)
const lastLifecycleAction = ref<'pause' | 'resume'>('pause')
const sourceConflict = ref<SourceConflict | null>(null)
const overrideConflict = ref<OverrideConflict | null>(null)
const editingEvent = ref(false)

const createValues = reactive({
  name: '',
  url: '',
  refreshIntervalMinutes: 360,
})
const editValues = reactive({
  name: '',
  url: '',
  refreshIntervalMinutes: 360,
})
const eventValues = reactive({
  name: '',
  description: '',
  location: '',
  busy: true,
})
const selectedOverrideFields = ref<SubscriptionOverrideField[]>([])
const eventEditorBase = ref<CalendarSubscriptionEvent | null>(null)

let createOperation: Operation | null = null
let updateOperation: Operation | null = null
let refreshOperation: Operation | null = null
let lifecycleOperation: Operation | null = null
let deleteOperation: Operation | null = null
let overrideOperation: Operation | null = null
let lastOverrideUpdate: CalendarSubscriptionOverrideUpdate | null = null
let lastOverrideResetField: SubscriptionOverrideField | null = null
let listRevision = 0
let detailRevision = 0
let activationRevision = 0
let pollTimer = 0
let pollStartedAt = 0
let pollDelay = 1000
let pollAttempts = 0
let pollRevision = 0
let pollInFlight = false
let pollGenerations = new Map<string, number>()
let consumedTargetKey = ''

const currentSubscription = computed(() => detail.value?.subscription ?? null)
const isRefreshing = computed(() => (
  subscriptions.value.some(item => ['queued', 'running'].includes(item.refresh_status))
))
const currentEvents = computed(() => detail.value?.events ?? [])
const hasEventDraftChanges = computed(() => {
  const base = eventEditorBase.value
  if (!base) return false
  if (!sameFields(selectedOverrideFields.value, base.override_fields)) return true
  return overrideFieldOrder.some(field => (
    selectedOverrideFields.value.includes(field)
    && eventDraftValue(field) !== normalizedEventValue(base, field)
  ))
})
const retryLabel = computed(() => {
  if (!retryAction.value || sourceConflict.value || overrideConflict.value) return undefined
  return ['list', 'detail', 'events'].includes(retryAction.value)
    ? '重新读取'
    : '重试操作'
})

function sameFields(left: SubscriptionOverrideField[], right: SubscriptionOverrideField[]) {
  return left.length === right.length && left.every(field => right.includes(field))
}

function operationId(current: Operation | null, fingerprint: string) {
  return current?.fingerprint === fingerprint
    ? current
    : { id: crypto.randomUUID(), fingerprint }
}

function clearFeedback() {
  error.value = null
  success.value = ''
  retryAction.value = null
}

function setFailure(reason: unknown, retry: RetryAction) {
  error.value = failure(reason)
  retryAction.value = retry
}

async function focusError() {
  await nextTick()
  document.querySelector<HTMLElement>('.subscription-manager .error-status')?.focus()
}

function readableTime(value: string | null | undefined) {
  if (!value) return '尚无'
  return new Date(value).toLocaleString('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function intervalLabel(minutes: number) {
  if (minutes % 1440 === 0) return `${minutes / 1440} 天`
  if (minutes % 60 === 0) return `${minutes / 60} 小时`
  return `${minutes} 分钟`
}

function sourceStatusText(subscription: CalendarSubscription) {
  if (subscription.lifecycle_status === 'paused') return '已暂停'
  return refreshStatusLabels[subscription.refresh_status]
}

function eventField(event: CalendarSubscriptionEvent, field: SubscriptionOverrideField) {
  return event.effective_fields[field]
}

function sourceField(event: CalendarSubscriptionEvent, field: SubscriptionOverrideField) {
  return event.source_fields[field]
}

function normalizedEventValue(
  event: CalendarSubscriptionEvent,
  field: SubscriptionOverrideField,
) {
  const value = eventField(event, field)
  return field === 'busy' ? value === true : typeof value === 'string' ? value : ''
}

function eventDraftValue(field: SubscriptionOverrideField) {
  if (field === 'busy') return eventValues.busy
  return eventValues[field]
}

function displayFieldValue(value: unknown, field: SubscriptionOverrideField) {
  if (field === 'busy') return value === true ? '占用时间' : '显示为空闲'
  if (typeof value !== 'string' || !value) return '未填写'
  return value
}

function eventName(event: CalendarSubscriptionEvent) {
  const value = eventField(event, 'name')
  return typeof value === 'string' && value ? value : '未命名来源事件'
}

function syncSubscription(next: CalendarSubscription) {
  const index = subscriptions.value.findIndex(item => item.id === next.id)
  if (index === -1) {
    subscriptions.value = [next, ...subscriptions.value]
  } else {
    subscriptions.value = subscriptions.value.map(item => (
      item.id === next.id ? { ...item, ...next } : item
    ))
  }
  if (detail.value?.subscription.id === next.id) {
    detail.value = {
      ...detail.value,
      subscription: { ...detail.value.subscription, ...next },
    }
  }
}

function replaceEvent(
  next: CalendarSubscriptionEvent,
  sourceId = selectedSourceId.value,
) {
  if (!detail.value || detail.value.subscription.id !== sourceId) return
  detail.value = {
    ...detail.value,
    events: detail.value.events.map(event => event.id === next.id ? next : event),
  }
  if (selectedEvent.value?.id === next.id) selectedEvent.value = next
}

function resetSourceEditor(subscription: CalendarSubscription) {
  editValues.name = subscription.name
  editValues.url = ''
  editValues.refreshIntervalMinutes = subscription.refresh_interval_minutes
  updateOperation = null
  deleteOperation = null
  sourceConflict.value = null
}

function resetEventEditor(event: CalendarSubscriptionEvent) {
  selectedEvent.value = event
  eventEditorBase.value = event
  eventValues.name = String(normalizedEventValue(event, 'name'))
  eventValues.description = String(normalizedEventValue(event, 'description'))
  eventValues.location = String(normalizedEventValue(event, 'location'))
  eventValues.busy = normalizedEventValue(event, 'busy') === true
  selectedOverrideFields.value = [...event.override_fields]
  overrideOperation = null
  overrideConflict.value = null
}

async function loadSubscriptions(silent = false) {
  const revision = ++listRevision
  if (!silent) listLoading.value = true
  try {
    const items = await listCalendarSubscriptions()
    if (revision !== listRevision) return false
    subscriptions.value = items
    const selected = items.find(item => item.id === selectedSourceId.value)
    if (selected && detail.value) syncSubscription(selected)
    if (!silent) {
      error.value = null
      retryAction.value = null
    }
    return true
  } catch (reason) {
    if (revision !== listRevision) return false
    if (!sourceConflict.value && !overrideConflict.value) setFailure(reason, 'list')
    return false
  } finally {
    if (revision === listRevision) listLoading.value = false
  }
}

async function fetchDetail(
  sourceId: string,
  targetEventId: string | null = null,
) {
  const firstLimit = targetEventId ? 200 : 25
  let result = await getCalendarSubscription(sourceId, null, firstLimit)
  let pageCount = 1
  while (
    targetEventId
    && !result.events.some(event => event.id === targetEventId)
    && result.next_event_cursor
    && pageCount < 25
  ) {
    const page = await getCalendarSubscription(
      sourceId,
      result.next_event_cursor,
      200,
    )
    result = {
      subscription: page.subscription,
      events: [...result.events, ...page.events],
      event_has_more: page.event_has_more,
      next_event_cursor: page.next_event_cursor,
      refreshes: page.refreshes,
    }
    pageCount += 1
  }
  return result
}

async function openSubscription(
  sourceId: string,
  targetEventId: string | null = null,
  preserveDrafts = false,
  silent = false,
) {
  const revision = ++detailRevision
  const sourceChanged = selectedSourceId.value !== sourceId
  selectedSourceId.value = sourceId
  if (sourceChanged) {
    selectedEvent.value = null
    editingEvent.value = false
    sourceConflict.value = null
    overrideConflict.value = null
  }
  if (!silent) detailLoading.value = true
  try {
    const result = await fetchDetail(sourceId, targetEventId)
    if (revision !== detailRevision) return false
    detail.value = result
    syncSubscription(result.subscription)
    if (!preserveDrafts || sourceChanged) resetSourceEditor(result.subscription)

    if (targetEventId) {
      const event = result.events.find(item => item.id === targetEventId)
      if (event) {
        if (
          !preserveDrafts
          || selectedEvent.value?.id !== event.id
          || !hasEventDraftChanges.value
        ) {
          resetEventEditor(event)
        } else {
          selectedEvent.value = event
        }
        editingEvent.value = true
      } else if (!silent) {
        error.value = localFailure('此订阅中未找到目标事件，可能已被来源移除。')
      }
    } else if (selectedEvent.value) {
      const latestEvent = result.events.find(item => item.id === selectedEvent.value?.id)
      if (latestEvent) selectedEvent.value = latestEvent
    }
    if (!silent) retryAction.value = null
    return true
  } catch (reason) {
    if (revision !== detailRevision) return false
    if (!sourceConflict.value && !overrideConflict.value) setFailure(reason, 'detail')
    return false
  } finally {
    if (revision === detailRevision) {
      detailLoading.value = false
      if (!pollInFlight) schedulePolling()
    }
  }
}

async function loadMoreEvents() {
  if (!detail.value?.next_event_cursor || eventsLoading.value) return
  const sourceId = detail.value.subscription.id
  const cursor = detail.value.next_event_cursor
  eventsLoading.value = true
  try {
    const page = await getCalendarSubscription(sourceId, cursor, 25)
    if (detail.value?.subscription.id !== sourceId) return
    const existing = new Set(detail.value.events.map(event => event.id))
    detail.value = {
      subscription: page.subscription,
      events: [
        ...detail.value.events,
        ...page.events.filter(event => !existing.has(event.id)),
      ],
      event_has_more: page.event_has_more,
      next_event_cursor: page.next_event_cursor,
      refreshes: page.refreshes,
    }
    syncSubscription(page.subscription)
    retryAction.value = null
  } catch (reason) {
    setFailure(reason, 'events')
    await focusError()
  } finally {
    eventsLoading.value = false
  }
}

function validateSubscriptionValues(name: string, interval: number, url?: string) {
  if (!name.trim()) return localFailure('请输入订阅名称。')
  if (Array.from(name.trim()).length > 255) {
    return localFailure('订阅名称不能超过 255 个字符。')
  }
  if (url !== undefined && !url.trim()) return localFailure('请输入 ICS 订阅地址。')
  if (!Number.isInteger(interval) || interval < 15 || interval > 10080) {
    return localFailure('刷新周期必须是 15 到 10080 之间的整数分钟。')
  }
  return null
}

async function createSubscription() {
  if (pending.value) return
  clearFeedback()
  const validation = validateSubscriptionValues(
    createValues.name,
    createValues.refreshIntervalMinutes,
    createValues.url,
  )
  if (validation) {
    error.value = validation
    await focusError()
    return
  }
  const draft = {
    name: createValues.name.trim(),
    url: createValues.url.trim(),
    refresh_interval_minutes: createValues.refreshIntervalMinutes,
  }
  const fingerprint = JSON.stringify(draft)
  createOperation = operationId(createOperation, fingerprint)
  pending.value = 'create'
  try {
    const created = await createCalendarSubscription(draft, createOperation.id)
    createOperation = null
    createValues.name = ''
    createValues.url = ''
    createValues.refreshIntervalMinutes = 360
    syncSubscription(created)
    await openSubscription(created.id)
    success.value = '订阅已创建，首次刷新已进入队列。'
    emit('changed', success.value)
    beginPolling()
  } catch (reason) {
    setFailure(reason, 'create')
    await focusError()
  } finally {
    pending.value = null
  }
}

async function saveSubscription(fromConflict = false) {
  const subscription = sourceConflict.value?.latest ?? currentSubscription.value
  if (!subscription || pending.value) return
  if (sourceConflict.value && !fromConflict) {
    error.value = localFailure('请先重读服务器最新版，再决定是否基于最新版重试。')
    return
  }
  clearFeedback()
  const validation = validateSubscriptionValues(
    editValues.name,
    editValues.refreshIntervalMinutes,
  )
  if (validation) {
    error.value = validation
    await focusError()
    return
  }
  const url = editValues.url.trim()
  const fields = {
    name: editValues.name.trim(),
    refresh_interval_minutes: editValues.refreshIntervalMinutes,
    ...(url ? { url } : {}),
  }
  const fingerprint = JSON.stringify({
    sourceId: subscription.id,
    version: subscription.version,
    fields,
  })
  updateOperation = operationId(updateOperation, fingerprint)
  pending.value = 'update'
  try {
    const updated = await updateCalendarSubscription(
      subscription,
      fields,
      updateOperation.id,
    )
    updateOperation = null
    sourceConflict.value = null
    syncSubscription(updated)
    if (selectedSourceId.value === updated.id) resetSourceEditor(updated)
    success.value = url
      ? '订阅设置与新地址已保存，正在重新刷新。'
      : '订阅设置已保存。'
    emit('changed', success.value)
    if (['queued', 'running'].includes(updated.refresh_status)) beginPolling()
  } catch (reason) {
    const currentError = failure(reason)
    error.value = currentError
    retryAction.value = 'update'
    if (currentError.code === 'calendar_subscription_version_conflict') {
      updateOperation = null
      await prepareSourceConflict('update')
    }
    await focusError()
  } finally {
    pending.value = null
  }
}

async function requestRefresh() {
  const subscription = currentSubscription.value
  if (!subscription || pending.value) return
  clearFeedback()
  const fingerprint = JSON.stringify({
    sourceId: subscription.id,
    action: 'refresh',
  })
  refreshOperation = operationId(refreshOperation, fingerprint)
  pending.value = 'refresh'
  try {
    const updated = await refreshCalendarSubscription(
      subscription.id,
      refreshOperation.id,
    )
    refreshOperation = null
    syncSubscription(updated)
    success.value = ['queued', 'running'].includes(subscription.refresh_status)
      ? '此订阅已经在刷新中。'
      : '已加入刷新队列，旧日历内容会保留到新结果成功写入。'
    beginPolling()
  } catch (reason) {
    setFailure(reason, 'refresh')
    await focusError()
  } finally {
    pending.value = null
  }
}

async function setLifecycle(
  action: 'pause' | 'resume',
  fromConflict = false,
) {
  const subscription = sourceConflict.value?.latest ?? currentSubscription.value
  if (!subscription || pending.value) return
  if (sourceConflict.value && !fromConflict) {
    error.value = localFailure('请先重读服务器最新版，再决定是否重试此操作。')
    return
  }
  clearFeedback()
  lastLifecycleAction.value = action
  const fingerprint = JSON.stringify({
    sourceId: subscription.id,
    version: subscription.version,
    action,
  })
  lifecycleOperation = operationId(lifecycleOperation, fingerprint)
  pending.value = 'lifecycle'
  try {
    const updated = action === 'pause'
      ? await pauseCalendarSubscription(subscription, lifecycleOperation.id)
      : await resumeCalendarSubscription(subscription, lifecycleOperation.id)
    lifecycleOperation = null
    sourceConflict.value = null
    syncSubscription(updated)
    success.value = action === 'pause'
      ? '订阅已暂停，现有日历内容仍会保留。'
      : '订阅已恢复，刷新已进入队列。'
    emit('changed', success.value)
    if (action === 'resume') beginPolling()
    else schedulePolling()
  } catch (reason) {
    const currentError = failure(reason)
    error.value = currentError
    retryAction.value = 'lifecycle'
    if (currentError.code === 'calendar_subscription_version_conflict') {
      lifecycleOperation = null
      await prepareSourceConflict(action)
    }
    await focusError()
  } finally {
    pending.value = null
  }
}

async function trashCurrentSubscription() {
  const subscription = currentSubscription.value
  if (!subscription || pending.value) return
  const pendingNote = syncStatus.pending_count
    ? `当前设备还有 ${syncStatus.pending_count} 项操作待同步。`
    : '当前设备没有待同步操作。'
  const refreshNote = ['queued', 'running'].includes(subscription.refresh_status)
    ? '当前刷新会停止，迟到结果不会保存。'
    : '当前没有进行中的刷新。'
  if (!window.confirm(
    `删除后订阅会立即从普通日历隐藏，并在回收站保留 30 天。\n${pendingNote}\n${refreshNote}`,
  )) return
  clearFeedback()
  const fingerprint = JSON.stringify({
    sourceId: subscription.id,
    version: subscription.version,
    action: 'delete',
  })
  deleteOperation = operationId(deleteOperation, fingerprint)
  pollRevision += 1
  listRevision += 1
  detailRevision += 1
  stopPolling()
  pending.value = 'delete'
  try {
    await trashCalendarSubscription(subscription, deleteOperation.id)
    deleteOperation = null
    subscriptions.value = subscriptions.value.filter(
      item => item.id !== subscription.id,
    )
    selectedSourceId.value = ''
    selectedEvent.value = null
    detail.value = null
    stopPolling()
    success.value = '订阅已移到回收站，将保留 30 天。已有计划未被改写。'
    emit('changed', success.value)
  } catch (reason) {
    const currentError = failure(reason)
    error.value = currentError
    retryAction.value = currentError.code === 'calendar_subscription_version_conflict'
      ? 'detail'
      : 'delete'
    await focusError()
  } finally {
    pending.value = null
  }
}

async function prepareSourceConflict(action: SourceConflictAction) {
  sourceConflict.value = { action, latest: null, readError: null }
  await rereadSourceConflict()
}

async function rereadSourceConflict() {
  const conflict = sourceConflict.value
  const sourceId = selectedSourceId.value
  if (!conflict || !sourceId || conflictLoading.value) return
  conflictLoading.value = true
  conflict.readError = null
  try {
    const latest = (await getCalendarSubscription(sourceId, null, 1)).subscription
    conflict.latest = latest
    syncSubscription(latest)
  } catch (reason) {
    conflict.readError = failure(reason)
  } finally {
    conflictLoading.value = false
  }
}

async function retrySourceConflict() {
  const conflict = sourceConflict.value
  if (!conflict?.latest) return
  if (conflict.action === 'update') await saveSubscription(true)
  else await setLifecycle(conflict.action, true)
}

function useLatestSubscription() {
  const latest = sourceConflict.value?.latest
  if (!latest) return
  resetSourceEditor(latest)
  error.value = null
  success.value = '已采用服务器最新版，当前草稿已清除。'
}

function selectEvent(event: CalendarSubscriptionEvent) {
  resetEventEditor(event)
  editingEvent.value = true
  error.value = null
  success.value = ''
}

function toggleOverrideField(field: SubscriptionOverrideField, checked: boolean) {
  selectedOverrideFields.value = checked
    ? [...new Set([...selectedOverrideFields.value, field])]
    : selectedOverrideFields.value.filter(item => item !== field)
}

function buildOverrideUpdate() {
  const event = selectedEvent.value
  if (!event) return null
  const values: SubscriptionEventFields = {}
  const resetFields: SubscriptionOverrideField[] = []
  for (const field of overrideFieldOrder) {
    const selected = selectedOverrideFields.value.includes(field)
    const wasOverridden = event.override_fields.includes(field)
    if (!selected && wasOverridden) {
      resetFields.push(field)
      continue
    }
    if (!selected) continue
    let value = eventDraftValue(field)
    if (typeof value === 'string') value = value.trim()
    if (field === 'name' && !value) {
      error.value = localFailure('事件名称不能为空。')
      return null
    }
    if (!wasOverridden || value !== normalizedEventValue(event, field)) {
      values[field] = value
    }
  }
  if (!Object.keys(values).length && !resetFields.length) {
    error.value = localFailure('事件字段没有需要保存的变化。')
    return null
  }
  return { values, reset_fields: resetFields }
}

async function saveEventOverrides() {
  if (!selectedEvent.value || pending.value) return
  if (overrideConflict.value) {
    error.value = localFailure('请先重读服务器最新版，再决定如何处理当前事件草稿。')
    return
  }
  clearFeedback()
  const update = buildOverrideUpdate()
  if (!update) {
    await focusError()
    return
  }
  await submitOverride(update, null)
}

async function resetEventField(field: SubscriptionOverrideField) {
  const event = selectedEvent.value
  if (!event || pending.value || !event.override_fields.includes(field)) return
  if (overrideConflict.value) {
    error.value = localFailure('请先处理版本冲突，再重置事件字段。')
    await focusError()
    return
  }
  clearFeedback()
  await submitOverride({ values: {}, reset_fields: [field] }, field)
}

async function submitOverride(
  update: CalendarSubscriptionOverrideUpdate,
  resetField: SubscriptionOverrideField | null,
  eventOverride: CalendarSubscriptionEvent | null = null,
) {
  const sourceId = selectedSourceId.value
  const event = eventOverride ?? selectedEvent.value
  if (!sourceId || !event || pending.value) return
  lastOverrideUpdate = {
    values: { ...update.values },
    reset_fields: [...update.reset_fields],
  }
  lastOverrideResetField = resetField
  const fingerprint = JSON.stringify({
    sourceId,
    eventId: event.id,
    overrideVersion: event.override_version,
    update,
  })
  overrideOperation = operationId(overrideOperation, fingerprint)
  pending.value = 'override'
  try {
    const updated = await updateCalendarSubscriptionOverride(
      sourceId,
      event,
      update,
      overrideOperation.id,
    )
    overrideOperation = null
    overrideConflict.value = null
    if (resetField) {
      replaceEvent(updated, sourceId)
      if (
        selectedSourceId.value !== sourceId
        || selectedEvent.value?.id !== updated.id
      ) {
        success.value = `${overrideFieldLabels[resetField]}已重置为来源值。`
        retryAction.value = null
        emit('changed', success.value)
        return
      }
      eventEditorBase.value = updated
      selectedOverrideFields.value = selectedOverrideFields.value.filter(
        field => field !== resetField,
      )
      if (resetField === 'busy') {
        eventValues.busy = normalizedEventValue(updated, resetField) === true
      } else {
        eventValues[resetField] = String(normalizedEventValue(updated, resetField))
      }
      success.value = `${overrideFieldLabels[resetField]}已重置为来源值。`
    } else {
      replaceEvent(updated, sourceId)
      if (
        selectedSourceId.value === sourceId
        && selectedEvent.value?.id === updated.id
      ) {
        resetEventEditor(updated)
      }
      success.value = '事件本地字段已保存。'
    }
    retryAction.value = null
    emit('changed', success.value)
  } catch (reason) {
    const currentError = failure(reason)
    error.value = currentError
    retryAction.value = 'override'
    if (currentError.code === 'calendar_subscription_override_version_conflict') {
      overrideOperation = null
      overrideConflict.value = {
        latest: null,
        update: lastOverrideUpdate,
        resetField: lastOverrideResetField,
        readError: null,
      }
      await rereadOverrideConflict()
    }
    await focusError()
  } finally {
    pending.value = null
  }
}

async function findLatestEvent(sourceId: string, eventId: string) {
  let page = await getCalendarSubscription(sourceId, null, 200)
  let count = 1
  while (!page.events.some(event => event.id === eventId) && page.next_event_cursor && count < 25) {
    page = await getCalendarSubscription(sourceId, page.next_event_cursor, 200)
    count += 1
  }
  return {
    event: page.events.find(item => item.id === eventId) ?? null,
    subscription: page.subscription,
  }
}

async function rereadOverrideConflict() {
  const conflict = overrideConflict.value
  const eventId = selectedEvent.value?.id
  const sourceId = selectedSourceId.value
  if (!conflict || !eventId || !sourceId || conflictLoading.value) return
  conflictLoading.value = true
  conflict.readError = null
  try {
    const result = await findLatestEvent(sourceId, eventId)
    if (!result.event) {
      conflict.readError = localFailure('服务器最新版中已找不到此事件。')
      return
    }
    conflict.latest = result.event
    syncSubscription(result.subscription)
    replaceEvent(result.event)
  } catch (reason) {
    conflict.readError = failure(reason)
  } finally {
    conflictLoading.value = false
  }
}

async function retryOverrideConflict() {
  const conflict = overrideConflict.value
  if (!conflict?.latest) return
  const resetField = conflict.resetField
  selectedEvent.value = conflict.latest
  const update = resetField
    ? {
      values: { ...conflict.update.values },
      reset_fields: [...conflict.update.reset_fields],
    }
    : buildOverrideUpdate()
  if (!update) return
  overrideConflict.value = null
  error.value = null
  await submitOverride(update, resetField, conflict.latest)
}

function useLatestEvent() {
  const latest = overrideConflict.value?.latest
  if (!latest) return
  resetEventEditor(latest)
  error.value = null
  success.value = '已采用服务器最新版，当前事件草稿已清除。'
}

function stopPolling() {
  window.clearTimeout(pollTimer)
  pollTimer = 0
}

function beginPolling() {
  stopPolling()
  pollRevision += 1
  pollStartedAt = Date.now()
  pollDelay = 1000
  pollAttempts = 0
  pollingNotice.value = ''
  pollGenerations = new Map(
    subscriptions.value.map(item => [item.id, item.generation]),
  )
  schedulePolling()
}

function schedulePolling(revision = pollRevision) {
  stopPolling()
  if (
    revision !== pollRevision
    || pollInFlight
    || !props.active
    || document.hidden
    || !isRefreshing.value
  ) return
  if (Date.now() - pollStartedAt >= 120_000 || pollAttempts >= 40) {
    pollingNotice.value = '自动刷新状态检查已暂停。后台刷新仍会继续，可手动重新读取状态。'
    return
  }
  pollTimer = window.setTimeout(() => {
    void pollSubscriptions(revision)
  }, pollDelay)
  pollDelay = Math.min(5000, pollDelay + 750)
}

async function pollSubscriptions(revision = pollRevision) {
  if (pollInFlight || revision !== pollRevision) return
  pollInFlight = true
  try {
    pollAttempts += 1
    const beforeBusy = new Set(
      subscriptions.value
        .filter(item => ['queued', 'running'].includes(item.refresh_status))
        .map(item => item.id),
    )
    const selectedId = selectedSourceId.value
    const loaded = await loadSubscriptions(true)
    if (!loaded || revision !== pollRevision) return
    const selectedSubscription = subscriptions.value.find(item => item.id === selectedId)
    const selectionUnchanged = selectedSourceId.value === selectedId
    const selectedFinished = selectionUnchanged
      && !!selectedId
      && beforeBusy.has(selectedId)
      && !!selectedSubscription
      && !['queued', 'running'].includes(selectedSubscription.refresh_status)
    if (selectedFinished) {
      await openSubscription(
        selectedId,
        selectedEvent.value?.id ?? null,
        true,
        true,
      )
    } else if (
      selectionUnchanged
      && selectedId
      && detail.value?.subscription.id === selectedId
    ) {
      try {
        const summary = await getCalendarSubscription(selectedId, null, 1)
        if (revision !== pollRevision) return
        if (detail.value?.subscription.id === selectedId) {
          detail.value = {
            ...detail.value,
            subscription: {
              ...detail.value.subscription,
              ...summary.subscription,
            },
            refreshes: summary.refreshes,
          }
          syncSubscription(summary.subscription)
        }
      } catch (reason) {
        if (!sourceConflict.value && !overrideConflict.value) {
          setFailure(reason, 'detail')
        }
      }
    }
    let completed = 0
    for (const subscription of subscriptions.value) {
      const previousGeneration = pollGenerations.get(subscription.id)
        ?? subscription.generation
      if (
        beforeBusy.has(subscription.id)
        && subscription.lifecycle_status === 'active'
        && subscription.refresh_status === 'idle'
        && subscription.generation >= previousGeneration
      ) {
        completed += 1
      }
      pollGenerations.set(subscription.id, subscription.generation)
    }
    if (completed) {
      error.value = null
      retryAction.value = null
      success.value = completed === 1
        ? '订阅刷新完成，日历状态已同步。'
        : `${completed} 个订阅刷新完成，日历状态已同步。`
      emit('changed', success.value)
    }
  } finally {
    pollInFlight = false
    if (revision === pollRevision) schedulePolling(revision)
  }
}

function visibilityChanged() {
  if (!props.active) return
  if (document.hidden) {
    stopPolling()
    return
  }
  if (isRefreshing.value) {
    beginPolling()
  }
}

async function consumeTarget() {
  const sourceId = props.targetSourceId
  if (!props.active || !sourceId) return
  const key = `${sourceId}:${props.targetEventId ?? ''}`
  if (consumedTargetKey === key) return
  consumedTargetKey = key
  await openSubscription(sourceId, props.targetEventId ?? null)
  emit('target-consumed')
}

async function activate() {
  const revision = ++activationRevision
  clearFeedback()
  const loaded = await loadSubscriptions()
  if (revision !== activationRevision || !props.active || !loaded) return
  if (props.targetSourceId) {
    await consumeTarget()
  } else {
    const sourceId = selectedSourceId.value || subscriptions.value[0]?.id
    if (sourceId) await openSubscription(sourceId)
  }
  if (isRefreshing.value) beginPolling()
}

async function retryFailure() {
  switch (retryAction.value) {
    case 'list':
      await activate()
      break
    case 'detail':
      if (selectedSourceId.value) await openSubscription(
        selectedSourceId.value,
        selectedEvent.value?.id ?? null,
        true,
      )
      break
    case 'events':
      await loadMoreEvents()
      break
    case 'create':
      await createSubscription()
      break
    case 'update':
      await saveSubscription()
      break
    case 'refresh':
      await requestRefresh()
      break
    case 'lifecycle':
      await setLifecycle(lastLifecycleAction.value)
      break
    case 'delete':
      await trashCurrentSubscription()
      break
    case 'override':
      if (lastOverrideUpdate) {
        await submitOverride(lastOverrideUpdate, lastOverrideResetField)
      }
      break
  }
}

watch(() => props.active, active => {
  if (!active) {
    activationRevision += 1
    pollRevision += 1
    stopPolling()
    return
  }
  void activate()
}, { immediate: true })

watch(
  () => [props.targetSourceId, props.targetEventId] as const,
  ([sourceId]) => {
    if (!sourceId) {
      consumedTargetKey = ''
      return
    }
    if (props.active) void consumeTarget()
  },
)

onMounted(() => {
  document.addEventListener('visibilitychange', visibilityChanged)
  window.addEventListener('focus', visibilityChanged)
})

onBeforeUnmount(() => {
  activationRevision += 1
  listRevision += 1
  detailRevision += 1
  pollRevision += 1
  stopPolling()
  document.removeEventListener('visibilitychange', visibilityChanged)
  window.removeEventListener('focus', visibilityChanged)
})
</script>

<template>
  <section class="subscription-manager" aria-labelledby="subscription-manager-title">
    <header class="subscription-heading">
      <div class="subscription-heading-copy">
        <CalendarClock :size="22" aria-hidden="true" />
        <div>
          <h2 id="subscription-manager-title">日历订阅</h2>
          <p>订阅只在服务端刷新，地址中的路径和参数不会回显。</p>
        </div>
      </div>
      <button type="button" class="small-icon-button subscription-reload-button"
        aria-label="重新读取日历订阅" title="重新读取"
        :disabled="listLoading || !!pending" @click="activate">
        <RefreshCw :size="17" aria-hidden="true" />
      </button>
    </header>

    <StatusMessage :error="error" :success="success" :retry-label="retryLabel"
      @retry="retryFailure" />
    <p v-if="pollingNotice" class="subscription-polling-notice" role="status">
      {{ pollingNotice }}
      <button type="button" class="text-button subscription-polling-retry"
        @click="beginPolling">继续检查</button>
    </p>

    <form class="subscription-create-form" novalidate @submit.prevent="createSubscription">
      <div class="subscription-section-heading">
        <div>
          <h3>添加 ICS URL</h3>
          <p>创建后立即排队首次刷新；需先在日历设置中保存时区。</p>
        </div>
        <Plus :size="19" aria-hidden="true" />
      </div>
      <div class="subscription-form-grid">
        <div class="field subscription-name-field">
          <label for="subscription-create-name">名称</label>
          <input id="subscription-create-name" v-model="createValues.name"
            maxlength="255" autocomplete="off" :disabled="!!pending" />
        </div>
        <div class="field subscription-url-field">
          <label for="subscription-create-url">ICS 订阅地址</label>
          <input id="subscription-create-url" v-model="createValues.url"
            type="url" maxlength="4096" inputmode="url" autocomplete="off"
            placeholder="https://example.com/calendar.ics"
            :disabled="!!pending" />
        </div>
        <div class="field subscription-interval-field">
          <label for="subscription-create-interval">刷新周期（分钟）</label>
          <input id="subscription-create-interval"
            v-model.number="createValues.refreshIntervalMinutes"
            type="number" min="15" max="10080" step="1" :disabled="!!pending" />
        </div>
      </div>
      <button type="submit" class="button subscription-create-button"
        :disabled="!!pending">
        <Plus :size="17" aria-hidden="true" />
        {{ pending === 'create' ? '正在创建…' : '创建订阅' }}
      </button>
    </form>

    <div class="subscription-workspace">
      <aside class="subscription-list-panel" aria-labelledby="subscription-list-title">
        <div class="subscription-section-heading">
          <div>
            <h3 id="subscription-list-title">订阅来源</h3>
            <p>最多显示当前账号的 100 个来源。</p>
          </div>
          <span class="subscription-count">{{ subscriptions.length }}</span>
        </div>
        <p v-if="listLoading && !subscriptions.length"
          class="subscription-loading" role="status">
          正在读取订阅…
        </p>
        <p v-else-if="!subscriptions.length" class="subscription-empty">
          还没有日历订阅。
        </p>
        <ol v-else class="subscription-list">
          <li v-for="subscription in subscriptions" :key="subscription.id">
            <button type="button" class="subscription-list-item"
              :class="{ selected: selectedSourceId === subscription.id }"
              :aria-pressed="selectedSourceId === subscription.id"
              :disabled="!!pending || detailLoading"
              @click="openSubscription(subscription.id)">
              <span class="subscription-list-identity">
                <strong>{{ subscription.name }}</strong>
                <small>{{ subscription.host }} · {{ intervalLabel(subscription.refresh_interval_minutes) }}</small>
              </span>
              <span class="state-label subscription-state-label"
                :data-state="subscription.lifecycle_status === 'paused'
                  ? 'paused'
                  : subscription.refresh_status">
                {{ sourceStatusText(subscription) }}
              </span>
              <small class="subscription-list-meta">
                {{ subscription.event_count ?? 0 }} 条事件
                <template v-if="subscription.error_event_count">
                  · {{ subscription.error_event_count }} 条异常
                </template>
              </small>
            </button>
          </li>
        </ol>
      </aside>

      <div class="subscription-detail-panel">
        <p v-if="detailLoading && !detail" class="subscription-loading" role="status">
          正在读取订阅详情…
        </p>
        <p v-else-if="!detail" class="subscription-empty">
          选择一个订阅以查看状态、刷新记录和来源事件。
        </p>
        <template v-else>
          <section class="subscription-summary" aria-labelledby="subscription-detail-title">
            <div class="subscription-detail-heading">
              <div>
                <h3 id="subscription-detail-title">{{ detail.subscription.name }}</h3>
                <p>
                  <Link :size="15" aria-hidden="true" />
                  {{ detail.subscription.url_display }}
                  <template v-if="detail.subscription.final_host
                    && detail.subscription.final_host !== detail.subscription.host">
                    · 最终来源 {{ detail.subscription.final_host }}
                  </template>
                </p>
              </div>
              <span class="state-label subscription-state-label"
                :data-state="detail.subscription.lifecycle_status === 'paused'
                  ? 'paused'
                  : detail.subscription.refresh_status">
                {{ sourceStatusText(detail.subscription) }}
              </span>
            </div>

            <dl class="subscription-metadata">
              <div>
                <dt>最近成功</dt>
                <dd>{{ readableTime(detail.subscription.last_success_at) }}</dd>
              </div>
              <div>
                <dt>最近尝试</dt>
                <dd>{{ readableTime(detail.subscription.last_attempt_at) }}</dd>
              </div>
              <div>
                <dt>下次刷新</dt>
                <dd>{{ detail.subscription.lifecycle_status === 'paused'
                  ? '暂停期间不刷新'
                  : readableTime(detail.subscription.next_refresh_at) }}</dd>
              </div>
              <div>
                <dt>默认时区</dt>
                <dd>{{ detail.subscription.default_time_zone }}</dd>
              </div>
            </dl>

            <div class="subscription-actions">
              <button type="button" class="button subscription-refresh-button"
                :disabled="!!pending || detail.subscription.lifecycle_status === 'paused'"
                @click="requestRefresh">
                <CloudDownload :size="17" aria-hidden="true" />
                {{ pending === 'refresh' ? '正在提交…' : '立即刷新' }}
              </button>
              <button v-if="detail.subscription.lifecycle_status === 'active'"
                type="button" class="button secondary subscription-pause-button"
                :disabled="!!pending" @click="setLifecycle('pause')">
                <CirclePause :size="17" aria-hidden="true" />
                {{ pending === 'lifecycle' ? '正在处理…' : '暂停' }}
              </button>
              <button v-else type="button"
                class="button secondary subscription-resume-button"
                :disabled="!!pending" @click="setLifecycle('resume')">
                <CirclePlay :size="17" aria-hidden="true" />
                {{ pending === 'lifecycle' ? '正在处理…' : '恢复' }}
              </button>
              <button type="button" class="button danger subscription-delete-button"
                :disabled="!!pending" @click="trashCurrentSubscription">
                <Trash2 :size="17" aria-hidden="true" />
                {{ pending === 'delete' ? '正在删除…' : '移到回收站' }}
              </button>
            </div>

            <div v-if="detail.subscription.refresh_status === 'error'"
              class="subscription-retained-error" role="status">
              <strong>最近刷新失败</strong>
              <p>{{ detail.subscription.last_error?.message ?? '来源暂时无法刷新。' }}</p>
              <p v-if="detail.subscription.last_success_at">
                上次成功刷新的 {{ detail.subscription.event_count ?? 0 }} 条事件仍保留在日历中，
                本次失败没有清空旧内容。
              </p>
              <p v-else>此来源尚无成功快照，日历中没有可保留的订阅事件。</p>
            </div>
            <p v-else-if="detail.subscription.error_event_count"
              class="subscription-retained-warning">
              最近快照有 {{ detail.subscription.error_event_count }} 条事件无法完整解析。
              已知事件会保留上次有效版本，不会因单项错误被误删。
            </p>
            <p v-if="detail.subscription.lifecycle_status === 'paused'"
              class="subscription-paused-note">
              暂停只停止后续刷新，当前日历内容和本地字段覆盖仍会保留。
            </p>
          </section>

          <form class="subscription-settings-form" novalidate
            @submit.prevent="saveSubscription()">
            <div class="subscription-section-heading">
              <div>
                <h3>订阅设置</h3>
                <p>新地址留空表示不修改；服务端不会返回原始私密 URL。</p>
              </div>
              <Pencil :size="18" aria-hidden="true" />
            </div>
            <div v-if="sourceConflict" class="subscription-conflict">
              <strong>服务器上的订阅已经变化，当前填写仍保留。</strong>
              <template v-if="sourceConflict.latest">
                <p>
                  服务器最新版：{{ sourceConflict.latest.name }} ·
                  {{ intervalLabel(sourceConflict.latest.refresh_interval_minutes) }} ·
                  版本 {{ sourceConflict.latest.version }}
                </p>
                <p>
                  当前草稿：{{ editValues.name || '未填写' }} ·
                  {{ intervalLabel(editValues.refreshIntervalMinutes) }}
                  <template v-if="editValues.url"> · 包含新 URL</template>
                </p>
              </template>
              <p v-if="sourceConflict.readError" class="subscription-conflict-error">
                {{ sourceConflict.readError.message }}
                <template v-if="sourceConflict.readError.request_id">
                  请求编号：{{ sourceConflict.readError.request_id }}
                </template>
              </p>
              <div class="subscription-conflict-actions">
                <button type="button" class="text-button subscription-conflict-read"
                  :disabled="conflictLoading" @click="rereadSourceConflict">
                  {{ conflictLoading ? '正在重读…' : '重读服务器版本' }}
                </button>
                <button type="button" class="text-button subscription-conflict-retry"
                  :disabled="conflictLoading || !sourceConflict.latest"
                  @click="retrySourceConflict">
                  基于最新版重试
                </button>
                <button type="button" class="text-button subscription-conflict-use-server"
                  :disabled="!sourceConflict.latest" @click="useLatestSubscription">
                  采用服务器设置
                </button>
              </div>
            </div>
            <div class="subscription-form-grid">
              <div class="field subscription-name-field">
                <label for="subscription-edit-name">名称</label>
                <input id="subscription-edit-name" v-model="editValues.name"
                  maxlength="255" :disabled="!!pending" />
              </div>
              <div class="field subscription-url-field">
                <label for="subscription-edit-url">可选新 URL</label>
                <input id="subscription-edit-url" v-model="editValues.url"
                  type="url" maxlength="4096" inputmode="url" autocomplete="off"
                  placeholder="留空则继续使用现有地址" :disabled="!!pending" />
              </div>
              <div class="field subscription-interval-field">
                <label for="subscription-edit-interval">刷新周期（分钟）</label>
                <input id="subscription-edit-interval"
                  v-model.number="editValues.refreshIntervalMinutes"
                  type="number" min="15" max="10080" step="1" :disabled="!!pending" />
              </div>
            </div>
            <div class="subscription-form-actions">
              <button type="button" class="button secondary subscription-settings-reset"
                :disabled="!!pending" @click="resetSourceEditor(detail.subscription)">
                <RotateCcw :size="17" aria-hidden="true" />恢复已保存设置
              </button>
              <button type="submit" class="button subscription-settings-save"
                :disabled="!!pending">
                <Save :size="17" aria-hidden="true" />
                {{ pending === 'update' ? '正在保存…' : '保存设置' }}
              </button>
            </div>
          </form>

          <section class="subscription-refresh-history"
            aria-labelledby="subscription-refresh-history-title">
            <div class="subscription-section-heading">
              <div>
                <h3 id="subscription-refresh-history-title">最近刷新</h3>
                <p>保留最近 10 次尝试的结果摘要，不保存日历正文或私密 URL。</p>
              </div>
              <History :size="18" aria-hidden="true" />
            </div>
            <p v-if="!detail.refreshes.length" class="subscription-empty">
              还没有已开始的刷新记录。
            </p>
            <ol v-else class="subscription-refresh-list">
              <li v-for="refresh in detail.refreshes" :key="refresh.id"
                class="subscription-refresh-item">
                <div class="subscription-refresh-heading">
                  <strong>
                    第 {{ refresh.attempt_number }} 次 ·
                    {{ refreshTriggerLabels[refresh.trigger] }}
                  </strong>
                  <span class="state-label subscription-refresh-state"
                    :data-state="refresh.status">
                    {{ refreshRunLabels[refresh.status] }}
                  </span>
                </div>
                <p>
                  {{ readableTime(refresh.started_at) }}
                  <template v-if="refresh.finished_at">
                    至 {{ readableTime(refresh.finished_at) }}
                  </template>
                  <template v-if="refresh.http_status">
                    · HTTP {{ refresh.http_status }}
                  </template>
                </p>
                <p v-if="['succeeded', 'not_modified'].includes(refresh.status)">
                  {{ refresh.event_count }} 条事件 · {{ refresh.changed_count }} 条变化 ·
                  {{ refresh.cancelled_count }} 条来源取消
                  <template v-if="refresh.error_count">
                    · {{ refresh.error_count }} 条项目错误
                  </template>
                </p>
                <p v-if="refresh.error" class="subscription-refresh-error">
                  {{ refresh.error.message }}
                </p>
                <ul v-if="refresh.item_errors.length"
                  class="subscription-refresh-item-errors">
                  <li v-for="(itemError, index) in refresh.item_errors.slice(0, 3)"
                    :key="`${itemError.code}-${index}`">
                    {{ itemError.message }}
                  </li>
                  <li v-if="refresh.item_errors.length > 3">
                    另有 {{ refresh.item_errors.length - 3 }} 条项目错误
                  </li>
                </ul>
              </li>
            </ol>
          </section>

          <section class="subscription-events" aria-labelledby="subscription-events-title">
            <div class="subscription-section-heading">
              <div>
                <h3 id="subscription-events-title">来源事件</h3>
                <p>
                  共 {{ detail.subscription.event_count ?? currentEvents.length }} 条；
                  本地覆盖优先于后续来源更新。
                </p>
              </div>
              <CalendarClock :size="18" aria-hidden="true" />
            </div>
            <p v-if="!currentEvents.length" class="subscription-empty">
              此订阅尚无事件；首次刷新成功后会显示在这里。
            </p>
            <ol v-else class="subscription-event-list">
              <li v-for="event in currentEvents" :key="event.id"
                class="subscription-event-item"
                :class="{ selected: selectedEvent?.id === event.id }">
                <button type="button" class="subscription-event-select"
                  :aria-expanded="selectedEvent?.id === event.id && editingEvent"
                  :disabled="!!pending"
                  @click="selectEvent(event)">
                  <span class="subscription-event-identity">
                    <strong>{{ eventName(event) }}</strong>
                    <small>
                      {{ event.recurrence_id ? '来源单次修订' : '来源主事件' }}
                    </small>
                  </span>
                  <span class="state-label subscription-event-state"
                    :data-state="event.parse_status">
                    {{ eventStatusLabels[event.parse_status] }}
                  </span>
                  <small class="subscription-event-meta">
                    <template v-if="event.source_status === 'cancelled'">来源已取消</template>
                    <template v-else-if="event.override_fields.length">
                      {{ event.override_fields.length }} 个本地覆盖
                    </template>
                    <template v-else>跟随来源</template>
                  </small>
                </button>
                <div v-if="selectedEvent?.id === event.id && editingEvent"
                  class="subscription-event-editor">
                  <p v-if="selectedEvent.error" class="subscription-event-error">
                    {{ selectedEvent.error.message }}
                    <template v-if="selectedEvent.parse_status === 'stale_error'">
                      上次有效版本仍保留。
                    </template>
                  </p>
                  <p v-if="selectedEvent.source_status === 'cancelled'"
                    class="subscription-event-cancelled">
                    来源已取消此事件；过去记录保留，未来实例不再占用时间。
                  </p>
                  <div v-if="selectedEvent.differences.length"
                    class="subscription-event-differences">
                    <strong>来源在本地覆盖后发生了变化</strong>
                    <dl>
                      <div v-for="difference in selectedEvent.differences"
                        :key="difference.field">
                        <dt>{{ overrideFieldLabels[difference.field] }}</dt>
                        <dd>
                          覆盖时来源：{{ displayFieldValue(
                            difference.source_at_override,
                            difference.field,
                          ) }}；
                          当前来源：{{ displayFieldValue(
                            difference.source_current,
                            difference.field,
                          ) }}；
                          本地值：{{ displayFieldValue(
                            difference.local_value,
                            difference.field,
                          ) }}
                        </dd>
                      </div>
                    </dl>
                  </div>
                  <div v-if="overrideConflict" class="subscription-conflict">
                    <strong>此事件覆盖已在其他页面变化，当前填写仍保留。</strong>
                    <p v-if="overrideConflict.latest">
                      服务器覆盖版本 {{ overrideConflict.latest.override_version }}，
                      当前有效名称为“{{ eventName(overrideConflict.latest) }}”。
                    </p>
                    <p v-if="overrideConflict.readError"
                      class="subscription-conflict-error">
                      {{ overrideConflict.readError.message }}
                      <template v-if="overrideConflict.readError.request_id">
                        请求编号：{{ overrideConflict.readError.request_id }}
                      </template>
                    </p>
                    <div class="subscription-conflict-actions">
                      <button type="button"
                        class="text-button subscription-conflict-read"
                        :disabled="conflictLoading" @click="rereadOverrideConflict">
                        {{ conflictLoading ? '正在重读…' : '重读服务器版本' }}
                      </button>
                      <button type="button"
                        class="text-button subscription-conflict-retry"
                        :disabled="conflictLoading || !overrideConflict.latest"
                        @click="retryOverrideConflict">
                        基于最新版重试
                      </button>
                      <button type="button"
                        class="text-button subscription-conflict-use-server"
                        :disabled="!overrideConflict.latest" @click="useLatestEvent">
                        采用服务器事件
                      </button>
                    </div>
                  </div>

                  <form class="subscription-event-form" novalidate
                    @submit.prevent="saveEventOverrides">
                    <div class="subscription-override-field subscription-override-name">
                      <div class="subscription-override-heading">
                        <label>
                          <input type="checkbox"
                            :checked="selectedOverrideFields.includes('name')"
                            :disabled="!!pending || selectedEvent.parse_status === 'error'"
                            @change="toggleOverrideField(
                              'name',
                              ($event.target as HTMLInputElement).checked,
                            )" />
                          本地覆盖名称
                        </label>
                        <button v-if="selectedEvent.override_fields.includes('name')"
                          type="button" class="text-button subscription-field-reset"
                          :disabled="!!pending" @click="resetEventField('name')">
                          重置为来源
                        </button>
                      </div>
                      <p>来源：{{ displayFieldValue(sourceField(selectedEvent, 'name'), 'name') }}</p>
                      <input v-model="eventValues.name" aria-label="事件本地名称"
                        maxlength="200"
                        :disabled="!!pending || !selectedOverrideFields.includes('name')" />
                    </div>

                    <div class="subscription-override-field subscription-override-location">
                      <div class="subscription-override-heading">
                        <label>
                          <input type="checkbox"
                            :checked="selectedOverrideFields.includes('location')"
                            :disabled="!!pending || selectedEvent.parse_status === 'error'"
                            @change="toggleOverrideField(
                              'location',
                              ($event.target as HTMLInputElement).checked,
                            )" />
                          本地覆盖地点
                        </label>
                        <button v-if="selectedEvent.override_fields.includes('location')"
                          type="button" class="text-button subscription-field-reset"
                          :disabled="!!pending" @click="resetEventField('location')">
                          重置为来源
                        </button>
                      </div>
                      <p>
                        来源：{{ displayFieldValue(
                          sourceField(selectedEvent, 'location'),
                          'location',
                        ) }}
                      </p>
                      <input v-model="eventValues.location" aria-label="事件本地地点"
                        maxlength="300"
                        :disabled="!!pending || !selectedOverrideFields.includes('location')" />
                    </div>

                    <div class="subscription-override-field subscription-override-busy">
                      <div class="subscription-override-heading">
                        <label>
                          <input type="checkbox"
                            :checked="selectedOverrideFields.includes('busy')"
                            :disabled="!!pending || selectedEvent.parse_status === 'error'"
                            @change="toggleOverrideField(
                              'busy',
                              ($event.target as HTMLInputElement).checked,
                            )" />
                          本地覆盖占用状态
                        </label>
                        <button v-if="selectedEvent.override_fields.includes('busy')"
                          type="button" class="text-button subscription-field-reset"
                          :disabled="!!pending" @click="resetEventField('busy')">
                          重置为来源
                        </button>
                      </div>
                      <p>
                        来源：{{ displayFieldValue(
                          sourceField(selectedEvent, 'busy'),
                          'busy',
                        ) }}
                      </p>
                      <label class="subscription-busy-value">
                        <input v-model="eventValues.busy" type="checkbox"
                          :disabled="!!pending || !selectedOverrideFields.includes('busy')" />
                        占用时间
                      </label>
                    </div>

                    <div class="subscription-override-field subscription-override-description">
                      <div class="subscription-override-heading">
                        <label>
                          <input type="checkbox"
                            :checked="selectedOverrideFields.includes('description')"
                            :disabled="!!pending || selectedEvent.parse_status === 'error'"
                            @change="toggleOverrideField(
                              'description',
                              ($event.target as HTMLInputElement).checked,
                            )" />
                          本地覆盖说明
                        </label>
                        <button v-if="selectedEvent.override_fields.includes('description')"
                          type="button" class="text-button subscription-field-reset"
                          :disabled="!!pending" @click="resetEventField('description')">
                          重置为来源
                        </button>
                      </div>
                      <p>
                        来源：{{ displayFieldValue(
                          sourceField(selectedEvent, 'description'),
                          'description',
                        ) }}
                      </p>
                      <textarea v-model="eventValues.description"
                        aria-label="事件本地说明" maxlength="20000" rows="3"
                        :disabled="!!pending
                          || !selectedOverrideFields.includes('description')"></textarea>
                    </div>

                    <div class="subscription-event-form-actions">
                      <button type="button"
                        class="button secondary subscription-event-cancel"
                        :disabled="!!pending" @click="editingEvent = false">
                        收起
                      </button>
                      <button type="submit" class="button subscription-event-save"
                        :disabled="!!pending || selectedEvent.parse_status === 'error'">
                        <Save :size="17" aria-hidden="true" />
                        {{ pending === 'override' ? '正在保存…' : '保存字段覆盖' }}
                      </button>
                    </div>
                  </form>
                </div>
              </li>
            </ol>
            <button v-if="detail.event_has_more" type="button"
              class="button secondary subscription-events-more"
              :disabled="eventsLoading" @click="loadMoreEvents">
              <ChevronDown :size="17" aria-hidden="true" />
              {{ eventsLoading ? '正在读取…' : '加载更多事件' }}
            </button>
          </section>
        </template>
      </div>
    </div>
  </section>
</template>
