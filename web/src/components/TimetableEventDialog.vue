<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import {
  isoWeekday,
  weekdayShortLabels,
} from '../lib/timetable'

export interface EventFormValues {
  name: string
  kind: 'course' | 'fixed'
  location: string
  date: string
  startTime: string
  endTime: string
  repeat: boolean
  weekdays: number[]
}

const props = defineProps<{
  modelValue: boolean
  mode: 'create' | 'edit'
  initial: EventFormValues
  busy: boolean
  error: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [values: EventFormValues]
}>()

const form = reactive<EventFormValues>({ ...props.initial })
const touchedWeekdays = ref(false)

const weekLabels = weekdayShortLabels()

const valid = computed(() => (
  form.name.trim().length > 0
  && /^\d{2}:\d{2}$/.test(form.startTime)
  && /^\d{2}:\d{2}$/.test(form.endTime)
  && form.endTime > form.startTime
  && (!form.repeat || form.weekdays.length > 0)
))

const validationMessage = computed(() => {
  if (!form.name.trim()) return '给这节课或安排起个名字。'
  if (!form.startTime || !form.endTime) return '需要开始和结束时间。'
  if (form.endTime <= form.startTime) return '结束时间需要晚于开始时间。'
  if (form.repeat && !form.weekdays.length) return '每周重复至少要选一天。'
  return ''
})

function reset() {
  Object.assign(form, props.initial)
  touchedWeekdays.value = false
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

function onDateChange() {
  if (!form.date || touchedWeekdays.value || !form.repeat) return
  form.weekdays = [isoWeekday(form.date)]
}

watch(() => form.kind, value => {
  if (value === 'course') form.repeat = true
})

function toggleWeekday(day: number) {
  touchedWeekdays.value = true
  if (form.weekdays.includes(day)) {
    form.weekdays = form.weekdays.filter(item => item !== day)
  } else {
    form.weekdays = [...form.weekdays, day].sort((left, right) => left - right)
  }
}

function close() {
  emit('update:modelValue', false)
}

function submit() {
  if (!valid.value || props.busy) return
  emit('submit', {
    ...form,
    name: form.name.trim(),
    location: form.location.trim(),
    weekdays: [...form.weekdays].sort((left, right) => left - right),
  })
}
</script>

<template>
  <ElDialog
    :model-value="modelValue"
    :title="mode === 'create' ? '添加课程或安排' : '编辑安排'"
    width="min(540px, 94vw)"
    align-center
    class="tt-dialog"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <form class="tt-form" @submit.prevent="submit">
      <label class="tt-field">
        <span>{{ form.kind === 'course' ? '课程名称' : '安排名称' }}</span>
        <input
          v-model="form.name"
          type="text"
          maxlength="80"
          placeholder="例如：高等数学 / 健身"
          autocomplete="off"
        >
      </label>

      <div class="tt-row">
        <label class="tt-field">
          <span>类型</span>
          <select v-model="form.kind">
            <option value="course">课程</option>
            <option value="fixed">固定安排</option>
          </select>
        </label>
        <label class="tt-field">
          <span>地点</span>
          <input
            v-model="form.location"
            type="text"
            maxlength="120"
            placeholder="教学楼 / 线上 / 可选"
            autocomplete="off"
          >
        </label>
      </div>

      <div class="tt-row">
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
        <input v-model="form.repeat" type="checkbox" :disabled="form.kind === 'course'">
        <span class="tt-switch-track" aria-hidden="true"><span /></span>
        <span class="tt-switch-copy">
          每周重复
          <small>{{ form.kind === 'course' ? '课程按所选星期每周上课' : '适合固定安排' }}</small>
        </span>
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
        >
          {{ label }}
        </button>
      </fieldset>

      <p v-if="validationMessage" class="tt-hint" role="status">
        {{ validationMessage }}
      </p>
      <p v-if="error" class="tt-error" role="alert">{{ error }}</p>

      <div class="tt-actions">
        <button type="button" class="tt-button ghost" :disabled="busy" @click="close">
          取消
        </button>
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
.tt-field select {
  width: 100%;
  min-height: 44px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 11px;
  background: var(--surface);
  color: var(--text);
  font-size: 14px;
}
.tt-field input:focus,
.tt-field select:focus {
  outline: 2px solid color-mix(in srgb, var(--focus) 55%, transparent);
  outline-offset: 1px;
  border-color: var(--focus);
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
.tt-weekdays {
  display: flex;
  gap: 6px;
  margin: 0;
  padding: 0;
  border: 0;
}
.tt-weekdays legend {
  padding: 0;
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
}
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
.tt-weekday.active {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--on-accent);
}
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
