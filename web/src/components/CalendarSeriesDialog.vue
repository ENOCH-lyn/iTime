<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { BookOpen, CalendarClock } from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import { failure, localFailure, type Failure } from '../lib/api'
import {
  getSeries,
  getTerm,
  saveSeries,
  splitSeries,
  type CalendarOccurrence,
  type CalendarSeries,
  type SeriesDraft,
  type Term,
} from '../lib/calendar'
import { emptyRecurrence, type RecurrenceFrequency } from '../lib/task-schedules'

const props = defineProps<{
  open: boolean
  series: CalendarSeries | null
  splitOccurrence: CalendarOccurrence | null
  terms: Term[]
  timeZone: string
  initialDate: string
}>()

const emit = defineEmits<{
  close: []
  saved: [message: string, conflictCount: number]
}>()

interface FormValues {
  kind: 'course' | 'fixed'
  termId: string
  name: string
  teacher: string
  description: string
  location: string
  busy: boolean
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
}

const dialog = ref<HTMLDialogElement | null>(null)
const pending = ref(false)
const error = ref<Failure | null>(null)
const serverSeries = ref<CalendarSeries | null>(null)
const termDetails = ref<Record<string, Term>>({})
const periodSnapshotChanged = ref(false)
const conflictResolutionRequired = ref(false)
const candidateOffsets = ref<number[]>([])
const selectedOffset = ref<number | null>(null)
const values = reactive<FormValues>({
  kind: 'fixed',
  termId: '',
  name: '',
  teacher: '',
  description: '',
  location: '',
  busy: true,
  startDate: '',
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
})
let operation: { id: string; fingerprint: string } | null = null

const editing = computed(() => !!props.series)
const splitting = computed(() => !!props.splitOccurrence)
const selectedTerm = computed(() => termDetails.value[values.termId] ?? (
  props.terms.find(term => term.id === values.termId) ?? null
))
const weekdays = [
  { value: 1, label: '一' },
  { value: 2, label: '二' },
  { value: 3, label: '三' },
  { value: 4, label: '四' },
  { value: 5, label: '五' },
  { value: 6, label: '六' },
  { value: 7, label: '日' },
]

function shortTime(value: string) {
  return value.slice(0, 5)
}

function isoWeekday(value: string) {
  const [year, month, day] = value.split('-').map(Number)
  const weekday = new Date(year, month - 1, day, 12).getDay()
  return weekday === 0 ? 7 : weekday
}

function utcDate(value: string) {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(Date.UTC(year, month - 1, day, 12))
}

function dateString(value: Date) {
  return value.toISOString().slice(0, 10)
}

function addUtcDays(value: string, days: number) {
  const date = utcDate(value)
  date.setUTCDate(date.getUTCDate() + days)
  return dateString(date)
}

function occurrenceDates(series: CalendarSeries) {
  const recurrence = series.recurrence
  const count = recurrence.count ?? 0
  if (!count) return []
  const results: string[] = []
  const append = (candidate: string) => {
    if (candidate >= series.start_date) results.push(candidate)
    return results.length >= count
  }
  if (recurrence.frequency === 'daily') {
    let current = series.start_date
    while (!append(current)) current = addUtcDays(current, recurrence.interval)
  } else if (recurrence.frequency === 'weekdays') {
    let current = series.start_date
    while (results.length < count) {
      if (isoWeekday(current) <= 5) append(current)
      current = addUtcDays(current, 1)
    }
  } else if (recurrence.frequency === 'weekly') {
    let current = series.start_date
    const startMonday = addUtcDays(series.start_date, 1 - isoWeekday(series.start_date))
    while (results.length < count) {
      const days = Math.round((utcDate(current).getTime() - utcDate(startMonday).getTime()) / 86_400_000)
      const week = Math.floor(days / 7)
      if (
        week % recurrence.interval === 0
        && recurrence.weekdays.includes(isoWeekday(current))
      ) append(current)
      current = addUtcDays(current, 1)
    }
  } else if (recurrence.frequency === 'monthly') {
    const start = utcDate(series.start_date)
    let monthIndex = start.getUTCFullYear() * 12 + start.getUTCMonth()
    while (results.length < count) {
      const year = Math.floor(monthIndex / 12)
      const month = monthIndex % 12
      const candidate = new Date(Date.UTC(year, month, recurrence.day_of_month ?? 1, 12))
      if (candidate.getUTCMonth() === month) append(dateString(candidate))
      monthIndex += recurrence.interval
    }
  } else if (recurrence.frequency === 'yearly') {
    let year = utcDate(series.start_date).getUTCFullYear()
    while (results.length < count) {
      const month = (recurrence.month ?? 1) - 1
      const candidate = new Date(Date.UTC(year, month, recurrence.day_of_month ?? 1, 12))
      if (candidate.getUTCMonth() === month) append(dateString(candidate))
      year += recurrence.interval
    }
  }
  return results
}

