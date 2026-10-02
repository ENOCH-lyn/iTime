<script setup lang="ts">
import { computed, nextTick, onUnmounted, reactive, ref, watch } from 'vue'
import { CalendarClock, Save } from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import { failure, localFailure, type Failure } from '../lib/api'
import { getTask, type TaskDetail } from '../lib/tasks'
import {
  emptyRecurrence,
  updateTaskSchedule,
  type RecurrenceFrequency,
  type TaskScheduleDraft,
} from '../lib/task-schedules'

const props = defineProps<{ task: TaskDetail }>()
const emit = defineEmits<{
  updated: [task: TaskDetail]
  dirty: [dirty: boolean]
}>()

const values = reactive({
  due_kind: 'none' as TaskScheduleDraft['due_kind'],
  due_date: '',
  due_time: '',
  splittable: true,
  minimum_block_minutes: '15',
  frequency: 'none' as RecurrenceFrequency,
  interval: '1',
  weekdays: [] as number[],
  end_mode: 'until' as 'until' | 'count',
  until: '',
  count: '4',
})
const initialSnapshot = ref('')
const workingVersion = ref(props.task.version)
const pending = ref(false)
const error = ref<Failure | null>(null)
const success = ref('')
const latest = ref<TaskDetail | null>(null)
const warnings = ref<string[]>([])
const fieldErrors = reactive<Record<string, string>>({})
let operation: { id: string; fingerprint: string } | null = null
let preserveOnNextProp = false

const weekdays = [
  { value: 1, label: '一' },
  { value: 2, label: '二' },
  { value: 3, label: '三' },
  { value: 4, label: '四' },
  { value: 5, label: '五' },
  { value: 6, label: '六' },
  { value: 7, label: '日' },
]

const frequencyNames: Record<RecurrenceFrequency, string> = {
  none: '不重复',
  daily: '每天',
  weekdays: '每个工作日',
  weekly: '每周',
  monthly: '每月',
  yearly: '每年',
}

function rawSnapshot() {
  return JSON.stringify({
    due_kind: values.due_kind,
    due_date: values.due_date,
    due_time: values.due_time,
    splittable: values.splittable,
    minimum_block_minutes: values.minimum_block_minutes,
    frequency: values.frequency,
    interval: values.interval,
    weekdays: [...values.weekdays].sort(),
    end_mode: values.end_mode,
    until: values.until,
    count: values.count,
  })
}

const dirty = computed(() => rawSnapshot() !== initialSnapshot.value)
watch(dirty, value => emit('dirty', value), { immediate: true })

watch(
  () => [props.task.id, props.task.version, JSON.stringify(props.task.schedule)] as const,
  () => {
    workingVersion.value = props.task.version
    if (preserveOnNextProp) {
      preserveOnNextProp = false
      return
    }
    reset(props.task)
  },
  { immediate: true },
)

onUnmounted(() => emit('dirty', false))

function reset(task: TaskDetail) {
  const schedule = task.schedule
  values.due_kind = schedule.due_kind
  values.due_date = schedule.due_date ?? ''
  values.due_time = schedule.due_time?.slice(0, 5) ?? ''
  values.splittable = schedule.splittable
  values.minimum_block_minutes = String(schedule.minimum_block_minutes)
  values.frequency = schedule.recurrence.frequency
  values.interval = String(schedule.recurrence.interval)
  values.weekdays = [...schedule.recurrence.weekdays]
  values.end_mode = schedule.recurrence.count !== null ? 'count' : 'until'
  values.until = schedule.recurrence.until ?? ''
  values.count = String(schedule.recurrence.count ?? 4)
  initialSnapshot.value = rawSnapshot()
  operation = null
  for (const key of Object.keys(fieldErrors)) delete fieldErrors[key]
}

function toggleWeekday(value: number) {
  values.weekdays = values.weekdays.includes(value)
    ? values.weekdays.filter(item => item !== value)
    : [...values.weekdays, value].sort()
}

