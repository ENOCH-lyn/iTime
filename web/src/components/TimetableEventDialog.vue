<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import {
  isoWeekday,
  weekdayShortLabels,
} from '../lib/timetable'

export type CourseWeekMode = 'all' | 'odd' | 'even' | 'custom'

export interface CourseSlotForm {
  id: string | null
  version: number | null
  weekdays: number[]
  startTime: string
  endTime: string
  location: string
  startWeek: number
  endWeek: number
  weekMode: CourseWeekMode
  weekNumbers: number[]
}

export interface EventFormValues {
  name: string
  kind: 'course' | 'fixed'
  teacher: string
  description: string
  location: string
  date: string
  startTime: string
  endTime: string
  repeat: boolean
  weekdays: number[]
  startWeek: number
  endWeek: number
  slots: CourseSlotForm[]
}

const props = defineProps<{
  modelValue: boolean
  mode: 'create' | 'edit'
  weekCount: number
  initial: EventFormValues
  busy: boolean
  error: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [values: EventFormValues]
}>()

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T
}

const form = reactive<EventFormValues>(clone(props.initial))
const touchedWeekdays = ref(false)

const weekLabels = weekdayShortLabels()
const weekOptions = computed(() => (
  Array.from({ length: Math.max(1, props.weekCount) }, (_, index) => index + 1)
))

function freshSlot(): CourseSlotForm {
  return {
    id: null,
    version: null,
    weekdays: [isoWeekday(form.date || new Date().toISOString().slice(0, 10))],
    startTime: form.startTime || '09:00',
    endTime: form.endTime || '10:00',
    location: form.location || '',
    startWeek: form.startWeek || 1,
    endWeek: form.endWeek || Math.max(1, props.weekCount),
    weekMode: 'all',
    weekNumbers: [],
  }
}

function weekNumbersFor(slot: CourseSlotForm): number[] {
  if (slot.weekMode === 'custom') {
    return [...slot.weekNumbers]
      .filter(value => value >= 1 && value <= props.weekCount)
      .sort((left, right) => left - right)
  }
  const weeks: number[] = []
  for (let week = slot.startWeek; week <= slot.endWeek; week += 1) {
    if (slot.weekMode === 'odd' && week % 2 === 0) continue
    if (slot.weekMode === 'even' && week % 2 === 1) continue
    weeks.push(week)
  }
  return weeks
}

function slotValid(slot: CourseSlotForm): boolean {
  return (
    slot.weekdays.length > 0
    && /^\d{2}:\d{2}$/.test(slot.startTime)
    && /^\d{2}:\d{2}$/.test(slot.endTime)
    && slot.endTime > slot.startTime
    && slot.startWeek >= 1
    && slot.endWeek >= slot.startWeek
    && slot.endWeek <= props.weekCount
    && (slot.weekMode !== 'custom' || weekNumbersFor(slot).length > 0)
  )
}

const valid = computed(() => {
  if (!form.name.trim()) return false
  if (form.kind === 'fixed') {
    return (
      /^\d{2}:\d{2}$/.test(form.startTime)
      && /^\d{2}:\d{2}$/.test(form.endTime)
      && form.endTime > form.startTime
      && (!form.repeat || form.weekdays.length > 0)
    )
  }
  return form.slots.length > 0 && form.slots.every(slotValid)
})

const validationMessage = computed(() => {
  if (!form.name.trim()) return '给这门课起个名字。'
  if (form.kind === 'fixed') {
    if (!form.startTime || !form.endTime) return '需要开始和结束时间。'
    if (form.endTime <= form.startTime) return '结束时间需要晚于开始时间。'
    if (form.repeat && !form.weekdays.length) return '每周重复至少要选一天。'
    return ''
  }
  for (const [index, slot] of form.slots.entries()) {
    if (!slot.weekdays.length) return `时间段 ${index + 1} 至少要选一天。`
    if (!slot.startTime || !slot.endTime || slot.endTime <= slot.startTime) {
      return `时间段 ${index + 1} 的结束时间需要晚于开始时间。`
    }
    if (slot.endWeek < slot.startWeek) return `时间段 ${index + 1} 的结束周不能早于开始周。`
    if (slot.endWeek > props.weekCount) return `时间段 ${index + 1} 超出学期周数。`
    if (slot.weekMode === 'custom' && !weekNumbersFor(slot).length) {
      return `时间段 ${index + 1} 请填写有效的指定周。`
    }
  }
  return ''
})