function remainingCount(series: CalendarSeries, splitDate: string) {
  if (!series.recurrence.count) return series.recurrence.count
  const index = occurrenceDates(series).indexOf(splitDate)
  return index < 0 ? series.recurrence.count : series.recurrence.count - index
}

function formatWeekNumbers(valuesToFormat: number[]) {
  if (!valuesToFormat.length) return ''
  const groups: string[] = []
  let start = valuesToFormat[0]
  let previous = start
  for (const value of valuesToFormat.slice(1)) {
    if (value === previous + 1) {
      previous = value
      continue
    }
    groups.push(start === previous ? `${start}` : `${start}-${previous}`)
    start = value
    previous = value
  }
  groups.push(start === previous ? `${start}` : `${start}-${previous}`)
  return groups.join(',')
}

function parseWeekNumbers(value: string) {
  const result = new Set<number>()
  for (const rawPart of value.split(/[,，\s]+/).filter(Boolean)) {
    const match = rawPart.match(/^(\d+)(?:-(\d+))?$/)
    if (!match) return null
    const start = Number(match[1])
    const end = Number(match[2] ?? match[1])
    if (start < 1 || end > 60 || start > end) return null
    for (let number = start; number <= end; number += 1) result.add(number)
  }
  return [...result].sort((left, right) => left - right)
}

function offsetLabel(minutes: number) {
  const sign = minutes >= 0 ? '+' : '-'
  const absolute = Math.abs(minutes)
  return `UTC${sign}${String(Math.floor(absolute / 60)).padStart(2, '0')}:${String(absolute % 60).padStart(2, '0')}`
}

async function loadTermDetail(id: string, force = false) {
  if (!id || (!force && termDetails.value[id]?.periods)) return
  try {
    const term = await getTerm(id)
    termDetails.value = { ...termDetails.value, [id]: term }
  } catch (reason) {
    error.value = failure(reason)
  }
}

function protectPeriodSnapshot(series: CalendarSeries) {
  periodSnapshotChanged.value = false
  if (!series.start_period_number || !series.term_id) return
  const detail = termDetails.value[series.term_id]
  const startPeriod = detail?.periods?.find(
    period => period.period_number === series.start_period_number,
  )
  const endPeriod = detail?.periods?.find(
    period => period.period_number === series.end_period_number,
  )
  if (
    !startPeriod
    || !endPeriod
    || shortTime(startPeriod.starts_at) !== shortTime(series.starts_at)
    || shortTime(endPeriod.ends_at) !== shortTime(series.ends_at)
  ) {
    values.timeMode = 'clock'
    periodSnapshotChanged.value = true
  }
}

watch(() => values.termId, id => { void loadTermDetail(id) })

watch(() => props.open, async open => {
  if (!open) {
    dialog.value?.close()
    return
  }
  const series = props.series
  serverSeries.value = series
  const startDate = props.splitOccurrence?.original_local_start_date
    ?? series?.start_date
    ?? props.initialDate
  values.kind = series?.kind ?? (props.terms.length ? 'course' : 'fixed')
  values.termId = series?.term_id ?? (values.kind === 'course' ? props.terms[0]?.id ?? '' : '')
  values.name = series?.name ?? ''
  values.teacher = series?.teacher ?? ''
  values.description = series?.description ?? ''
  values.location = series?.location ?? ''
  values.busy = series?.busy ?? true
  values.startDate = startDate
  values.startsAt = shortTime(series?.starts_at ?? '09:00')
  values.endsAt = shortTime(series?.ends_at ?? '10:00')
  values.endDayOffset = series?.end_day_offset ?? 0
  values.timeMode = series?.start_period_number ? 'period' : 'clock'
  values.startPeriod = series?.start_period_number ?? 1
  values.endPeriod = series?.end_period_number ?? series?.start_period_number ?? 1
  values.frequency = series?.recurrence.frequency ?? 'none'
  values.interval = series?.recurrence.interval ?? 1
  values.weekdays = series?.recurrence.weekdays?.length
    ? [...series.recurrence.weekdays]
    : [isoWeekday(startDate)]
  values.endKind = series?.recurrence.until ? 'until' : 'count'
  values.until = series?.recurrence.until ?? ''
  values.count = (
    props.splitOccurrence && series
      ? remainingCount(series, props.splitOccurrence.original_local_start_date)
      : series?.recurrence.count
  ) ?? 12
  values.weekNumbers = formatWeekNumbers(series?.recurrence.week_numbers ?? [])
    || '1-16'
  error.value = null
  periodSnapshotChanged.value = false
  conflictResolutionRequired.value = false
  candidateOffsets.value = []
  selectedOffset.value = series?.ambiguous_utc_offset_minutes ?? null
  operation = null
  if (values.termId) await loadTermDetail(values.termId, true)
  if (series) protectPeriodSnapshot(series)
  await nextTick()
  dialog.value?.showModal()
  document.getElementById('calendar-series-name')?.focus()
})

