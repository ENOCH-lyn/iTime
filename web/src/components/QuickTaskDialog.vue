<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { isoWeekday, weekdayShortLabels } from '../lib/timetable'

export type TaskRepeat = 'none' | 'daily' | 'weekdays' | 'weekly' | 'monthly' | 'yearly'

export interface TaskFormValues {
  title: string
  date: string
  repeat: TaskRepeat
  weekdays: number[]
  estimatedMinutes: number | null
  priority: number
  until: string | null
}

const props = defineProps<{
  modelValue: boolean
  mode?: 'create' | 'edit'
  defaultUntil?: string
  initial: {
    title: string
    date: string
    repeat?: TaskRepeat
    weekdays?: number[]
    estimatedMinutes?: number | null
    priority?: number
    until?: string | null
  }
  busy: boolean
  error: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  submit: [values: TaskFormValues]
}>()

const form = reactive({
  title: '',
  date: props.initial.date,
  repeat: 'none' as TaskRepeat,
  weekdays: [isoWeekday(props.initial.date)],
  estimatedMinutes: null as number | null,
  priority: 2,
  until: '',
})
const weekLabels = weekdayShortLabels()

const valid = computed(() => (
  form.title.trim().length > 0
  && /^\d{4}-\d{2}-\d{2}$/.test(form.date)
  && (form.repeat === 'none' || (
    /^\d{4}-\d{2}-\d{2}$/.test(form.until)
    && form.until >= form.date
  ))
  && (form.repeat !== 'weekly' || form.weekdays.length > 0)
))

function reset() {
  form.title = props.initial.title
  form.date = props.initial.date
  form.repeat = props.initial.repeat ?? 'none'
  form.weekdays = props.initial.weekdays?.length
    ? [...props.initial.weekdays]
    : [isoWeekday(props.initial.date)]
  form.estimatedMinutes = props.initial.estimatedMinutes ?? null
  form.priority = props.initial.priority ?? 2
  form.until = props.initial.until
    ?? props.defaultUntil
    ?? props.initial.date
}

watch(() => props.modelValue, value => {
  if (value) reset()
})

watch(() => form.date, value => {
  if (value && form.repeat === 'weekly' && form.weekdays.length === 0) {
    form.weekdays = [isoWeekday(value)]
  }
})

function toggleWeekday(day: number) {
  form.weekdays = form.weekdays.includes(day)
    ? form.weekdays.filter(item => item !== day)
    : [...form.weekdays, day].sort((left, right) => left - right)
}

function close() {
  emit('update:modelValue', false)
}

function submit() {
  if (!valid.value || props.busy) return
  emit('submit', {
    title: form.title.trim(),
    date: form.date,
    repeat: form.repeat,
    weekdays: [...form.weekdays].sort((left, right) => left - right),
    estimatedMinutes: form.estimatedMinutes,
    priority: form.priority,
    until: form.repeat === 'none' ? null : form.until,
  })
}
</script>

<template>
  <ElDialog
    :model-value="modelValue"
    :title="mode === 'edit' ? '编辑任务' : '新建任务'"
    width="min(500px, 94vw)"
    align-center
    class="tt-dialog"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <form id="tt-form" class="tt-form" @submit.prevent="submit">
      <label class="tt-field">
        <span>要做什么</span>
        <input
          v-model="form.title"
          type="text"
          maxlength="200"
          placeholder="例如：写完高数作业"
          autocomplete="off"
          autofocus
        >
      </label>

      <div class="tt-row">
        <label class="tt-field">
          <span>日期</span>
          <input v-model="form.date" type="date">
        </label>
        <label class="tt-field">
          <span>重复</span>
          <select v-model="form.repeat">
            <option value="none">不重复</option>
            <option value="daily">每天</option>
            <option value="weekdays">每个工作日</option>
            <option value="weekly">每周</option>
            <option value="monthly">每月</option>
            <option value="yearly">每年</option>
          </select>
        </label>
        <label class="tt-field">
          <span>预计用时</span>
          <select v-model="form.estimatedMinutes">
            <option :value="null">不设定</option>
            <option :value="15">15 分钟</option>
            <option :value="30">30 分钟</option>
            <option :value="45">45 分钟</option>
            <option :value="60">1 小时</option>
            <option :value="90">1.5 小时</option>
            <option :value="120">2 小时</option>
          </select>
        </label>
        <label class="tt-field">
          <span>优先级</span>
          <select v-model.number="form.priority">
            <option :value="1">低</option>
            <option :value="2">中</option>
            <option :value="3">高</option>
          </select>
        </label>
      </div>

      <fieldset v-if="form.repeat === 'weekly'" class="tt-weekdays">
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

      <label v-if="form.repeat !== 'none'" class="tt-field">
        <span>重复到</span>
        <input v-model="form.until" type="date" :min="form.date">
      </label>

      <p v-if="error" class="tt-error" role="alert">{{ error }}</p>

    </form>

    <template #footer>
      <div class="tt-actions">
        <button type="button" class="tt-button ghost" :disabled="busy" @click="close">
          取消
        </button>
        <button type="submit" form="tt-form" class="tt-button primary" :disabled="!valid || busy">
          {{ busy ? '保存中…' : (mode === 'edit' ? '保存修改' : '创建任务') }}
        </button>
      </div>
    </template>
  </ElDialog>