function draft(): TaskScheduleDraft | null {
  for (const key of Object.keys(fieldErrors)) delete fieldErrors[key]
  const minimum = Number(values.minimum_block_minutes)
  if (!Number.isInteger(minimum) || minimum < 1 || minimum > 1440) {
    fieldErrors.minimum = '请输入 1—1440 之间的整数分钟数。'
  }
  if (values.due_kind === 'none') {
    return Object.keys(fieldErrors).length
      ? null
      : {
          due_kind: 'none',
          due_date: null,
          due_time: null,
          time_zone: null,
          ambiguous_utc_offset_minutes: null,
          splittable: values.splittable,
          minimum_block_minutes: minimum,
          recurrence: emptyRecurrence(),
        }
  }
  if (!values.due_date) fieldErrors.due_date = '请选择截止日期。'
  if (values.due_kind === 'datetime' && !values.due_time) {
    fieldErrors.due_time = '请选择截止时刻。'
  }
  const interval = Number(values.interval)
  if (!Number.isInteger(interval) || interval < 1 || interval > 366) {
    fieldErrors.interval = '重复间隔必须是 1—366 之间的整数。'
  }
  if (values.frequency === 'weekly' && !values.weekdays.length) {
    fieldErrors.weekdays = '每周重复至少选择一天。'
  }
  let until: string | null = null
  let count: number | null = null
  if (values.frequency !== 'none') {
    if (values.end_mode === 'until') {
      until = values.until
      if (!until) fieldErrors.until = '请选择重复结束日期。'
    } else {
      count = Number(values.count)
      if (!Number.isInteger(count) || count < 1 || count > 366) {
        fieldErrors.count = '重复次数必须是 1—366 之间的整数。'
      }
    }
  }
  if (Object.keys(fieldErrors).length) return null
  const dueDate = new Date(`${values.due_date}T00:00:00`)
  return {
    due_kind: values.due_kind,
    due_date: values.due_date,
    due_time: values.due_kind === 'datetime' ? `${values.due_time}:00` : null,
    time_zone: props.task.schedule.time_zone,
    ambiguous_utc_offset_minutes: props.task.schedule.ambiguous_utc_offset_minutes,
    splittable: values.splittable,
    minimum_block_minutes: minimum,
    recurrence: {
      frequency: values.frequency,
      interval,
      weekdays: values.frequency === 'weekly' ? values.weekdays : [],
      month: values.frequency === 'yearly' ? dueDate.getMonth() + 1 : null,
      day_of_month: ['monthly', 'yearly'].includes(values.frequency)
        ? dueDate.getDate()
        : null,
      until,
      count,
      week_numbers: [],
    },
  }
}

async function focusFirstError() {
  await nextTick()
  const key = Object.keys(fieldErrors)[0]
  document.getElementById(`task-schedule-${key}`)?.focus()
}

async function save() {
  if (pending.value) return
  error.value = null
  success.value = ''
  latest.value = null
  warnings.value = []
  const fields = draft()
  if (!fields) {
    await focusFirstError()
    return
  }
  const body = { expected_task_version: workingVersion.value, ...fields }
  const fingerprint = JSON.stringify(body)
  if (!operation || operation.fingerprint !== fingerprint) {
    operation = { id: crypto.randomUUID(), fingerprint }
  }
  pending.value = true
  try {
    const result = await updateTaskSchedule(
      { id: props.task.id, version: workingVersion.value },
      operation.id,
      fields,
    )
    operation = null
    workingVersion.value = result.task.version
    warnings.value = result.warnings.map(item => item.message)
    reset(result.task)
    success.value = values.due_kind === 'none'
      ? '日期与重复设置已清除。'
      : '日期与重复设置已保存。'
    emit('updated', result.task)
  } catch (reason) {
    error.value = failure(reason)
    if (error.value.code === 'task_version_conflict') {
      try {
        latest.value = await getTask(props.task.id)
      } catch {
        error.value = localFailure('无法读取服务器最新版。当前设置仍保留，请稍后重试。')
      }
    }
    await nextTick()
    document.querySelector<HTMLElement>('.task-schedule-editor .error-status')?.focus()
  } finally {
    pending.value = false
  }
}

function keepDraftOnLatest() {
  if (!latest.value) return
  workingVersion.value = latest.value.version
  preserveOnNextProp = true
  emit('updated', latest.value)
  latest.value = null
  error.value = null
  operation = null
  success.value = '已载入最新版本号；当前日期设置仍保留，请检查后再次保存。'
}

function useServerVersion() {
  if (!latest.value) return
  const task = latest.value
  workingVersion.value = task.version
  latest.value = null
  error.value = null
  operation = null
  reset(task)
  emit('updated', task)
  success.value = '已改用服务器上的最新日期设置。'
}
</script>