watch(() => values.kind, kind => {
  if (kind === 'course') {
    values.frequency = 'weekly'
    if (!values.termId) values.termId = props.terms[0]?.id ?? ''
  } else if (values.frequency === 'weekly' && props.series?.kind !== 'fixed') {
    values.frequency = 'none'
  }
})

function resetOffsetChoice() {
  candidateOffsets.value = []
  selectedOffset.value = null
}

function confirmOverwrite() {
  conflictResolutionRequired.value = false
  error.value = null
  operation = null
}

async function applyLatest() {
  const series = serverSeries.value
  if (!series) return
  values.termId = series.term_id ?? ''
  values.name = series.name
  values.description = series.description
  values.location = series.location
  values.busy = series.busy
  values.startDate = props.splitOccurrence?.original_local_start_date ?? series.start_date
  values.startsAt = shortTime(series.starts_at)
  values.endsAt = shortTime(series.ends_at)
  values.endDayOffset = series.end_day_offset
  values.timeMode = series.start_period_number ? 'period' : 'clock'
  values.startPeriod = series.start_period_number ?? 1
  values.endPeriod = series.end_period_number ?? series.start_period_number ?? 1
  values.frequency = series.recurrence.frequency
  values.interval = series.recurrence.interval
  values.weekdays = [...series.recurrence.weekdays]
  values.endKind = series.recurrence.until ? 'until' : 'count'
  values.until = series.recurrence.until ?? ''
  values.count = (
    props.splitOccurrence
      ? remainingCount(series, props.splitOccurrence.original_local_start_date)
      : series.recurrence.count
  ) ?? 12
  values.weekNumbers = formatWeekNumbers(series.recurrence.week_numbers) || '1-16'
  selectedOffset.value = series.ambiguous_utc_offset_minutes
  candidateOffsets.value = []
  conflictResolutionRequired.value = false
  error.value = null
  operation = null
  if (values.termId) await loadTermDetail(values.termId, true)
  protectPeriodSnapshot(series)
}

function close() {
  if (pending.value) return
  dialog.value?.close()
  emit('close')
}

function toggleWeekday(value: number, checked: boolean) {
  values.weekdays = checked
    ? [...new Set([...values.weekdays, value])].sort()
    : values.weekdays.filter(day => day !== value)
}

