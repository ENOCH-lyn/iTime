<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import {
  AlertTriangle,
  ArrowLeft,
  CalendarPlus,
  CalendarClock,
  CheckCircle2,
  CircleSlash2,
  ListChecks,
  MapPin,
  Pencil,
  RotateCcw,
  SlidersHorizontal,
  Trash2,
} from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import { ApiError, failure, localFailure, type Failure } from '../lib/api'
import { identity } from '../lib/auth'
import {
  createCalendarAddition,
  resetSubscriptionOccurrenceException,
  saveOccurrenceException,
  saveSubscriptionOccurrenceException,
  trashCalendarSeries,
  updateCalendarAddition,
  type CalendarDisplayItem,
  type CalendarOccurrence,
  type SubscriptionExceptionFields,
} from '../lib/calendar'
import {
  createTaskMakeup,
  rescheduleTaskOccurrence,
  syncTaskOccurrenceStepCompletion,
  updateTaskOccurrenceStatus,
} from '../lib/task-schedules'
import { appendTaskOperation } from '../lib/sync-queue'
import { getTask, type TaskDetail, type TaskStep } from '../lib/tasks'

const props = defineProps<{
  item: CalendarDisplayItem | null
  timeZone: string
  recurring: boolean
  seriesBusy: boolean
  success: string
}>()

const emit = defineEmits<{
  close: []
  changed: [message: string, conflictCount: number]
  editSeries: [occurrence: CalendarOccurrence]
  splitSeries: [occurrence: CalendarOccurrence]
  openSubscription: [sourceId: string, eventId: string]
}>()

type Action = 'none' | 'move' | 'override' | 'makeup' | 'task-move' | 'task-makeup'
type OccurrenceExceptionFields = SubscriptionExceptionFields & {
  moved_end_day_offset: 0 | 1 | null
}

const action = ref<Action>('none')
const pending = ref(false)
const error = ref<Failure | null>(null)
const operation = ref<{ id: string; fingerprint: string } | null>(null)
const candidateOffsets = ref<number[]>([])
const selectedOffset = ref<number | null>(null)
const move = reactive({
  date: '',
  endDate: '',
  startsAt: '',
  endsAt: '',
  endDayOffset: 0 as 0 | 1,
})
const override = reactive({ name: '', description: '', location: '', busy: true })
const makeup = reactive({ date: '', startsAt: '', endsAt: '', endDayOffset: 0 as 0 | 1 })
const taskMove = reactive({ date: '', time: '' })
const taskMakeup = reactive({ date: '', time: '' })
const taskDetail = ref<TaskDetail | null>(null)
const localCompletedStepIds = ref<string[]>([])
let taskDetailRevision = 0

const occurrence = computed(() => props.item?.calendar ?? null)
const task = computed(() => props.item?.task ?? null)
const plan = computed(() => props.item?.plan ?? null)
const canSplit = computed(() => (
  occurrence.value?.occurrence_type === 'base'
  && occurrence.value.source_type !== 'subscription'
  && props.recurring
))
const canCreateTaskMakeup = computed(() => (
  task.value?.kind === 'base'
  && taskDetail.value?.schedule.recurrence.frequency !== 'none'
))

watch(() => props.item?.id, () => {
  action.value = 'none'
  error.value = null
  operation.value = null
  candidateOffsets.value = []
  selectedOffset.value = null
  localCompletedStepIds.value = [...(task.value?.completed_step_ids ?? [])]
  const revision = ++taskDetailRevision
  if (!task.value) {
    taskDetail.value = null
    return
  }
  void getTask(task.value.task_id)
    .then(detail => {
      if (revision === taskDetailRevision) taskDetail.value = detail
    })
    .catch(reason => {
      if (revision === taskDetailRevision) error.value = failure(reason)
    })
}, { immediate: true })