function reset() {
  Object.assign(form, clone(props.initial))
  touchedWeekdays.value = false
  if (form.kind === 'course' && form.slots.length === 0) {
    form.slots = [freshSlot()]
  }
  if (props.initial.repeat && props.initial.weekdays.length === 0) {
    form.weekdays = [isoWeekday(props.initial.date)]
  }
}

watch(() => props.modelValue, value => {
  if (value) reset()
})
watch(() => props.initial, () => {
  if (props.modelValue) reset()
}, { deep: true })
watch(() => form.kind, value => {
  if (value === 'course' && form.slots.length === 0) form.slots = [freshSlot()]
})

function onDateChange() {
  if (!form.date || touchedWeekdays.value || !form.repeat) return
  form.weekdays = [isoWeekday(form.date)]
}

function toggleWeekday(day: number) {
  touchedWeekdays.value = true
  if (form.weekdays.includes(day)) {
    form.weekdays = form.weekdays.filter(item => item !== day)
  } else {
    form.weekdays = [...form.weekdays, day].sort((left, right) => left - right)
  }
}

function toggleSlotWeekday(slot: CourseSlotForm, day: number) {
  if (slot.weekdays.includes(day)) {
    slot.weekdays = slot.weekdays.filter(item => item !== day)
  } else {
    slot.weekdays = [...slot.weekdays, day].sort((left, right) => left - right)
  }
}

function addSlot() {
  const previous = form.slots.at(-1) ?? freshSlot()
  form.slots.push({
    ...clone(previous),
    id: null,
    version: null,
    weekdays: [],
    weekNumbers: [],
  })
}

function removeSlot(index: number) {
  if (form.slots.length <= 1) return
  form.slots.splice(index, 1)
}

function setCustomWeeks(slot: CourseSlotForm, event: Event) {
  const input = event.target as HTMLInputElement
  slot.weekNumbers = input.value
    .split(/[，,、\s]+/)
    .map(value => Number(value))
    .filter(value => Number.isInteger(value) && value >= 1 && value <= props.weekCount)
    .sort((left, right) => left - right)
}

function close() {
  emit('update:modelValue', false)
}

function submit() {
  if (!valid.value || props.busy) return
  emit('submit', {
    ...clone(form),
    name: form.name.trim(),
    teacher: form.teacher.trim(),
    description: form.description.trim(),
    location: form.location.trim(),
    weekdays: [...form.weekdays].sort((left, right) => left - right),
    slots: form.slots.map(slot => ({
      ...clone(slot),
      weekdays: [...slot.weekdays].sort((left, right) => left - right),
      weekNumbers: weekNumbersFor(slot),
      location: slot.location.trim(),
    })),
  })
}
</script>