function draft(): SeriesDraft | null {
  if (!values.name.trim()) {
    error.value = localFailure('请输入安排名称。')
    return null
  }
  if (!values.startDate || !values.startsAt || !values.endsAt) {
    error.value = localFailure('请填写完整的日期与时间。')
    return null
  }
  if (values.kind === 'course' && !values.termId) {
    error.value = localFailure('课程必须选择学期。')
    return null
  }
  if (values.kind === 'course' && !values.weekdays.length) {
    error.value = localFailure('课程至少选择一个上课日。')
    return null
  }
  const weekNumbers = values.kind === 'course'
    ? parseWeekNumbers(values.weekNumbers)
    : []
  if (values.kind === 'course' && (!weekNumbers || !weekNumbers.length)) {
    error.value = localFailure('请用 1-16、18 这样的格式填写有效周次。')
    return null
  }
  if (values.kind === 'fixed' && values.frequency === 'weekly' && !values.weekdays.length) {
    error.value = localFailure('每周重复至少选择一个星期。')
    return null
  }
  const repeating = values.kind === 'course' || values.frequency !== 'none'
  const frequency = values.kind === 'course' ? 'weekly' : values.frequency
  const recurrence = emptyRecurrence()
  recurrence.frequency = frequency
  recurrence.interval = values.kind === 'course' || frequency === 'weekdays' ? 1 : values.interval
  recurrence.weekdays = frequency === 'weekly' ? [...values.weekdays] : []
  recurrence.week_numbers = weekNumbers ?? []
  if (values.kind === 'fixed' && repeating) {
    if (values.endKind === 'until') {
      if (!values.until) {
        error.value = localFailure('请选择重复结束日期。')
        return null
      }
      recurrence.until = values.until
    } else {
      recurrence.count = values.count
    }
    if (frequency === 'monthly') recurrence.day_of_month = Number(values.startDate.slice(8, 10))
    if (frequency === 'yearly') {
      recurrence.month = Number(values.startDate.slice(5, 7))
      recurrence.day_of_month = Number(values.startDate.slice(8, 10))
    }
  }
  const periodMode = values.timeMode === 'period'
  return {
    kind: values.kind,
    term_id: values.termId || null,
    name: values.name.trim(),
    teacher: values.teacher,
    description: values.description,
    location: values.location,
    busy: values.busy,
    time_zone: props.timeZone,
    start_date: values.startDate,
    starts_at: periodMode ? null : values.startsAt,
    ends_at: periodMode ? null : values.endsAt,
    end_day_offset: periodMode ? 0 : values.endDayOffset,
    start_period_number: periodMode ? values.startPeriod : null,
    end_period_number: periodMode ? values.endPeriod : null,
    ambiguous_utc_offset_minutes: periodMode ? null : selectedOffset.value,
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
    error.value = localFailure('请先比较服务器最新版，并明确选择保留当前填写或改用服务器内容。')
    return
  }
  error.value = null
  const fields = draft()
  if (!fields) {
    await nextTick()
    document.querySelector<HTMLElement>('#calendar-series-dialog .error-status')?.focus()
    return
  }
  const current = serverSeries.value
  const fingerprint = JSON.stringify({
    scope: splitting.value ? 'future' : current ? 'all' : 'create',
    version: current?.version,
    occurrence: props.splitOccurrence?.occurrence_key,
    fields,
  })
  pending.value = true
  try {
    const result = splitting.value && current && props.splitOccurrence
      ? await splitSeries(
        current,
        props.splitOccurrence.occurrence_key,
        fields,
        operationId(fingerprint),
      )
      : await saveSeries(current, fields, operationId(fingerprint))
    operation = null
    const conflicts = result.conflicts.items.length
    emit(
      'saved',
      splitting.value ? '已从本次开始创建新的后继系列。' : current ? '系列修改已保存。' : '日程已创建。',
      conflicts,
    )
    dialog.value?.close()
    emit('close')
  } catch (reason) {
    error.value = failure(reason)
    if (error.value.code === 'ambiguous_local_time') {
      candidateOffsets.value = error.value.details?.candidate_offsets_minutes ?? []
    }
    if (current && error.value.code === 'calendar_series_version_conflict') {
      try {
        serverSeries.value = await getSeries(current.id)
        conflictResolutionRequired.value = true
        operation = null
      } catch {
        error.value = localFailure('无法读取服务器最新版。当前填写仍保留，请稍后重试。')
      }
    }
    await nextTick()
    document.querySelector<HTMLElement>('#calendar-series-dialog .error-status')?.focus()
  } finally {
    pending.value = false
  }
}
</script>