function dateTime(value: string | null) {
  if (!value) return '时间无效'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: props.timeZone,
    month: 'long',
    day: 'numeric',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function localTime(value: string | null) {
  return value?.slice(0, 5) ?? ''
}

function statusLabel(state: CalendarDisplayItem['state']) {
  if (state === 'scheduled' || state === 'open') return '进行中'
  if (state === 'completed') return '已完成'
  if (state === 'cancelled') return '已取消'
  if (state === 'source_cancelled') return '来源已取消'
  return '已跳过'
}

function inclusiveEndDate(value: string) {
  const [year, month, day] = value.split('-').map(Number)
  const result = new Date(Date.UTC(year, month - 1, day))
  result.setUTCDate(result.getUTCDate() - 1)
  return result.toISOString().slice(0, 10)
}

function shiftDate(value: string, days: number) {
  const [year, month, day] = value.split('-').map(Number)
  const result = new Date(Date.UTC(year, month - 1, day + days))
  return result.toISOString().slice(0, 10)
}

function allDayLabel(value: CalendarOccurrence) {
  const inclusiveEnd = inclusiveEndDate(value.local_end_date)
  return inclusiveEnd > value.local_start_date
    ? `${value.local_start_date} 至 ${inclusiveEnd} 全天`
    : `${value.local_start_date} 全天`
}

function offsetLabel(minutes: number) {
  const sign = minutes >= 0 ? '+' : '-'
  const absolute = Math.abs(minutes)
  return `UTC${sign}${String(Math.floor(absolute / 60)).padStart(2, '0')}:${String(absolute % 60).padStart(2, '0')}`
}

function captureAmbiguousOffset(reason: unknown) {
  error.value = failure(reason)
  candidateOffsets.value = error.value.details?.candidate_offsets_minutes ?? []
}

function begin(next: Action) {
  const current = occurrence.value
  if (next === 'move' && current) {
    move.date = current.local_start_date
    move.endDate = current.local_end_date
    move.startsAt = localTime(props.item?.time ?? current.local_start_time)
    move.endsAt = localTime(props.item?.end_time ?? current.local_end_time)
    move.endDayOffset = current.local_end_date === current.local_start_date ? 0 : 1
  }
  if (next === 'override' && current) {
    override.name = current.name
    override.description = current.description
    override.location = current.location
    override.busy = current.busy
  }
  if (next === 'makeup' && current) {
    makeup.date = props.item?.date ?? current.local_start_date
    makeup.startsAt = localTime(props.item?.time ?? current.local_start_time)
    makeup.endsAt = localTime(props.item?.end_time ?? current.local_end_time)
    makeup.endDayOffset = props.item?.end_date === props.item?.date ? 0 : 1
  }
  if (next === 'task-move' && task.value) {
    taskMove.date = task.value.due_date
    taskMove.time = task.value.due_time?.slice(0, 5) ?? ''
  }
  if (next === 'task-makeup' && task.value) {
    taskMakeup.date = task.value.due_date
    taskMakeup.time = task.value.due_time?.slice(0, 5) ?? ''
  }
  action.value = next
  error.value = null
  candidateOffsets.value = []
  selectedOffset.value = null
}

function operationId(fingerprint: string) {
  if (!operation.value || operation.value.fingerprint !== fingerprint) {
    operation.value = { id: crypto.randomUUID(), fingerprint }
  }
  return operation.value.id
}

async function runCalendarAction(
  message: string,
  fields: OccurrenceExceptionFields,
) {
  if (!occurrence.value || pending.value) return
  const fingerprint = JSON.stringify({
    occurrence: occurrence.value.occurrence_key,
    version: occurrence.value.source_type === 'subscription'
      ? occurrence.value.source_version
      : occurrence.value.series_version,
    fields,
  })
  pending.value = true
  error.value = null
  try {
    const result = occurrence.value.source_type === 'subscription'
      ? await saveSubscriptionOccurrenceException(
          occurrence.value,
          {
            action: fields.action,
            moved_all_day: fields.moved_all_day,
            moved_start_date: fields.moved_start_date,
            moved_end_date: fields.moved_end_date,
            moved_starts_at: fields.moved_starts_at,
            moved_ends_at: fields.moved_ends_at,
            moved_time_zone: fields.moved_time_zone,
            moved_ambiguous_utc_offset_minutes:
              fields.moved_ambiguous_utc_offset_minutes,
            override_name: fields.override_name,
            override_description: fields.override_description,
            override_location: fields.override_location,
            override_busy: fields.override_busy,
          },
          operationId(fingerprint),
        )
      : await saveOccurrenceException(
          occurrence.value,
          {
            action: fields.action,
            moved_start_date: fields.moved_start_date,
            moved_starts_at: fields.moved_starts_at,
            moved_ends_at: fields.moved_ends_at,
            moved_end_day_offset: fields.moved_end_day_offset,
            moved_time_zone: fields.moved_time_zone,
            moved_ambiguous_utc_offset_minutes:
              fields.moved_ambiguous_utc_offset_minutes,
            override_name: fields.override_name,
            override_description: fields.override_description,
            override_location: fields.override_location,
            override_busy: fields.override_busy,
          },
          operationId(fingerprint),
        )
    operation.value = null
    action.value = 'none'
    emit('changed', message, result.conflicts.items.length)
  } catch (reason) {
    captureAmbiguousOffset(reason)
    await nextTick()
    document.querySelector<HTMLElement>('.occurrence-panel .error-status')?.focus()
  } finally {
    pending.value = false
  }
}

function cancelOccurrence() {
  void runCalendarAction(
    occurrence.value?.source_type === 'subscription'
      ? '本次订阅安排已取消，其他实例和来源均未改变。'
      : '本次安排已取消，其他实例未受影响。',
    {
      action: 'cancelled',
      moved_all_day: null,
      moved_start_date: null,
      moved_end_date: null,
      moved_starts_at: null,
      moved_ends_at: null,
      moved_end_day_offset: null,
      moved_time_zone: null,
      moved_ambiguous_utc_offset_minutes: null,
      override_name: null,
      override_description: null,
      override_location: null,
      override_busy: null,
    },
  )
}

function restoreOccurrence() {
  if (!occurrence.value) return
  void runCalendarAction('本次安排已恢复。', {
    action: 'overridden',
    moved_all_day: null,
    moved_start_date: null,
    moved_end_date: null,
    moved_starts_at: null,
    moved_ends_at: null,
    moved_end_day_offset: null,
    moved_time_zone: null,
    moved_ambiguous_utc_offset_minutes: null,
    override_name: occurrence.value.name,
    override_description: null,
    override_location: null,
    override_busy: props.seriesBusy,
  })
}

async function resetSubscriptionOccurrence() {
  const current = occurrence.value
  if (!current || pending.value) return
  const fingerprint = JSON.stringify({
    exception: current.exception_id,
    sourceVersion: current.source_version,
    exceptionVersion: current.exception_version,
    action: 'reset',
  })
  pending.value = true
  error.value = null
  try {
    const result = await resetSubscriptionOccurrenceException(
      current,
      operationId(fingerprint),
    )
    operation.value = null
    action.value = 'none'
    emit(
      'changed',
      '本次订阅安排已恢复为当前来源内容。',
      result.conflicts.items.length,
    )
  } catch (reason) {
    error.value = failure(reason)
    await nextTick()
    document.querySelector<HTMLElement>('.occurrence-panel .error-status')?.focus()
  } finally {
    pending.value = false
  }
}

async function trashCurrentSeries() {
  const current = occurrence.value
  if (
    !current?.series_id
    || current.series_version === null
    || pending.value
    || !window.confirm(
      '删除后安排会立即从普通日历隐藏，并在回收站保留 30 天。已有计划和复盘历史不会改写。',
    )
  ) return
  error.value = null
  const fingerprint = JSON.stringify({
    action: 'trash-series',
    seriesId: current.series_id,
    version: current.series_version,
  })
  pending.value = true
  try {
    await trashCalendarSeries(
      {
        id: current.series_id,
        version: current.series_version,
      },
      operationId(fingerprint),
    )
    operation.value = null
    emit('close')
    emit(
      'changed',
      '安排已移到回收站，将保留 30 天。已有计划未被改写。',
      0,
    )
  } catch (reason) {
    error.value = failure(reason)
    await nextTick()
    document.querySelector<HTMLElement>('.occurrence-panel .error-status')?.focus()
  } finally {
    pending.value = false
  }
}

function saveMove() {
  const current = occurrence.value
  if (!current || !move.date || !move.endDate) {
    error.value = localFailure('请填写完整的调整日期。')
    return
  }
  if (!current.all_day && (!move.startsAt || !move.endsAt)) {
    error.value = localFailure('请填写完整的调整日期与时间。')
    return
  }
  void runCalendarAction('本次安排已调课。', {
    action: 'moved',
    moved_all_day: current.source_type === 'subscription'
      ? Boolean(current.all_day)
      : null,
    moved_start_date: move.date,
    moved_end_date: current.source_type === 'subscription'
      ? (
          current.all_day
            ? move.endDate
            : shiftDate(move.date, move.endDayOffset)
        )
      : null,
    moved_starts_at: current.all_day ? null : move.startsAt,
    moved_ends_at: current.all_day ? null : move.endsAt,
    moved_end_day_offset: move.endDayOffset,
    moved_time_zone: props.timeZone,
    moved_ambiguous_utc_offset_minutes: selectedOffset.value,
    override_name: null,
    override_description: null,
    override_location: null,
    override_busy: null,
  })
}

function saveOverride() {
  if (!override.name.trim()) {
    error.value = localFailure('本次名称不能为空。')
    return
  }
  void runCalendarAction('本次内容覆盖已保存。', {
    action: 'overridden',
    moved_all_day: null,
    moved_start_date: null,
    moved_end_date: null,
    moved_starts_at: null,
    moved_ends_at: null,
    moved_end_day_offset: null,
    moved_time_zone: null,
    moved_ambiguous_utc_offset_minutes: null,
    override_name: override.name.trim(),
    override_description: override.description,
    override_location: override.location,
    override_busy: override.busy,
  })
}

async function saveMakeup() {
  if (!occurrence.value || pending.value) return
  if (!makeup.date || !makeup.startsAt || !makeup.endsAt) {
    error.value = localFailure('请填写完整的补课日期与时间。')
    return
  }
  const fields = {
    local_start_date: makeup.date,
    starts_at: makeup.startsAt,
    ends_at: makeup.endsAt,
    end_day_offset: makeup.endDayOffset,
    time_zone: props.timeZone,
    ambiguous_utc_offset_minutes: selectedOffset.value,
    override_name: null,
    override_description: null,
    override_location: null,
    override_busy: null,
  } as const
  const fingerprint = JSON.stringify({
    occurrence: occurrence.value.occurrence_key,
    version: occurrence.value.series_version,
    fields,
  })
  pending.value = true
  error.value = null
  try {
    const result = await createCalendarAddition(
      occurrence.value,
      fields,
      operationId(fingerprint),
    )
    operation.value = null
    action.value = 'none'
    emit('changed', '补课已关联到本次原安排。', result.conflicts.items.length)
  } catch (reason) {
    captureAmbiguousOffset(reason)
  } finally {
    pending.value = false
  }
}

async function toggleAddition() {
  if (!occurrence.value || pending.value) return
  const status = occurrence.value.status === 'cancelled' ? 'scheduled' : 'cancelled'
  const fingerprint = JSON.stringify({
    addition: occurrence.value.addition_id,
    version: occurrence.value.series_version,
    status,
  })
  pending.value = true
  error.value = null
  try {
    const result = await updateCalendarAddition(
      occurrence.value,
      status,
      operationId(fingerprint),
    )
    operation.value = null
    emit('changed', status === 'cancelled' ? '本次补课已取消。' : '本次补课已恢复。', result.conflicts.items.length)
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    pending.value = false
  }
}

async function setTaskStatus(status: 'open' | 'completed' | 'skipped') {
  if (!task.value || pending.value) return
  const fingerprint = JSON.stringify({
    task: task.value.task_id,
    key: task.value.occurrence_key,
    version: task.value.task_version,
    occurrenceVersion: task.value.occurrence_version,
    status,
  })
  pending.value = true
  error.value = null
  try {
    await updateTaskOccurrenceStatus(
      task.value,
      operationId(fingerprint),
      status,
      task.value.completed_step_ids,
    )
    operation.value = null
    emit('changed', status === 'completed' ? '任务实例已完成。' : status === 'skipped' ? '任务实例已跳过。' : '任务实例已重新打开。', 0)
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    pending.value = false
  }
}

async function setOccurrenceStep(step: TaskStep, completed: boolean) {
  if (!task.value || pending.value || !identity.value) return
  const current = task.value
  const fingerprint = JSON.stringify({
    occurrence: current.occurrence_key,
    step: step.id,
    base: localCompletedStepIds.value.includes(step.id),
    completed,
    taskVersion: current.task_version,
    occurrenceVersion: current.occurrence_version,
  })
  const id = operationId(fingerprint)
  const baseCompleted = localCompletedStepIds.value.includes(step.id)
  pending.value = true
  error.value = null
  try {
    const result = await syncTaskOccurrenceStepCompletion(
      current,
      step.id,
      id,
      baseCompleted,
      completed,
    )
    operation.value = null
    localCompletedStepIds.value = [...result.occurrence.completed_step_ids]
    emit('changed', '步骤进度已同步；任务仍需明确完成。', 0)
  } catch (reason) {
    if (
      !navigator.onLine
      || (
        reason instanceof ApiError
        && ['network_error', 'request_timeout'].includes(reason.info.code)
      )
    ) {
      try {
        appendTaskOperation(identity.value.user.id, {
          schema_version: 1,
          kind: 'task_occurrence_step_completion',
          operation_id: id,
          task_id: current.task_id,
          occurrence_key: current.occurrence_key,
          step_id: step.id,
          expected_task_version: current.task_version,
          expected_occurrence_version: current.occurrence_version,
          base_completed: baseCompleted,
          completed,
          created_at: new Date().toISOString(),
        })
        localCompletedStepIds.value = completed
          ? [...localCompletedStepIds.value, step.id]
          : localCompletedStepIds.value.filter(item => item !== step.id)
      } catch (queueError) {
        error.value = failure(queueError)
      }
    } else {
      error.value = failure(reason)
    }
  } finally {
    pending.value = false
  }
}

async function saveTaskMove() {
  if (!task.value || pending.value || !taskMove.date) return
  const fingerprint = JSON.stringify({
    task: task.value.task_id,
    key: task.value.occurrence_key,
    version: task.value.task_version,
    date: taskMove.date,
    time: taskMove.time || null,
  })
  pending.value = true
  error.value = null
  try {
    await rescheduleTaskOccurrence(
      task.value,
      operationId(fingerprint),
      taskMove.date,
      taskMove.time || null,
      selectedOffset.value,
    )
    operation.value = null
    action.value = 'none'
    emit('changed', '任务实例已改期。', 0)
  } catch (reason) {
    captureAmbiguousOffset(reason)
  } finally {
    pending.value = false
  }
}

async function saveTaskMakeup() {
  if (!task.value || !taskDetail.value || pending.value) return
  const dueTime = task.value.due_kind === 'datetime'
    ? taskMakeup.time
    : null
  if (!taskMakeup.date || (task.value.due_kind === 'datetime' && !dueTime)) {
    error.value = localFailure(
      task.value.due_kind === 'datetime'
        ? '请填写完整的补做日期与时刻。'
        : '请选择补做日期。',
    )
    await nextTick()
    document.querySelector<HTMLElement>('.occurrence-panel .error-status')?.focus()
    return
  }
  const expectedTaskVersion = Math.max(
    task.value.task_version,
    taskDetail.value.version,
  )
  const fingerprint = JSON.stringify({
    task: task.value.task_id,
    source: task.value.occurrence_key,
    version: expectedTaskVersion,
    date: taskMakeup.date,
    time: dueTime,
    offset: selectedOffset.value,
  })
  pending.value = true
  error.value = null
  try {
    const result = await createTaskMakeup(
      task.value,
      expectedTaskVersion,
      operationId(fingerprint),
      taskMakeup.date,
      dueTime,
      selectedOffset.value,
    )
    taskDetail.value = result.task
    operation.value = null
    action.value = 'none'
    emit('changed', '补做任务已创建，原实例保持不变。', 0)
  } catch (reason) {
    const requestFailure = failure(reason)
    error.value = requestFailure
    candidateOffsets.value = requestFailure.details?.candidate_offsets_minutes ?? []
    if (requestFailure.code === 'task_version_conflict') {
      try {
        taskDetail.value = await getTask(task.value.task_id)
      } catch {
        // Keep the original conflict and the user's draft for a later retry.
      }
    }
    await nextTick()
    document.querySelector<HTMLElement>('.occurrence-panel .error-status')?.focus()
  } finally {
    pending.value = false
  }
}
</script>

<template>
  <aside class="occurrence-panel" :class="{ empty: !item }" aria-label="安排详情">
    <template v-if="item">
      <header class="occurrence-header">
        <button type="button" class="small-icon-button occurrence-back" aria-label="返回日历" title="返回日历"
          @click="emit('close')">
          <ArrowLeft :size="19" aria-hidden="true" />
        </button>
        <div>
          <p>
            {{ occurrence?.source_type === 'subscription'
              ? 'ICS 订阅'
              : item.kind === 'course'
                ? '课程'
                : item.kind === 'fixed'
                  ? '固定安排'
                  : item.kind === 'plan'
                    ? '计划块'
                  : '任务截止' }}
          </p>
          <h1 tabindex="-1">{{ item.title }}</h1>
        </div>
      </header>

      <div class="occurrence-body">
        <StatusMessage :error="error" :success="error ? '' : success" />
        <div v-if="item.conflict" class="status restriction-status" role="status">
          <AlertTriangle :size="20" aria-hidden="true" />
          <p v-if="plan?.source_conflict_count">
            订阅来源变化后与此计划块重叠。第 {{ plan.plan_version }} 版计划未被自动调整，请前往规划页处理。
          </p>
          <p v-else>这段时间与其他占用安排重叠。两项内容均已保留，请确认是否需要调整。</p>
        </div>

        <dl class="occurrence-facts">
          <div>
            <dt><CalendarClock :size="17" aria-hidden="true" />时间</dt>
            <dd v-if="occurrence?.all_day">
              {{ allDayLabel(occurrence) }}
            </dd>
            <dd v-else-if="occurrence">{{ dateTime(occurrence.start_at) }} 至 {{ dateTime(occurrence.end_at) }}</dd>
            <dd v-else-if="plan">
              {{ item.date }} {{ item.time?.slice(0, 5) }} 至 {{ item.end_time?.slice(0, 5) }}
            </dd>
            <dd v-else>{{ item.date }} {{ item.time?.slice(0, 5) || '全天' }}</dd>
          </div>
          <div v-if="item.subtitle">
            <dt>
              <ListChecks v-if="plan" :size="17" aria-hidden="true" />
              <MapPin v-else :size="17" aria-hidden="true" />
              {{ plan ? '步骤' : item.kind === 'task' ? '说明' : '地点' }}
            </dt>
            <dd>{{ item.subtitle }}</dd>
          </div>
          <div>
            <dt>状态</dt>
            <dd>{{ statusLabel(item.state) }}</dd>
          </div>
          <div v-if="occurrence?.exception_action">
            <dt>单次修改</dt>
            <dd>{{ occurrence.exception_action === 'moved' ? '已调课' : occurrence.exception_action === 'overridden' ? '已覆盖内容' : '已取消' }}</dd>
          </div>
          <div v-if="occurrence?.occurrence_type === 'addition'">
            <dt>来源</dt>
            <dd>关联补课 · {{ occurrence.original_local_start_date }}</dd>
          </div>
          <div v-if="occurrence?.source_type === 'subscription'">
            <dt>来源</dt>
            <dd>只读 ICS 订阅</dd>
          </div>
          <div
            v-if="occurrence?.source_type === 'subscription'
              && occurrence.source_status === 'source_cancelled'
              && occurrence.exception_action === 'moved'"
          >
            <dt>来源状态</dt>
            <dd>来源已取消，本地移动仍保留</dd>
          </div>
          <div v-if="plan">
            <dt>计划来源</dt>
            <dd>{{ plan.plan_source === 'ai' ? 'AI 提案确认' : '手工调整' }}</dd>
          </div>
          <div v-if="plan">
            <dt>计划版本</dt>
            <dd>第 {{ plan.plan_version }} 版</dd>
          </div>
          <div v-if="task?.kind === 'makeup' && task.makeup_for_key">
            <dt>来源</dt>
            <dd>关联补做 · 原实例 {{ task.makeup_for_key.slice(0, 10) }}</dd>
          </div>
        </dl>

        <p v-if="occurrence?.description" class="occurrence-description">{{ occurrence.description }}</p>
        <p v-if="task?.description" class="occurrence-description">{{ task.description }}</p>

        <template v-if="occurrence">
          <div v-if="occurrence.source_type === 'subscription'" class="occurrence-actions">
            <button
              v-if="occurrence.exception_id"
              type="button"
              class="button secondary"
              :disabled="pending"
              @click="resetSubscriptionOccurrence"
            >
              <RotateCcw :size="17" aria-hidden="true" />
              重置为来源
            </button>
            <button
              v-else-if="occurrence.status !== 'source_cancelled'"
              type="button"
              class="button secondary"
              :disabled="pending"
              @click="cancelOccurrence"
            >
              <CircleSlash2 :size="17" aria-hidden="true" />
              取消本次
            </button>
            <button
              type="button"
              class="button secondary"
              :disabled="pending"
              @click="begin('move')"
            >
              调整本次时间
            </button>
            <button
              type="button"
              class="button secondary"
              :disabled="pending"
              @click="begin('override')"
            >
              <Pencil :size="17" aria-hidden="true" />
              覆盖本次内容
            </button>
            <button
              type="button"
              class="button secondary"
              :disabled="pending || !occurrence.source_id || !occurrence.subscription_event_id"
              @click="occurrence.source_id && occurrence.subscription_event_id
                && emit('openSubscription', occurrence.source_id, occurrence.subscription_event_id)"
            >
              <SlidersHorizontal :size="17" aria-hidden="true" />
              管理来源与本地覆盖
            </button>
          </div>
          <div v-else-if="occurrence.occurrence_type === 'addition'" class="occurrence-actions">
            <button type="button" class="button secondary" :disabled="pending" @click="toggleAddition">
              <RotateCcw v-if="occurrence.status === 'cancelled'" :size="17" aria-hidden="true" />
              <CircleSlash2 v-else :size="17" aria-hidden="true" />
              {{ occurrence.status === 'cancelled' ? '恢复补课' : '取消补课' }}
            </button>
          </div>
          <template v-else>
            <div class="occurrence-actions">
              <button v-if="occurrence.status === 'cancelled'" type="button" class="button secondary"
                :disabled="pending" @click="restoreOccurrence">
                <RotateCcw :size="17" aria-hidden="true" />恢复本次
              </button>
              <button v-else type="button" class="button secondary" :disabled="pending" @click="cancelOccurrence">
                <CircleSlash2 :size="17" aria-hidden="true" />取消本次
              </button>
              <button type="button" class="button secondary" :disabled="pending" @click="begin('move')">
                调整本次时间
              </button>
              <button type="button" class="button secondary" :disabled="pending" @click="begin('override')">
                <Pencil :size="17" aria-hidden="true" />覆盖本次内容
              </button>
              <button type="button" class="button secondary" :disabled="pending" @click="begin('makeup')">安排补课</button>
            </div>
            <div class="series-actions">
              <button type="button" class="text-button" :disabled="pending" @click="emit('editSeries', occurrence)">
                编辑整个系列
              </button>
              <button v-if="canSplit" type="button" class="text-button" :disabled="pending"
                @click="emit('splitSeries', occurrence)">
                修改本次及以后
              </button>
              <button type="button" class="text-button danger-text" :disabled="pending"
                @click="trashCurrentSeries">
                <Trash2 :size="16" aria-hidden="true" />移到回收站
              </button>
            </div>
          </template>
        </template>

        <template v-if="task">
          <div
            v-if="taskDetail?.steps.length && task.status === 'open'"
            class="occurrence-step-list"
          >
            <h3>本次步骤</h3>
            <label v-for="step in taskDetail.steps" :key="step.id">
              <input
                type="checkbox"
                :checked="localCompletedStepIds.includes(step.id)"
                :disabled="pending"
                @change="setOccurrenceStep(step, ($event.target as HTMLInputElement).checked)"
              />
              <span>{{ step.title }}</span>
            </label>
          </div>
          <div class="occurrence-actions">
            <button v-if="task.status !== 'completed'" type="button" class="button" :disabled="pending"
              @click="setTaskStatus('completed')">
              <CheckCircle2 :size="17" aria-hidden="true" />标为完成
            </button>
            <button v-if="task.status !== 'open'" type="button" class="button secondary" :disabled="pending"
              @click="setTaskStatus('open')">
              <RotateCcw :size="17" aria-hidden="true" />重新打开
            </button>
            <button v-if="task.status === 'open'" type="button" class="button secondary" :disabled="pending"
              @click="setTaskStatus('skipped')">
              <CircleSlash2 :size="17" aria-hidden="true" />跳过本次
            </button>
            <button type="button" class="button secondary" :disabled="pending" @click="begin('task-move')">
              调整截止日期
            </button>
            <button v-if="canCreateTaskMakeup" type="button" class="button secondary"
              :disabled="pending" @click="begin('task-makeup')">
              <CalendarPlus :size="17" aria-hidden="true" />安排补做
            </button>
            <RouterLink to="/tasks" class="text-button">前往任务</RouterLink>
          </div>
        </template>

        <form v-if="action === 'move'" class="occurrence-action-form" @submit.prevent="saveMove">
          <h2>调整本次时间</h2>
          <div class="calendar-form-grid">
            <div class="field">
              <label for="move-date">开始日期</label>
              <input id="move-date" v-model="move.date" type="date" required />
            </div>
            <div v-if="occurrence?.all_day && occurrence.source_type === 'subscription'" class="field">
              <label for="move-end-date">结束日期（不含）</label>
              <input id="move-end-date" v-model="move.endDate" type="date" required />
            </div>
            <template v-else>
              <div class="field">
                <label for="move-start">开始</label>
                <input id="move-start" v-model="move.startsAt" type="time" required />
              </div>
              <div class="field">
                <label for="move-end">结束</label>
                <input id="move-end" v-model="move.endsAt" type="time" required />
              </div>
            </template>
          </div>
          <div v-if="candidateOffsets.length" class="field">
            <label for="occurrence-offset">重复时刻的 UTC 偏移</label>
            <select id="occurrence-offset" v-model.number="selectedOffset">
              <option :value="null" disabled>请选择这次使用的时间点</option>
              <option v-for="offset in candidateOffsets" :key="offset" :value="offset">{{ offsetLabel(offset) }}</option>
            </select>
          </div>
          <label
            v-if="!occurrence?.all_day"
            class="checkbox-label calendar-checkbox"
          >
            <input v-model="move.endDayOffset" type="checkbox" :true-value="1" :false-value="0" />
            跨至次日结束
          </label>
          <div class="dialog-actions"><button type="button" class="button secondary" @click="action = 'none'">取消</button><button class="button" :disabled="pending">保存本次</button></div>
        </form>

        <form v-else-if="action === 'override'" class="occurrence-action-form" @submit.prevent="saveOverride">
          <h2>覆盖本次内容</h2>
          <div class="field"><label for="override-name">名称</label><input id="override-name" v-model="override.name" maxlength="200" /></div>
          <div class="field"><label for="override-location">地点</label><input id="override-location" v-model="override.location" maxlength="300" /></div>
          <div class="field"><label for="override-description">说明</label><textarea id="override-description" v-model="override.description" rows="3" maxlength="20000"></textarea></div>
          <label class="checkbox-label calendar-checkbox"><input v-model="override.busy" type="checkbox" />参与冲突检查</label>
          <div class="dialog-actions"><button type="button" class="button secondary" @click="action = 'none'">取消</button><button class="button" :disabled="pending">保存本次</button></div>
        </form>

        <form v-else-if="action === 'makeup'" class="occurrence-action-form" @submit.prevent="saveMakeup">
          <h2>安排关联补课</h2>
          <div class="field"><label for="makeup-date">日期</label><input id="makeup-date" v-model="makeup.date" type="date" /></div>
          <div class="calendar-form-grid">
            <div class="field"><label for="makeup-start">开始</label><input id="makeup-start" v-model="makeup.startsAt" type="time" /></div>
            <div class="field"><label for="makeup-end">结束</label><input id="makeup-end" v-model="makeup.endsAt" type="time" /></div>
          </div>
          <div v-if="candidateOffsets.length" class="field">
            <label for="makeup-offset">重复时刻的 UTC 偏移</label>
            <select id="makeup-offset" v-model.number="selectedOffset">
              <option :value="null" disabled>请选择这次使用的时间点</option>
              <option v-for="offset in candidateOffsets" :key="offset" :value="offset">{{ offsetLabel(offset) }}</option>
            </select>
          </div>
          <label class="checkbox-label calendar-checkbox"><input v-model="makeup.endDayOffset" type="checkbox" :true-value="1" :false-value="0" />跨至次日结束</label>
          <div class="dialog-actions"><button type="button" class="button secondary" @click="action = 'none'">取消</button><button class="button" :disabled="pending">创建补课</button></div>
        </form>

        <form v-else-if="action === 'task-move'" class="occurrence-action-form" @submit.prevent="saveTaskMove">
          <h2>调整任务截止日期</h2>
          <div class="field"><label for="task-move-date">日期</label><input id="task-move-date" v-model="taskMove.date" type="date" /></div>
          <div class="field"><label for="task-move-time">时刻 <span class="muted">可选</span></label><input id="task-move-time" v-model="taskMove.time" type="time" /></div>
          <div v-if="candidateOffsets.length" class="field">
            <label for="task-move-offset">重复时刻的 UTC 偏移</label>
            <select id="task-move-offset" v-model.number="selectedOffset">
              <option :value="null" disabled>请选择这次使用的时间点</option>
              <option v-for="offset in candidateOffsets" :key="offset" :value="offset">{{ offsetLabel(offset) }}</option>
            </select>
          </div>
          <div class="dialog-actions"><button type="button" class="button secondary" @click="action = 'none'">取消</button><button class="button" :disabled="pending">保存改期</button></div>
        </form>

        <form v-else-if="action === 'task-makeup'" class="occurrence-action-form" @submit.prevent="saveTaskMakeup">
          <h2>安排关联补做</h2>
          <p class="section-empty">新实例会关联到本次任务，原实例状态保持不变。</p>
          <div class="field">
            <label for="task-makeup-date">补做日期</label>
            <input id="task-makeup-date" v-model="taskMakeup.date" type="date" required />
          </div>
          <div v-if="task?.due_kind === 'datetime'" class="field">
            <label for="task-makeup-time">补做时刻</label>
            <input id="task-makeup-time" v-model="taskMakeup.time" type="time" required />
          </div>
          <div v-if="candidateOffsets.length" class="field">
            <label for="task-makeup-offset">重复时刻的 UTC 偏移</label>
            <select id="task-makeup-offset" v-model.number="selectedOffset">
              <option :value="null" disabled>请选择这次使用的时间点</option>
              <option v-for="offset in candidateOffsets" :key="offset" :value="offset">{{ offsetLabel(offset) }}</option>
            </select>
          </div>
          <div class="dialog-actions">
            <button type="button" class="button secondary" @click="action = 'none'">取消</button>
            <button class="button" :disabled="pending">{{ pending ? '正在创建…' : '创建补做' }}</button>
          </div>
        </form>
      </div>
    </template>

    <div v-else class="occurrence-placeholder">
      <CalendarClock :size="30" aria-hidden="true" />
      <h2>选择一项安排</h2>
      <p>查看时间、冲突和单次修改操作。</p>
    </div>
  </aside>
</template>