<template>
  <ElDialog
    :model-value="modelValue"
    :title="mode === 'create' ? '添加课程或安排' : '编辑课程或安排'"
    width="min(680px, 94vw)"
    align-center
    class="tt-dialog"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <form class="tt-form" @submit.prevent="submit">
      <label class="tt-field">
        <span>{{ form.kind === 'course' ? '课程名称' : '安排名称' }}</span>
        <input v-model="form.name" type="text" maxlength="80" placeholder="例如：高等数学 / 健身" autocomplete="off">
      </label>

      <div class="tt-row">
        <label class="tt-field">
          <span>类型</span>
          <select v-model="form.kind">
            <option value="course">课程</option>
            <option value="fixed">固定安排</option>
          </select>
        </label>
        <label v-if="form.kind === 'course'" class="tt-field">
          <span>教师</span>
          <input v-model="form.teacher" type="text" maxlength="120" placeholder="例如：张老师 / 可选" autocomplete="off">
        </label>
      </div>

      <label class="tt-field">
        <span>{{ form.kind === 'course' ? '课程备注' : '备注' }}</span>
        <textarea v-model="form.description" rows="3" maxlength="2000" placeholder="教材、考核方式、上课要求……" />
      </label>

      <template v-if="form.kind === 'course'">
        <div v-for="(slot, index) in form.slots" :key="slot.id ?? index" class="tt-slot">
          <header class="tt-slot-head">
            <strong>时间段 {{ index + 1 }}</strong>
            <button v-if="form.slots.length > 1" type="button" class="tt-slot-remove" @click="removeSlot(index)">移除</button>
          </header>

          <fieldset class="tt-weekdays">
            <legend>上课日</legend>
            <button
              v-for="(label, dayIndex) in weekLabels"
              :key="label"
              type="button"
              class="tt-weekday"
              :class="{ active: slot.weekdays.includes(dayIndex + 1) }"
              :aria-pressed="slot.weekdays.includes(dayIndex + 1)"
              @click="toggleSlotWeekday(slot, dayIndex + 1)"
            >{{ label }}</button>
          </fieldset>

          <div class="tt-row">
            <label class="tt-field">
              <span>开始</span>
              <input v-model="slot.startTime" type="time" step="300">
            </label>
            <label class="tt-field">
              <span>结束</span>
              <input v-model="slot.endTime" type="time" step="300">
            </label>
            <label class="tt-field">
              <span>地点</span>
              <input v-model="slot.location" type="text" maxlength="120" placeholder="教学楼 / 线上" autocomplete="off">
            </label>
          </div>

          <div class="tt-row">
            <label class="tt-field">
              <span>开始周</span>
              <select v-model.number="slot.startWeek">
                <option v-for="week in weekOptions" :key="week" :value="week">第 {{ week }} 周</option>
              </select>
            </label>
            <label class="tt-field">
              <span>结束周</span>
              <select v-model.number="slot.endWeek">
                <option v-for="week in weekOptions" :key="week" :value="week">第 {{ week }} 周</option>
              </select>
            </label>
            <label class="tt-field">
              <span>周次规则</span>
              <select v-model="slot.weekMode">
                <option value="all">每周</option>
                <option value="odd">单周</option>
                <option value="even">双周</option>
                <option value="custom">指定周</option>
              </select>
            </label>
          </div>

          <label v-if="slot.weekMode === 'custom'" class="tt-field">
            <span>指定周（逗号分隔）</span>
            <input :value="slot.weekNumbers.join(', ')" type="text" placeholder="例如：1, 3, 5, 7" @input="setCustomWeeks(slot, $event)">
          </label>
        </div>

        <button type="button" class="tt-add-slot" @click="addSlot">＋ 添加另一个时间段</button>
      </template>

      <template v-else>
        <div class="tt-row">
          <label class="tt-field">
            <span>地点</span>
            <input v-model="form.location" type="text" maxlength="120" placeholder="教学楼 / 线上 / 可选" autocomplete="off">
          </label>
          <label class="tt-field">
            <span>日期</span>
            <input v-model="form.date" type="date" @change="onDateChange">
          </label>
          <label class="tt-field">
            <span>开始</span>
            <input v-model="form.startTime" type="time" step="300">
          </label>
          <label class="tt-field">
            <span>结束</span>
            <input v-model="form.endTime" type="time" step="300">
          </label>
        </div>

        <label class="tt-switch">
          <input v-model="form.repeat" type="checkbox">
          <span class="tt-switch-track" aria-hidden="true"><span /></span>
          <span class="tt-switch-copy">每周重复<small>适合固定安排</small></span>
        </label>

        <fieldset v-if="form.repeat" class="tt-weekdays">
          <legend>重复在</legend>
          <button
            v-for="(label, index) in weekLabels"
            :key="label"
            type="button"
            class="tt-weekday"
            :class="{ active: form.weekdays.includes(index + 1) }"
            :aria-pressed="form.weekdays.includes(index + 1)"
            @click="toggleWeekday(index + 1)"
          >{{ label }}</button>
        </fieldset>
      </template>

      <p v-if="validationMessage" class="tt-hint" role="status">{{ validationMessage }}</p>
      <p v-if="error" class="tt-error" role="alert">{{ error }}</p>

      <div class="tt-actions">
        <button type="button" class="tt-button ghost" :disabled="busy" @click="close">取消</button>
        <button type="submit" class="tt-button primary" :disabled="!valid || busy">
          {{ busy ? '保存中…' : (mode === 'create' ? '加入课表' : '保存修改') }}
        </button>
      </div>
    </form>
  </ElDialog>