<template>
  <dialog id="calendar-series-dialog" ref="dialog" class="calendar-dialog calendar-series-dialog"
    aria-labelledby="calendar-series-title" @cancel.prevent="close">
    <form novalidate @submit.prevent="save">
      <div class="calendar-dialog-heading">
        <component :is="values.kind === 'course' ? BookOpen : CalendarClock" :size="22" aria-hidden="true" />
        <div>
          <h2 id="calendar-series-title">
            {{ splitting ? '修改本次及以后' : editing ? '编辑整个系列' : '添加日程' }}
          </h2>
          <p>{{ splitting ? '此前实例保持不变，所选实例开始使用新设置。' : '课程按学期周次展开，固定安排按日期规则展开。' }}</p>
        </div>
      </div>

      <StatusMessage :error="error" />
      <div v-if="conflictResolutionRequired && serverSeries" class="conflict-comparison">
        <strong>服务器最新版 · 版本 {{ serverSeries.version }}</strong>
        <p>名称：{{ serverSeries.name }}</p>
        <p>地点：{{ serverSeries.location || '未设置' }}</p>
        <p>时间：{{ shortTime(serverSeries.starts_at) }}–{{ shortTime(serverSeries.ends_at) }}</p>
        <strong>当前填写</strong>
        <p>名称：{{ values.name }}</p>
        <p>地点：{{ values.location || '未设置' }}</p>
        <p>时间：{{ values.startsAt }}–{{ values.endsAt }}</p>
        <div class="inline-actions">
          <button type="button" class="text-button" @click="confirmOverwrite">
            保留当前填写并明确覆盖服务器内容
          </button>
          <button type="button" class="text-button" @click="applyLatest">改用服务器内容</button>
        </div>
      </div>
      <p v-if="periodSnapshotChanged" class="conflict-note">
        当前学期节次表已变化。表单已改用系列原先保存的明确时刻，保存不会静默套用新节次。
      </p>

      <fieldset class="segmented-field">
        <legend>类型</legend>
        <label :class="{ selected: values.kind === 'course' }">
          <input v-model="values.kind" type="radio" value="course" :disabled="pending || editing" />
          <BookOpen :size="16" aria-hidden="true" />课程
        </label>
        <label :class="{ selected: values.kind === 'fixed' }">
          <input v-model="values.kind" type="radio" value="fixed" :disabled="pending || editing" />
          <CalendarClock :size="16" aria-hidden="true" />固定安排
        </label>
      </fieldset>

      <div class="calendar-form-grid">
        <div class="field span-two">
          <label for="calendar-series-name">名称</label>
          <input id="calendar-series-name" v-model="values.name" maxlength="200" :disabled="pending" />
        </div>
        <div class="field">
          <label for="calendar-series-location">地点 <span class="muted">可选</span></label>
          <input id="calendar-series-location" v-model="values.location" maxlength="300" :disabled="pending" />
        </div>
        <div class="field">
          <label for="calendar-series-teacher">教师 <span class="muted">可选</span></label>
          <input id="calendar-series-teacher" v-model="values.teacher" maxlength="200" :disabled="pending" />
        </div>
        <div class="field">
          <label for="calendar-series-term">学期 {{ values.kind === 'course' ? '' : '（可选）' }}</label>
          <select id="calendar-series-term" v-model="values.termId" :disabled="pending || splitting">
            <option v-if="values.kind === 'fixed'" value="">不关联学期</option>
            <option v-for="term in terms" :key="term.id" :value="term.id">{{ term.name }}</option>
          </select>
        </div>
        <div class="field span-two">
          <label for="calendar-series-description">说明 <span class="muted">可选</span></label>
          <textarea id="calendar-series-description" v-model="values.description" maxlength="20000"
            rows="3" :disabled="pending"></textarea>
        </div>
      </div>

      <fieldset class="calendar-fieldset">
        <legend>日期与时间</legend>
        <div class="calendar-form-grid">
          <div class="field">
            <label for="calendar-series-date">{{ values.kind === 'course' ? '首个实例日期' : '开始日期' }}</label>
            <input id="calendar-series-date" v-model="values.startDate" type="date"
              :disabled="pending || splitting" @input="resetOffsetChoice" />
          </div>
          <div v-if="values.termId && selectedTerm?.period_count" class="field">
            <label for="calendar-time-mode">时间填写方式</label>
            <select id="calendar-time-mode" v-model="values.timeMode" :disabled="pending">
              <option value="period">按节次</option>
              <option value="clock">按时刻</option>
            </select>
          </div>
          <template v-if="values.timeMode === 'period' && selectedTerm?.periods?.length">
            <div class="field">
              <label for="calendar-start-period">开始节次</label>
              <select id="calendar-start-period" v-model.number="values.startPeriod" :disabled="pending">
                <option v-for="period in selectedTerm.periods" :key="period.period_number" :value="period.period_number">
                  {{ period.name }} · {{ shortTime(period.starts_at) }}
                </option>
              </select>
            </div>
            <div class="field">
              <label for="calendar-end-period">结束节次</label>
              <select id="calendar-end-period" v-model.number="values.endPeriod" :disabled="pending">
                <option v-for="period in selectedTerm.periods" :key="period.period_number" :value="period.period_number">
                  {{ period.name }} · {{ shortTime(period.ends_at) }}
                </option>
              </select>
            </div>
          </template>
          <template v-else>
            <div class="field">
              <label for="calendar-start-time">开始时刻</label>
              <input id="calendar-start-time" v-model="values.startsAt" type="time"
                :disabled="pending" @input="resetOffsetChoice" />
            </div>
            <div class="field">
              <label for="calendar-end-time">结束时刻</label>
              <input id="calendar-end-time" v-model="values.endsAt" type="time"
                :disabled="pending" @input="resetOffsetChoice" />
            </div>
            <label class="checkbox-label calendar-checkbox">
              <input v-model="values.endDayOffset" type="checkbox" :true-value="1" :false-value="0"
                :disabled="pending" @change="resetOffsetChoice" />
              跨至次日结束
            </label>
            <div v-if="candidateOffsets.length" class="field span-two">
              <label for="calendar-ambiguous-offset">重复时刻的 UTC 偏移</label>
              <select id="calendar-ambiguous-offset" v-model.number="selectedOffset" :disabled="pending">
                <option :value="null" disabled>请选择这次使用的时间点</option>
                <option v-for="offset in candidateOffsets" :key="offset" :value="offset">
                  {{ offsetLabel(offset) }}
                </option>
              </select>
              <p class="field-hint">当地时钟在夏令时回拨后出现两次，请明确选择偏移再保存。</p>
            </div>
          </template>
        </div>
      </fieldset>

      <fieldset class="calendar-fieldset">
        <legend>{{ values.kind === 'course' ? '上课周次' : '重复规则' }}</legend>
        <template v-if="values.kind === 'course'">
          <div class="field">
            <label for="calendar-course-weeks">有效周次</label>
            <input id="calendar-course-weeks" v-model="values.weekNumbers" placeholder="1-16,18" :disabled="pending" />
            <p class="field-hint">支持逗号和范围，例如 1-8,10,12-16。</p>
          </div>
        </template>
        <div v-else class="calendar-form-grid">
          <div class="field">
            <label for="calendar-frequency">重复方式</label>
            <select id="calendar-frequency" v-model="values.frequency" :disabled="pending">
              <option value="none">不重复</option>
              <option value="daily">每日</option>
              <option value="weekdays">工作日</option>
              <option value="weekly">每周</option>
              <option value="monthly">每月</option>
              <option value="yearly">每年</option>
            </select>
          </div>
          <div v-if="!['none', 'weekdays'].includes(values.frequency)" class="field">
            <label for="calendar-interval">每隔</label>
            <div class="input-with-suffix">
              <input id="calendar-interval" v-model.number="values.interval" type="number" min="1" max="366" :disabled="pending" />
              <span>{{ values.frequency === 'daily' ? '天' : values.frequency === 'weekly' ? '周' : values.frequency === 'monthly' ? '月' : '年' }}</span>
            </div>
          </div>
        </div>
        <div v-if="values.kind === 'course' || values.frequency === 'weekly'" class="weekday-picker">
          <span>星期</span>
          <label v-for="day in weekdays" :key="day.value">
            <input type="checkbox" :checked="values.weekdays.includes(day.value)" :disabled="pending"
              @change="toggleWeekday(day.value, ($event.target as HTMLInputElement).checked)" />
            <span>{{ day.label }}</span>
          </label>
        </div>
        <div v-if="values.kind === 'fixed' && values.frequency !== 'none'" class="repeat-end">
          <label><input v-model="values.endKind" type="radio" value="count" :disabled="pending" />共</label>
          <input v-model.number="values.count" aria-label="重复次数" type="number" min="1" max="366"
            :disabled="pending || values.endKind !== 'count'" />
          <span>次</span>
          <label><input v-model="values.endKind" type="radio" value="until" :disabled="pending" />至</label>
          <input v-model="values.until" aria-label="重复结束日期" type="date"
            :disabled="pending || values.endKind !== 'until'" />
        </div>
      </fieldset>

      <label class="checkbox-label calendar-checkbox">
        <input v-model="values.busy" type="checkbox" :disabled="pending" />
        标记为占用时间，参与冲突检查
      </label>

      <div class="dialog-actions">
        <button type="button" class="button secondary" :disabled="pending" @click="close">取消</button>
        <button type="submit" class="button" :disabled="pending">
          {{ pending ? '正在保存…' : splitting ? '保存本次及以后' : editing ? '保存整个系列' : '创建日程' }}
        </button>
      </div>
    </form>
  </dialog>
</template>

