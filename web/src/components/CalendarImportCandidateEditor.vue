<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { RotateCcw, Save, X } from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import { failure, localFailure, type Failure } from '../lib/api'
import {
  getCalendarImport,
  updateImportCandidate,
  type CalendarImportBatch,
  type CalendarImportCandidate,
} from '../lib/calendar-imports'
import type { SeriesDraft, Term } from '../lib/calendar'
import { emptyRecurrence, type RecurrenceFrequency } from '../lib/task-schedules'

const props = defineProps<{
  batch: CalendarImportBatch
  candidate: CalendarImportCandidate
  terms: Term[]
  timeZone: string
  initialDate: string
}>()

const emit = defineEmits<{
  cancel: []
  updated: [batch: CalendarImportBatch, candidate: CalendarImportCandidate]
}>()

interface EditorValues {
  kind: 'course' | 'fixed'
  termId: string
  name: string
  description: string
  location: string
  busy: boolean
  timeZone: string
  startDate: string
  startsAt: string
  endsAt: string
  endDayOffset: 0 | 1
  timeMode: 'clock' | 'period'
  startPeriod: number
  endPeriod: number
  frequency: RecurrenceFrequency
  interval: number
  weekdays: number[]
  endKind: 'until' | 'count'
  until: string
  count: number
  weekNumbers: string
  month: number
  dayOfMonth: number
  ambiguousOffset: number | null
}

const values = reactive<EditorValues>({
  kind: 'fixed',
  termId: '',
  name: '',
  description: '',
  location: '',
  busy: true,
  timeZone: props.timeZone,
  startDate: props.initialDate,
  startsAt: '09:00',
  endsAt: '10:00',
  endDayOffset: 0,
  timeMode: 'clock',
  startPeriod: 1,
  endPeriod: 1,
  frequency: 'none',
  interval: 1,
  weekdays: [1],
  endKind: 'count',
  until: '',
  count: 12,
  weekNumbers: '1-16',
  month: 1,
  dayOfMonth: 1,
  ambiguousOffset: null,
})
const pending = ref(false)
const error = ref<Failure | null>(null)
const candidateOffsets = ref<number[]>([])
const workingBatch = ref(props.batch)
const workingCandidate = ref(props.candidate)
const serverCandidate = ref<CalendarImportCandidate | null>(null)
const conflictResolutionRequired = ref(false)
let operation: { id: string; fingerprint: string } | null = null

const weekdays = [
  { value: 1, label: '一' },
  { value: 2, label: '二' },
  { value: 3, label: '三' },
  { value: 4, label: '四' },
  { value: 5, label: '五' },
  { value: 6, label: '六' },
  { value: 7, label: '日' },
]
const id = computed(() => `import-candidate-${props.candidate.id}`)

function shortTime(value: string | null | undefined, fallback: string) {
  return value ? value.slice(0, 5) : fallback
}

function formatNumbers(numbers: number[]) {
  return numbers.join(',')
}

function parseNumbers(value: string, maximum: number) {
  const result = new Set<number>()
  for (const part of value.split(/[,，\s]+/).filter(Boolean)) {
    const match = part.match(/^(\d+)(?:[-—](\d+))?$/)
    if (!match) return null
    const start = Number(match[1])
    const end = Number(match[2] ?? match[1])
    if (start < 1 || end > maximum || start > end) return null
    for (let number = start; number <= end; number += 1) result.add(number)
  }
  return [...result].sort((left, right) => left - right)
}