</template>

<style scoped>
.tt-form { display: flex; flex-direction: column; gap: 18px; }
.tt-row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.tt-row:has(> :nth-child(2):last-child) { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.tt-field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.tt-field > span { font-size: 12px; font-weight: 600; color: var(--muted); }
.tt-field input,
.tt-field select,
.tt-field textarea {
  width: 100%;
  min-height: 44px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 11px;
  background: var(--surface);
  color: var(--text);
  font-size: 14px;
}
.tt-field textarea { resize: vertical; }
.tt-field input:focus,
.tt-field select:focus,
.tt-field textarea:focus {
  outline: 2px solid color-mix(in srgb, var(--focus) 55%, transparent);
  outline-offset: 1px;
  border-color: var(--focus);
}
.tt-slot {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--border);
  border-radius: 14px;
  background: color-mix(in srgb, var(--canvas) 55%, var(--surface));
}
.tt-slot-head { display: flex; align-items: center; justify-content: space-between; }
.tt-slot-head strong { font-size: 13px; }
.tt-slot-remove { border: 0; background: transparent; color: var(--muted); font-size: 12px; text-decoration: underline; }
.tt-add-slot {
  min-height: 40px;
  border: 1px dashed var(--border);
  border-radius: 12px;
  background: transparent;
  color: var(--accent);
  font-weight: 600;
}
.tt-switch { display: flex; align-items: center; gap: 12px; cursor: pointer; }
.tt-switch > input { position: absolute; opacity: 0; pointer-events: none; }
.tt-switch-track {
  position: relative;
  width: 42px;
  height: 24px;
  flex-shrink: 0;
  border-radius: 999px;
  background: color-mix(in srgb, var(--muted) 30%, var(--surface));
  transition: background .16s ease;
}
.tt-switch-track > span {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--surface);
  box-shadow: 0 1px 3px rgb(0 0 0 / 18%);
  transition: transform .16s ease;
}
.tt-switch input:checked + .tt-switch-track { background: var(--accent); }
.tt-switch input:checked + .tt-switch-track > span { transform: translateX(18px); }
.tt-switch input:focus-visible + .tt-switch-track { outline: 2px solid var(--focus); outline-offset: 2px; }
.tt-switch-copy { font-size: 14px; font-weight: 550; display: flex; flex-direction: column; }
.tt-switch-copy small { font-size: 11px; font-weight: 400; color: var(--muted); }
.tt-weekdays { display: flex; gap: 6px; margin: 0; padding: 0; border: 0; }
.tt-weekdays legend { padding: 0; margin-bottom: 8px; font-size: 12px; font-weight: 600; color: var(--muted); }
.tt-weekday {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--muted);
  font-size: 13px;
  font-weight: 550;
}
.tt-weekday.active { border-color: var(--accent); background: var(--accent); color: var(--on-accent); }
.tt-hint { margin: 0; min-height: 18px; font-size: 12px; color: var(--muted); }
.tt-error { margin: 0; font-size: 13px; color: var(--error); }
.tt-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 4px; }
.tt-button {
  min-height: 44px;
  padding: 0 20px;
  border-radius: 12px;
  font-size: 14px;
  font-weight: 600;
}
.tt-button.ghost { background: transparent; color: var(--muted); }
.tt-button.ghost:hover { background: var(--selected); color: var(--text); }
.tt-button.primary { background: var(--accent); color: var(--on-accent); }
.tt-button.primary:hover:not(:disabled) { background: var(--accent-hover); }
.tt-button:disabled { opacity: .45; }
@media (max-width: 560px) {
  .tt-row { grid-template-columns: 1fr 1fr; }
  .tt-row:first-of-type { grid-template-columns: 1fr; }
}
</style>