</template>

<style scoped>
.tt-form { display: flex; flex-direction: column; gap: 18px; }
.tt-row { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
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
.tt-error { margin: 0; font-size: 13px; color: var(--error); }
.tt-actions { display: flex; justify-content: flex-end; gap: 10px; }
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
}
/* Material 3 dialog surface */
:global(.tt-dialog.el-dialog) {
  width: min(520px, calc(100vw - 32px));
  padding: 24px;
  border-radius: var(--m3-shape-xl);
  background: var(--m3-surface-container-high);
  box-shadow: var(--m3-elevation-3);
}
:global(.tt-dialog .el-dialog__header) {
  padding: 0 0 12px;
  margin: 0;
}
:global(.tt-dialog .el-dialog__title) {
  color: var(--m3-on-surface);
  font: 500 22px/28px var(--font);
  letter-spacing: 0;
}
:global(.tt-dialog .el-dialog__headerbtn) {
  top: 12px;
  right: 12px;
  width: 40px;
  height: 40px;
  border-radius: var(--m3-shape-full);
}
:global(.tt-dialog .el-dialog__headerbtn:hover) {
  background: color-mix(in srgb, var(--m3-on-surface) 8%, transparent);
}
:global(.tt-dialog .el-dialog__headerbtn .el-dialog__close) {
  color: var(--m3-on-surface-variant);
  font-size: 20px;
}
:global(.tt-dialog .el-dialog__body) {
  max-height: calc(100dvh - 180px);
  padding: 0;
  overflow-y: auto;
}

.tt-form {
  gap: 16px;
}
.tt-row {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 16px;
}
.tt-field > span {
  color: var(--m3-on-surface-variant);
  font: 500 12px/16px var(--font);
  letter-spacing: 0.5px;
}
.tt-field input,
.tt-field select {
  min-height: 56px;
  padding: 0 16px;
  border: 1px solid var(--m3-outline);
  border-radius: var(--m3-shape-xs);
  background: var(--m3-surface-container-highest);
  color: var(--m3-on-surface);
  font: 400 14px/20px var(--font);
  outline: 0;
}
.tt-field input:focus,
.tt-field select:focus {
  outline: none;
  border-color: var(--m3-primary);
  box-shadow: inset 0 0 0 1px var(--m3-primary);
}
.tt-weekdays {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 6px;
  padding-top: 8px;
}
.tt-weekdays legend {
  grid-column: 1 / -1;
  margin-bottom: 0;
  color: var(--m3-on-surface-variant);
  font: 500 12px/16px var(--font);
  letter-spacing: 0.5px;
}
.tt-weekday {
  width: 100%;
  height: 40px;
  border: 1px solid var(--m3-outline);
  border-radius: var(--m3-shape-full);
  background: transparent;
  color: var(--m3-on-surface-variant);
  font: 500 13px/18px var(--font);
  cursor: pointer;
}
.tt-weekday.active {
  border-color: transparent;
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
}
.tt-error {
  margin: 0;
  color: var(--m3-error);
  font: 400 12px/16px var(--font);
}
.tt-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}
.tt-button {
  min-height: 40px;
  padding: 0 24px;
  border: 1px solid transparent;
  border-radius: var(--m3-shape-full);
  font: 500 14px/20px var(--font);
  letter-spacing: 0.1px;
  cursor: pointer;
}
.tt-button.primary {
  background: var(--m3-primary);
  color: var(--m3-on-primary);
}
.tt-button.primary:disabled {
  background: var(--m3-surface-container-highest);
  color: var(--m3-on-surface-variant);
  opacity: 1;
  cursor: not-allowed;
}
.tt-button.ghost {
  background: transparent;
  color: var(--m3-primary);
}
.tt-button.ghost:disabled {
  color: var(--m3-on-surface-variant);
  opacity: 1;
  cursor: not-allowed;
}
@media (max-width: 600px) {
  :global(.tt-dialog.el-dialog) {
    width: calc(100vw - 24px);
    padding: 20px;
  }
  :global(.tt-dialog .el-dialog__body) {
    max-height: calc(100dvh - 200px);
  }
  .tt-row {
    grid-template-columns: minmax(0, 1fr);
  }
}
:global(.tt-dialog .el-dialog__footer) {
  flex: 0 0 auto;
  padding: 0 24px 24px;
}
:global(.tt-dialog .el-dialog__body) {
  flex: 1 1 auto;
  min-height: 0;
  max-height: none;
  overflow-y: auto;
}
:global(.tt-dialog.el-dialog) {
  max-height: calc(100dvh - 24px);
  display: flex;
  flex-direction: column;
}
.tt-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 12px;
  border-top: 1px solid var(--m3-outline-variant);
}
</style>