<template>
  <section class="editor-section task-schedule-editor" aria-labelledby="task-schedule-title">
    <div class="editor-section-heading">
      <div>
        <h2 id="task-schedule-title">日期与重复</h2>
        <p>日期型截止在任务时区当天结束前有效。</p>
      </div>
      <CalendarClock :size="20" aria-hidden="true" />
    </div>

    <StatusMessage :error="error" :success="success" />
    <div v-if="latest" class="conflict-comparison">
      <strong>服务器最新版</strong>
      <p>任务版本 {{ latest.version }} · 当前填写尚未丢失</p>
      <div class="inline-actions">
        <button type="button" class="text-button" @click="keepDraftOnLatest">
          保留填写并基于最新版重试
        </button>
        <button type="button" class="text-button" @click="useServerVersion">
          改用服务器设置
        </button>
      </div>
    </div>
    <div v-if="warnings.length" class="schedule-warning" role="status">
      <strong>已保存，但有时间提示</strong>
      <p v-for="message in warnings" :key="message">{{ message }}</p>
    </div>

    <div class="schedule-grid">
      <div class="field">
        <label for="task-schedule-kind">截止类型</label>
        <select id="task-schedule-kind" v-model="values.due_kind" :disabled="pending">
          <option value="none">不设置截止</option>
          <option value="date">截止日期</option>
          <option value="datetime">具体日期与时刻</option>
        </select>
      </div>
      <div v-if="values.due_kind !== 'none'" class="field">
        <label for="task-schedule-due_date">截止日期</label>
        <input id="task-schedule-due_date" v-model="values.due_date" type="date"
          :disabled="pending" :aria-invalid="!!fieldErrors.due_date" />
        <p v-if="fieldErrors.due_date" class="field-error">{{ fieldErrors.due_date }}</p>
      </div>
      <div v-if="values.due_kind === 'datetime'" class="field">
        <label for="task-schedule-due_time">截止时刻</label>
        <input id="task-schedule-due_time" v-model="values.due_time" type="time"
          :disabled="pending" :aria-invalid="!!fieldErrors.due_time" />
        <p v-if="fieldErrors.due_time" class="field-error">{{ fieldErrors.due_time }}</p>
      </div>
      <div class="field">
        <label for="task-schedule-minimum">最小连续时长 <span class="muted">分钟</span></label>
        <input id="task-schedule-minimum" v-model="values.minimum_block_minutes"
          type="number" inputmode="numeric" min="1" max="1440" :disabled="pending"
          :aria-invalid="!!fieldErrors.minimum" />
        <p v-if="fieldErrors.minimum" class="field-error">{{ fieldErrors.minimum }}</p>
      </div>
    </div>

    <label class="checkbox-label schedule-splittable">
      <input v-model="values.splittable" type="checkbox" :disabled="pending" />
      规划时允许拆成多个时间块
    </label>

    <template v-if="values.due_kind !== 'none'">
      <div class="schedule-grid recurrence-grid">
        <div class="field">
          <label for="task-schedule-frequency">重复方式</label>
          <select id="task-schedule-frequency" v-model="values.frequency" :disabled="pending">
            <option v-for="(name, value) in frequencyNames" :key="value" :value="value">
              {{ name }}
            </option>
          </select>
        </div>
        <div v-if="!['none', 'weekdays'].includes(values.frequency)" class="field">
          <label for="task-schedule-interval">每隔</label>
          <div class="inline-number-field">
            <input id="task-schedule-interval" v-model="values.interval" type="number"
              inputmode="numeric" min="1" max="366" :disabled="pending"
              :aria-invalid="!!fieldErrors.interval" />
            <span>{{ values.frequency === 'daily' ? '天' : values.frequency === 'weekly' ? '周' : values.frequency === 'monthly' ? '月' : '年' }}</span>
          </div>
          <p v-if="fieldErrors.interval" class="field-error">{{ fieldErrors.interval }}</p>
        </div>
      </div>

      <fieldset v-if="values.frequency === 'weekly'" class="weekday-field">
        <legend>在星期几重复</legend>
        <div>
          <label v-for="day in weekdays" :key="day.value"
            :class="{ selected: values.weekdays.includes(day.value) }">
            <input type="checkbox" :checked="values.weekdays.includes(day.value)"
              :disabled="pending" @change="toggleWeekday(day.value)" />
            {{ day.label }}
          </label>
        </div>
        <p v-if="fieldErrors.weekdays" id="task-schedule-weekdays" class="field-error">
          {{ fieldErrors.weekdays }}
        </p>
      </fieldset>

      <fieldset v-if="values.frequency !== 'none'" class="schedule-end-field">
        <legend>重复结束</legend>
        <label>
          <input v-model="values.end_mode" type="radio" value="until" :disabled="pending" />
          到日期
        </label>
        <input id="task-schedule-until" v-model="values.until" type="date"
          :disabled="pending || values.end_mode !== 'until'"
          :aria-invalid="!!fieldErrors.until" aria-label="重复结束日期" />
        <label>
          <input v-model="values.end_mode" type="radio" value="count" :disabled="pending" />
          共
        </label>
        <input id="task-schedule-count" v-model="values.count" type="number"
          inputmode="numeric" min="1" max="366"
          :disabled="pending || values.end_mode !== 'count'"
          :aria-invalid="!!fieldErrors.count" aria-label="重复次数" />
        <span>次</span>
        <p v-if="fieldErrors.until || fieldErrors.count" class="field-error">
          {{ fieldErrors.until || fieldErrors.count }}
        </p>
      </fieldset>
    </template>

    <div class="schedule-save-row">
      <p v-if="task.schedule.time_zone" class="section-empty">
        使用 {{ task.schedule.time_zone }}；修改个人时区不会改写已保存的截止时间。
      </p>
      <p v-else class="section-empty">首次保存时采用当前浏览器时区。</p>
      <button type="button" class="button secondary compact-button"
        :disabled="pending || !dirty" @click="save">
        <Save :size="16" aria-hidden="true" />
        {{ pending ? '正在保存…' : '保存日期与重复' }}
      </button>
    </div>
  </section>
</template>