function rawText(...keys: string[]) {
  for (const key of keys) {
    const value = props.candidate.raw_data[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return ''
}

function reset() {
  const draft = props.candidate.draft
  workingBatch.value = props.batch
  workingCandidate.value = props.candidate
  serverCandidate.value = null
  conflictResolutionRequired.value = false
  const startDate = draft?.start_date ?? props.initialDate
  values.kind = draft?.kind ?? 'fixed'
  values.termId = draft?.term_id ?? ''
  values.name = draft?.name ?? rawText('name', 'summary')
  values.description = draft?.description ?? ''
  values.location = draft?.location ?? ''
  values.busy = draft?.busy ?? true
  values.timeZone = draft?.time_zone ?? props.timeZone
  values.startDate = startDate ?? ''
  values.startsAt = shortTime(draft?.starts_at, '09:00')
  values.endsAt = shortTime(draft?.ends_at, '10:00')
  values.endDayOffset = draft?.end_day_offset ?? 0
  values.timeMode = draft?.start_period_number ? 'period' : 'clock'
  values.startPeriod = draft?.start_period_number ?? 1
  values.endPeriod = draft?.end_period_number ?? draft?.start_period_number ?? 1
  values.frequency = draft?.recurrence.frequency ?? 'none'
  values.interval = draft?.recurrence.interval ?? 1
  values.weekdays = draft?.recurrence.weekdays.length
    ? [...draft.recurrence.weekdays]
    : [1]
  values.endKind = draft?.recurrence.until ? 'until' : 'count'
  values.until = draft?.recurrence.until ?? ''
  values.count = draft?.recurrence.count ?? 12
  values.weekNumbers = formatNumbers(draft?.recurrence.week_numbers ?? []) || '1-16'
  values.month = draft?.recurrence.month ?? Number(startDate?.slice(5, 7) || 1)
  values.dayOfMonth = draft?.recurrence.day_of_month
    ?? Number(startDate?.slice(8, 10) || 1)
  values.ambiguousOffset = draft?.ambiguous_utc_offset_minutes ?? null
  candidateOffsets.value = (
    props.candidate.error?.details.candidate_offsets_minutes as number[] | undefined
  ) ?? []
  error.value = props.candidate.error
    ? {
      code: props.candidate.error.code,
      message: props.candidate.error.message,
      request_id: null,
      saved: 'saved',
      retryable: false,
      details: props.candidate.error.details,
    }
    : null
  operation = null
}

watch(() => props.candidate, reset, { immediate: true })

function toggleWeekday(day: number, checked: boolean) {
  values.weekdays = checked
    ? [...new Set([...values.weekdays, day])].sort()
    : values.weekdays.filter(value => value !== day)
}

function draft(): SeriesDraft | null {
  if (!values.name.trim()) {
    error.value = localFailure('请输入安排名称。')
    return null
  }
  if (values.kind === 'fixed' && !values.startDate) {
    error.value = localFailure('固定安排必须填写开始日期。')
    return null
  }
  if (values.kind === 'course' && !values.termId) {
    error.value = localFailure('课程必须选择学期。')
    return null
  }
  if (values.timeMode === 'clock' && (!values.startsAt || !values.endsAt)) {
    error.value = localFailure('请填写完整的开始和结束时刻。')
    return null
  }
  if (values.kind === 'course' && !values.weekdays.length) {
    error.value = localFailure('课程至少选择一个星期。')
    return null
  }
  if (values.kind === 'fixed' && values.frequency === 'weekly' && !values.weekdays.length) {
    error.value = localFailure('每周重复至少选择一个星期。')
    return null
  }
  const weekNumbers = values.kind === 'course'
    ? parseNumbers(values.weekNumbers, 60)
    : []
  if (values.kind === 'course' && !weekNumbers?.length) {
    error.value = localFailure('有效周次格式不正确。')
    return null
  }
  const recurrence = emptyRecurrence()
  recurrence.frequency = values.kind === 'course' ? 'weekly' : values.frequency
  recurrence.interval = (
    values.kind === 'course' || recurrence.frequency === 'weekdays'
      ? 1
      : values.interval
  )
  recurrence.weekdays = recurrence.frequency === 'weekly'
    ? [...values.weekdays]
    : []
  recurrence.week_numbers = weekNumbers ?? []
  if (values.kind === 'fixed' && recurrence.frequency !== 'none') {
    if (values.endKind === 'until') {
      if (!values.until) {
        error.value = localFailure('请选择重复结束日期。')
        return null
      }
      recurrence.until = values.until
    } else {
      recurrence.count = values.count
    }
    if (recurrence.frequency === 'monthly') {
      recurrence.day_of_month = values.dayOfMonth
    }
    if (recurrence.frequency === 'yearly') {
      recurrence.month = values.month
      recurrence.day_of_month = values.dayOfMonth
    }
  }
  const periodMode = values.timeMode === 'period'
  return {
    kind: values.kind,
    term_id: values.termId || null,
    name: values.name.trim(),
    teacher: '',
    description: values.description,
    location: values.location,
    busy: values.busy,
    time_zone: values.timeZone || props.timeZone,
    start_date: values.startDate || null,
    starts_at: periodMode ? null : values.startsAt,
    ends_at: periodMode ? null : values.endsAt,
    end_day_offset: periodMode ? 0 : values.endDayOffset,
    start_period_number: periodMode ? values.startPeriod : null,
    end_period_number: periodMode ? values.endPeriod : null,
    ambiguous_utc_offset_minutes: periodMode ? null : values.ambiguousOffset,
    recurrence,
  }
}

function operationId(fingerprint: string) {
  if (!operation || operation.fingerprint !== fingerprint) {
    operation = { id: crypto.randomUUID(), fingerprint }
  }
  return operation.id
}

async function save() {
  if (pending.value) return
  if (conflictResolutionRequired.value) {
    error.value = localFailure('请先比较服务器最新版，再选择保留当前填写或采用服务器候选。')
    return
  }
  error.value = null
  const fields = draft()
  if (!fields) return
  const fingerprint = JSON.stringify({
    batchVersion: workingBatch.value.version,
    candidateVersion: workingCandidate.value.version,
    fields,
  })
  pending.value = true
  try {
    const result = await updateImportCandidate(
      workingBatch.value,
      workingCandidate.value,
      fields,
      operationId(fingerprint),
    )
    operation = null
    emit('updated', result.batch, result.candidate)
    if (result.candidate.state === 'invalid' && result.candidate.error) {
      error.value = {
        code: result.candidate.error.code,
        message: result.candidate.error.message,
        request_id: null,
        saved: 'saved',
        retryable: false,
        details: result.candidate.error.details,
      }
      candidateOffsets.value = (
        result.candidate.error.details.candidate_offsets_minutes as number[] | undefined
      ) ?? []
      await nextTick()
      document.querySelector<HTMLElement>(`#${id.value} .error-status`)?.focus()
      return
    }
    emit('cancel')
  } catch (reason) {
    error.value = failure(reason)
    if (
      error.value.code === 'calendar_import_version_conflict'
      || error.value.code === 'calendar_import_candidate_version_conflict'
    ) {
      try {
        const latest = await getCalendarImport(props.batch.id)
        const latestCandidate = latest.candidates.find(
          candidate => candidate.id === props.candidate.id,
        )
        if (latestCandidate) {
          workingBatch.value = latest.batch
          workingCandidate.value = latestCandidate
          serverCandidate.value = latestCandidate
          conflictResolutionRequired.value = true
          operation = null
        }
      } catch {
        error.value = localFailure('无法读取服务器最新版。当前填写仍保留，请稍后重试。')
      }
    }
    await nextTick()
    document.querySelector<HTMLElement>(`#${id.value} .error-status`)?.focus()
  } finally {
    pending.value = false
  }
}

function keepCurrentDraft() {
  conflictResolutionRequired.value = false
  error.value = null
  operation = null
}

function useServerCandidate() {
  if (!serverCandidate.value) return
  emit('updated', workingBatch.value, serverCandidate.value)
  emit('cancel')
}
</script>

<template>
  <form :id="id" class="import-candidate-editor" novalidate @submit.prevent="save">
    <div class="import-editor-heading">
      <div>
        <strong>校对候选 {{ candidate.position }}</strong>
        <span>版本 {{ candidate.version }}</span>
      </div>
      <button type="button" class="small-icon-button" aria-label="关闭候选校对"
        title="关闭候选校对" :disabled="pending" @click="emit('cancel')">
        <X :size="18" aria-hidden="true" />
      </button>
    </div>
    <StatusMessage :error="error" />
    <div v-if="conflictResolutionRequired && serverCandidate"
      class="conflict-comparison import-conflict-comparison">
      <strong>服务器最新版 · 候选版本 {{ serverCandidate.version }}</strong>
      <p>名称：{{ serverCandidate.draft?.name ?? '待校对' }}</p>
      <p>
        时间：{{ serverCandidate.draft?.start_date ?? '未设置' }}
        {{ serverCandidate.draft?.starts_at?.slice(0, 5) ?? '' }}
      </p>
      <strong>当前填写</strong>
      <p>名称：{{ values.name || '未填写' }}</p>
      <p>时间：{{ values.startDate || '未设置' }} {{ values.startsAt }}</p>
      <div class="inline-actions">
        <button type="button" class="text-button" @click="keepCurrentDraft">
          保留当前填写并基于最新版本重试
        </button>
        <button type="button" class="text-button" @click="useServerCandidate">
          采用服务器候选
        </button>
      </div>
    </div>

    <div class="import-editor-grid">
      <div class="field span-two">
        <label :for="`${id}-name`">名称</label>
        <input :id="`${id}-name`" v-model="values.name" maxlength="200"
          :disabled="pending" />
      </div>
      <div class="field">
        <label :for="`${id}-kind`">类型</label>
        <select :id="`${id}-kind`" v-model="values.kind" :disabled="pending">
          <option value="fixed">固定安排</option>
          <option value="course">课程</option>
        </select>
      </div>
      <div class="field">
        <label :for="`${id}-term`">学期</label>
        <select :id="`${id}-term`" v-model="values.termId" :disabled="pending">
          <option value="">不关联学期</option>
          <option v-for="term in terms" :key="term.id" :value="term.id">
            {{ term.name }}
          </option>
        </select>
      </div>
      <div class="field">
        <label :for="`${id}-date`">开始日期</label>
        <input :id="`${id}-date`" v-model="values.startDate" type="date"
          :disabled="pending" />
      </div>
      <div class="field">
        <label :for="`${id}-timezone`">IANA 时区</label>
        <input :id="`${id}-timezone`" v-model="values.timeZone" maxlength="64"
          :disabled="pending" />
      </div>
      <div class="field">
        <label :for="`${id}-time-mode`">时间填写方式</label>
        <select :id="`${id}-time-mode`" v-model="values.timeMode" :disabled="pending">
          <option value="clock">按时刻</option>
          <option value="period">按节次</option>
        </select>
      </div>
      <template v-if="values.timeMode === 'period'">
        <div class="field">
          <label :for="`${id}-start-period`">开始节次</label>
          <input :id="`${id}-start-period`" v-model.number="values.startPeriod"
            type="number" min="1" max="99" :disabled="pending" />
        </div>
        <div class="field">
          <label :for="`${id}-end-period`">结束节次</label>
          <input :id="`${id}-end-period`" v-model.number="values.endPeriod"
            type="number" min="1" max="99" :disabled="pending" />
        </div>
      </template>
      <template v-else>
        <div class="field">
          <label :for="`${id}-start-time`">开始时刻</label>
          <input :id="`${id}-start-time`" v-model="values.startsAt" type="time"
            :disabled="pending" />
        </div>
        <div class="field">
          <label :for="`${id}-end-time`">结束时刻</label>
          <input :id="`${id}-end-time`" v-model="values.endsAt" type="time"
            :disabled="pending" />
        </div>
        <label class="checkbox-label">
          <input v-model="values.endDayOffset" type="checkbox" :true-value="1"
            :false-value="0" :disabled="pending" />
          跨至次日结束
        </label>
      </template>
      <div v-if="candidateOffsets.length" class="field">
        <label :for="`${id}-offset`">重复时刻的 UTC 偏移</label>
        <select :id="`${id}-offset`" v-model.number="values.ambiguousOffset"
          :disabled="pending">
          <option :value="null" disabled>请选择</option>
          <option v-for="offset in candidateOffsets" :key="offset" :value="offset">
            {{ offset }}
          </option>
        </select>
      </div>
      <div class="field">
        <label :for="`${id}-frequency`">重复方式</label>
        <select :id="`${id}-frequency`" v-model="values.frequency"
          :disabled="pending || values.kind === 'course'">
          <option value="none">不重复</option>
          <option value="daily">每日</option>
          <option value="weekdays">工作日</option>
          <option value="weekly">每周</option>
          <option value="monthly">每月</option>
          <option value="yearly">每年</option>
        </select>
      </div>
      <div v-if="values.kind === 'course' || values.frequency === 'weekly'"
        class="weekday-picker span-two">
        <span>星期</span>
        <label v-for="day in weekdays" :key="day.value">
          <input type="checkbox" :checked="values.weekdays.includes(day.value)"
            :disabled="pending"
            @change="toggleWeekday(day.value, ($event.target as HTMLInputElement).checked)" />
          <span>{{ day.label }}</span>
        </label>
      </div>
      <div v-if="values.kind === 'course'" class="field span-two">
        <label :for="`${id}-weeks`">有效周次</label>
        <input :id="`${id}-weeks`" v-model="values.weekNumbers"
          placeholder="1-16,18" :disabled="pending" />
      </div>
      <template v-else-if="values.frequency !== 'none'">
        <div class="field">
          <label :for="`${id}-interval`">重复间隔</label>
          <input :id="`${id}-interval`" v-model.number="values.interval"
            type="number" min="1" max="366" :disabled="pending" />
        </div>
        <div class="field">
          <label :for="`${id}-repeat-end`">结束方式</label>
          <select :id="`${id}-repeat-end`" v-model="values.endKind" :disabled="pending">
            <option value="count">按次数</option>
            <option value="until">按日期</option>
          </select>
        </div>
        <div v-if="values.endKind === 'count'" class="field">
          <label :for="`${id}-count`">重复次数</label>
          <input :id="`${id}-count`" v-model.number="values.count"
            type="number" min="1" max="366" :disabled="pending" />
        </div>
        <div v-else class="field">
          <label :for="`${id}-until`">重复结束日期</label>
          <input :id="`${id}-until`" v-model="values.until" type="date"
            :disabled="pending" />
        </div>
        <div v-if="values.frequency === 'monthly'" class="field">
          <label :for="`${id}-month-day`">每月日期</label>
          <input :id="`${id}-month-day`" v-model.number="values.dayOfMonth"
            type="number" min="1" max="31" :disabled="pending" />
        </div>
        <template v-if="values.frequency === 'yearly'">
          <div class="field">
            <label :for="`${id}-month`">月份</label>
            <input :id="`${id}-month`" v-model.number="values.month"
              type="number" min="1" max="12" :disabled="pending" />
          </div>
          <div class="field">
            <label :for="`${id}-year-day`">日期</label>
            <input :id="`${id}-year-day`" v-model.number="values.dayOfMonth"
              type="number" min="1" max="31" :disabled="pending" />
          </div>
        </template>
      </template>
      <div class="field">
        <label :for="`${id}-location`">地点</label>
        <input :id="`${id}-location`" v-model="values.location" maxlength="300"
          :disabled="pending" />
      </div>
      <label class="checkbox-label import-busy-field">
        <input v-model="values.busy" type="checkbox" :disabled="pending" />
        占用时间
      </label>
      <div class="field span-two">
        <label :for="`${id}-description`">说明</label>
        <textarea :id="`${id}-description`" v-model="values.description"
          maxlength="20000" rows="3" :disabled="pending"></textarea>
      </div>
    </div>

    <div class="import-editor-actions">
      <button type="button" class="button secondary"
        :disabled="pending || conflictResolutionRequired" @click="reset">
        <RotateCcw :size="17" aria-hidden="true" />恢复候选
      </button>
      <button type="submit" class="button" :disabled="pending">
        <Save :size="17" aria-hidden="true" />{{ pending ? '正在校验…' : '保存校对' }}
      </button>
    </div>
  </form>
</template>
