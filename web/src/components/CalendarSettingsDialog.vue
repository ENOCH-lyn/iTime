<script setup lang="ts">
import { nextTick, reactive, ref, watch } from 'vue'
import { Clock3, Pencil, Plus, Trash2, X } from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import { failure, localFailure, type Failure } from '../lib/api'
import {
  getTerm,
  replaceTermPeriods,
  saveTerm,
  saveTimeSettings,
  type Term,
  type TermPeriod,
  type TimeSettings,
} from '../lib/calendar'

const props = withDefaults(defineProps<{
  open: boolean
  settings: TimeSettings | null
  terms: Term[]
  browserTimeZone: string
  showTeacher?: boolean
  showDisplaySettings?: boolean
}>(), {
  showTeacher: true,
  showDisplaySettings: false,
})

const emit = defineEmits<{
  close: []
  changed: [message: string]
  'update:show-teacher': [value: boolean]
}>()

const dialog = ref<HTMLDialogElement | null>(null)
const pendingZone = ref(false)
const pendingTerm = ref(false)
const error = ref<Failure | null>(null)
const success = ref('')
const zone = ref('')
const editingTerm = ref<Term | null>(null)
const termFormOpen = ref(false)
const values = reactive({
  name: '',
  startDate: '',
  endDate: '',
  firstWeekMonday: '',
  periods: [] as TermPeriod[],
})
let zoneOperation: { id: string; fingerprint: string } | null = null
let termOperation: { id: string; fingerprint: string } | null = null
let periodsOperation: { id: string; fingerprint: string } | null = null

watch(() => props.open, async open => {
  if (!open) {
    dialog.value?.close()
    return
  }
  zone.value = props.settings?.time_zone ?? props.browserTimeZone
  error.value = null
  success.value = ''
  termFormOpen.value = false
  editingTerm.value = null
  zoneOperation = null
  termOperation = null
  periodsOperation = null
  await nextTick()
  dialog.value?.showModal()
})

function operationId(
  current: { id: string; fingerprint: string } | null,
  fingerprint: string,
) {
  return current?.fingerprint === fingerprint
    ? current
    : { id: crypto.randomUUID(), fingerprint }
}

function mondayOnOrBefore(value: string) {
  if (!value) return ''
  const [year, month, day] = value.split('-').map(Number)
  const date = new Date(year, month - 1, day, 12)
  const weekday = date.getDay() || 7
  date.setDate(date.getDate() - weekday + 1)
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

function close() {
  if (pendingZone.value || pendingTerm.value) return
  dialog.value?.close()
  emit('close')
}

async function saveZone() {
  if (!props.settings || pendingZone.value) return
  if (!zone.value.trim()) {
    error.value = localFailure('请输入 IANA 时区名称。')
    return
  }
  error.value = null
  success.value = ''
  const fingerprint = JSON.stringify({
    version: props.settings.version,
    time_zone: zone.value.trim(),
  })
  zoneOperation = operationId(zoneOperation, fingerprint)
  pendingZone.value = true
  try {
    await saveTimeSettings(props.settings, zone.value.trim(), zoneOperation.id)
    zoneOperation = null
    success.value = '当前时区已更新；既有安排仍保留各自保存的时区。'
    emit('changed', success.value)
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    pendingZone.value = false
  }
}

function startTerm() {
  editingTerm.value = null
  values.name = ''
  values.startDate = ''
  values.endDate = ''
  values.firstWeekMonday = ''
  values.periods = [
    { period_number: 1, name: '第一节', starts_at: '08:00', ends_at: '08:45' },
  ]
  error.value = null
  success.value = ''
  termOperation = null
  periodsOperation = null
  termFormOpen.value = true
  void nextTick(() => document.getElementById('term-name')?.focus())
}

async function editTerm(term: Term) {
  error.value = null
  success.value = ''
  pendingTerm.value = true
  try {
    const detail = await getTerm(term.id)
    editingTerm.value = detail
    values.name = detail.name
    values.startDate = detail.start_date
    values.endDate = detail.end_date
    values.firstWeekMonday = detail.first_week_monday
    values.periods = (detail.periods ?? []).map(period => ({
      ...period,
      starts_at: period.starts_at.slice(0, 5),
      ends_at: period.ends_at.slice(0, 5),
    }))
    termOperation = null
    periodsOperation = null
    termFormOpen.value = true
    await nextTick()
    document.getElementById('term-name')?.focus()
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    pendingTerm.value = false
  }
}

function updateStartDate(value: string) {
  values.startDate = value
  if (!editingTerm.value || !values.firstWeekMonday) {
    values.firstWeekMonday = mondayOnOrBefore(value)
  }
}

function addPeriod() {
  const previous = values.periods.at(-1)
  const number = Math.max(0, ...values.periods.map(period => period.period_number)) + 1
  values.periods.push({
    period_number: number,
    name: `第${number}节`,
    starts_at: previous?.ends_at.slice(0, 5) ?? '08:00',
    ends_at: previous?.ends_at.slice(0, 5) ?? '08:45',
  })
}

function removePeriod(index: number) {
  values.periods.splice(index, 1)
}

async function saveTermForm() {
  if (pendingTerm.value) return
  if (!values.name.trim() || !values.startDate || !values.endDate || !values.firstWeekMonday) {
    error.value = localFailure('请填写完整的学期名称和日期。')
    return
  }
  if (values.periods.some(period => (
    !period.name.trim() || !period.starts_at || !period.ends_at
  ))) {
    error.value = localFailure('请填写完整的节次名称与时间。')
    return
  }
  error.value = null
  success.value = ''
  const termFields = {
    name: values.name.trim(),
    start_date: values.startDate,
    end_date: values.endDate,
    first_week_monday: values.firstWeekMonday,
  }
  const termFingerprint = JSON.stringify({
    id: editingTerm.value?.id,
    version: editingTerm.value?.version,
    fields: termFields,
  })
  termOperation = operationId(termOperation, termFingerprint)
  pendingTerm.value = true
  try {
    let saved = await saveTerm(
      editingTerm.value,
      termFields,
      zone.value || props.browserTimeZone,
      termOperation.id,
    )
    termOperation = null
    editingTerm.value = saved
    const normalizedPeriods = values.periods.map(period => ({
      period_number: Number(period.period_number),
      name: period.name.trim(),
      starts_at: period.starts_at,
      ends_at: period.ends_at,
    }))
    const periodFingerprint = JSON.stringify({
      id: saved.id,
      version: saved.version,
      periods: normalizedPeriods,
    })
    periodsOperation = operationId(periodsOperation, periodFingerprint)
    saved = await replaceTermPeriods(saved, normalizedPeriods, periodsOperation.id)
    periodsOperation = null
    editingTerm.value = saved
    success.value = '学期与节次已保存。'
    termFormOpen.value = false
    emit('changed', success.value)
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    pendingTerm.value = false
  }
}
</script>

<template>
  <dialog id="calendar-settings-dialog" ref="dialog" class="calendar-dialog calendar-settings-dialog"
    aria-labelledby="calendar-settings-title" @cancel.prevent="close">
    <div class="calendar-dialog-heading">
      <Clock3 :size="22" aria-hidden="true" />
      <div>
        <h2 id="calendar-settings-title">时间与学期</h2>
        <p>日历按当前时区查询；课程可使用学期周次和连续节次。</p>
      </div>
      <button type="button" class="calendar-settings-close" aria-label="关闭" :disabled="pendingZone || pendingTerm" @click="close">
        <X :size="18" aria-hidden="true" />
      </button>
    </div>

    <StatusMessage :error="error" :success="success" />

    <section class="calendar-settings-section">
      <div class="settings-section-heading">
        <div>
          <h3>当前时区</h3>
          <p>默认来自本浏览器。修改后不会重写既有安排的时区。</p>
        </div>
        <button type="button" class="button secondary compact-button" :disabled="pendingZone" @click="zone = browserTimeZone">
          使用本机时区
        </button>
      </div>
      <form class="timezone-form" @submit.prevent="saveZone">
        <div class="field">
          <label for="calendar-time-zone">IANA 时区</label>
          <input id="calendar-time-zone" v-model="zone" autocomplete="off" :disabled="pendingZone" />
        </div>
        <button type="submit" class="button compact-button" :disabled="pendingZone">
          {{ pendingZone ? '正在保存…' : '保存时区' }}
        </button>
      </form>
    </section>

    <section v-if="showDisplaySettings" class="calendar-settings-section">
      <div class="settings-section-heading">
        <div>
          <h3>显示设置</h3>
          <p>控制课程块上显示的次要信息，不影响课表数据。</p>
        </div>
      </div>
      <label class="settings-toggle">
        <input
          type="checkbox"
          :checked="showTeacher"
          @change="emit('update:show-teacher', ($event.target as HTMLInputElement).checked)"
        >
        <span class="settings-toggle-track" aria-hidden="true"><span /></span>
        <span>在课程块显示教师</span>
      </label>
    </section>

    <section class="calendar-settings-section">
      <div class="settings-section-heading">
        <div>
          <h3>学期与节次</h3>
          <p>同一账号可保留多个学期，已有课程不会随节次表改动而移动。</p>
        </div>
        <button type="button" class="button secondary compact-button" :disabled="pendingTerm" @click="startTerm">
          <Plus :size="16" aria-hidden="true" />新建学期
        </button>
      </div>

      <ul v-if="terms.length" class="term-list">
        <li v-for="term in terms" :key="term.id">
          <div>
            <strong>{{ term.name }}</strong>
            <p>{{ term.start_date }} 至 {{ term.end_date }} · {{ term.period_count }} 个节次</p>
          </div>
          <button type="button" class="small-icon-button" :aria-label="`编辑${term.name}`"
            :title="`编辑${term.name}`" :disabled="pendingTerm" @click="editTerm(term)">
            <Pencil :size="16" aria-hidden="true" />
          </button>
        </li>
      </ul>
      <p v-else class="calendar-settings-empty">尚未创建学期。新建课程前需要先设置学期和节次。</p>

      <form v-if="termFormOpen" class="term-form" novalidate @submit.prevent="saveTermForm">
        <h3>{{ editingTerm ? '编辑学期' : '新建学期' }}</h3>
        <div class="calendar-form-grid">
          <div class="field span-two">
            <label for="term-name">学期名称</label>
            <input id="term-name" v-model="values.name" maxlength="120" :disabled="pendingTerm" />
          </div>
          <div class="field">
            <label for="term-start-date">开始日期</label>
            <input id="term-start-date" :value="values.startDate" type="date" :disabled="pendingTerm"
              @input="updateStartDate(($event.target as HTMLInputElement).value)" />
          </div>
          <div class="field">
            <label for="term-end-date">结束日期</label>
            <input id="term-end-date" v-model="values.endDate" type="date" :disabled="pendingTerm" />
          </div>
          <div class="field">
            <label for="term-first-monday">第一周周一</label>
            <input id="term-first-monday" v-model="values.firstWeekMonday" type="date" :disabled="pendingTerm" />
          </div>
        </div>

        <div class="period-heading">
          <div>
            <h3>节次表</h3>
            <p>相邻节次可以首尾衔接，但不能重叠。</p>
          </div>
          <button type="button" class="button secondary compact-button" :disabled="pendingTerm" @click="addPeriod">
            <Plus :size="16" aria-hidden="true" />添加节次
          </button>
        </div>
        <ol class="period-list">
          <li v-for="(period, index) in values.periods" :key="index">
            <div class="period-main">
              <input v-model.number="period.period_number" class="period-number" :aria-label="`节次 ${index + 1} 编号`"
                type="number" min="1" max="99" :disabled="pendingTerm" />
              <input v-model="period.name" class="period-name" :aria-label="`节次 ${index + 1} 名称`"
                maxlength="80" :disabled="pendingTerm" />
              <button type="button" class="small-icon-button danger-icon" :aria-label="`删除节次 ${index + 1}`"
                title="删除节次" :disabled="pendingTerm" @click="removePeriod(index)">
                <Trash2 :size="16" aria-hidden="true" />
              </button>
            </div>
            <div class="period-times">
              <label>
                <span>开始</span>
                <input v-model="period.starts_at" :aria-label="`节次 ${index + 1} 开始时间`"
                  type="time" :disabled="pendingTerm" />
              </label>
              <span class="period-sep" aria-hidden="true">—</span>
              <label>
                <span>结束</span>
                <input v-model="period.ends_at" :aria-label="`节次 ${index + 1} 结束时间`"
                  type="time" :disabled="pendingTerm" />
              </label>
            </div>
          </li>
        </ol>
        <div class="dialog-actions">
          <button type="button" class="button secondary" :disabled="pendingTerm" @click="termFormOpen = false">取消编辑</button>
          <button type="submit" class="button" :disabled="pendingTerm">
            {{ pendingTerm ? '正在保存…' : '保存学期与节次' }}
          </button>
        </div>
      </form>
    </section>

    <div class="dialog-actions">
      <button type="button" class="button secondary" :disabled="pendingZone || pendingTerm" @click="close">完成</button>
    </div>
  </dialog>
</template>

<style scoped>
dialog.calendar-dialog {
  width: min(780px, 94vw);
  max-width: min(780px, 94vw);
  max-height: min(90dvh, 900px);
  margin: auto;
  padding: 0;
  border: 1px solid color-mix(in srgb, var(--border) 80%, transparent);
  border-radius: 24px;
  background: var(--surface);
  color: var(--text);
  box-shadow: 0 30px 90px -32px rgb(18 30 24 / 55%);
  overflow-y: auto;
  overscroll-behavior: contain;
}
dialog.calendar-dialog::backdrop {
  background: rgb(18 30 24 / 42%);
  backdrop-filter: blur(4px);
}
.calendar-dialog-heading {
  position: sticky;
  top: 0;
  z-index: 2;
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 24px 28px 18px;
  background: color-mix(in srgb, var(--surface) 94%, transparent);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid color-mix(in srgb, var(--border) 65%, transparent);
}
.calendar-dialog-heading > svg {
  flex: 0 0 auto;
  width: 38px;
  height: 38px;
  padding: 9px;
  border-radius: 12px;
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, var(--surface));
}
.calendar-settings-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  margin-left: auto;
  border: 1px solid color-mix(in srgb, var(--border) 70%, transparent);
  border-radius: 9px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
}
.calendar-settings-close:hover { background: var(--selected); color: var(--text); }
.calendar-settings-close:disabled { opacity: .4; }
.calendar-dialog-heading h2 {
  margin: 0;
  font-size: 20px;
  letter-spacing: -.02em;
}
.calendar-dialog-heading p {
  margin: 5px 0 0;
  color: var(--muted);
  font-size: 12.5px;
  line-height: 1.55;
}
.calendar-settings-section {
  margin: 18px 28px;
  padding: 18px;
  border: 1px solid color-mix(in srgb, var(--border) 72%, transparent);
  border-radius: 16px;
  background: color-mix(in srgb, var(--canvas) 42%, var(--surface));
}
.settings-section-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}
.settings-section-heading h3 {
  margin: 0;
  font-size: 15px;
  letter-spacing: -.01em;
}
.settings-section-heading p {
  margin: 4px 0 0;
  color: var(--muted);
  font-size: 12px;
  line-height: 1.55;
}
.timezone-form {
  display: flex;
  align-items: flex-end;
  gap: 10px;
}
.timezone-form .field { flex: 1 1 auto; }
.field { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.field label {
  color: var(--muted);
  font-size: 12px;
  font-weight: 600;
}
.field input,
.field select,
.period-list input {
  width: 100%;
  min-height: 42px;
  padding: 9px 11px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text);
  font: inherit;
  font-size: 13.5px;
}
.field input:focus,
.field select:focus,
.period-list input:focus {
  outline: 2px solid color-mix(in srgb, var(--focus) 50%, transparent);
  outline-offset: 1px;
  border-color: var(--focus);
}
.button,
.compact-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  width: auto;
  min-height: 40px;
  padding: 0 14px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text);
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
}
.button:not(.secondary) {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--on-accent);
}
.button.secondary { color: var(--muted); }
.button:disabled { opacity: .45; cursor: default; }
.term-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.term-list li {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid color-mix(in srgb, var(--border) 70%, transparent);
  border-radius: 12px;
  background: var(--surface);
}
.term-list strong { font-size: 13.5px; }
.term-list p { margin: 4px 0 0; color: var(--muted); font-size: 11.5px; }
.small-icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex: 0 0 auto;
  border: 1px solid var(--border);
  border-radius: 9px;
  background: transparent;
  color: var(--muted);
  cursor: pointer;
}
.small-icon-button:hover { background: var(--selected); color: var(--text); }
.term-form {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
}
.term-form > h3 { margin: 0 0 12px; font-size: 15px; }
.calendar-form-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.calendar-form-grid .span-two { grid-column: span 2; }
.period-heading {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  margin-top: 10px;
}
.period-heading h3 { margin: 0; font-size: 14px; }
.period-heading p { margin: 4px 0 0; color: var(--muted); font-size: 11.5px; }
.period-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}
.period-list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid color-mix(in srgb, var(--border) 70%, transparent);
  border-radius: 12px;
  background: var(--surface);
}
.period-main { display: flex; align-items: center; gap: 6px; flex: 1 1 auto; min-width: 0; }
.period-list .period-main .period-number {
  width: 52px;
  min-width: 52px;
  flex: 0 0 52px;
}
.period-list .period-main .period-name {
  flex: 1 1 auto;
  width: auto;
  min-width: 0;
}
.period-number { flex: 0 0 auto; }
.period-name { flex: 1 1 auto; min-width: 0; }
.period-times { display: flex; align-items: center; gap: 6px; flex: 0 0 auto; }
.period-times label { display: flex; align-items: center; gap: 4px; color: var(--muted); font-size: 11px; }
.period-times input { width: 104px; }
.period-sep { color: var(--muted); font-size: 10px; }
.period-list input[type="number"] { text-align: center; padding-inline: 4px; }
.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 6px 28px 24px;
}
.calendar-settings-empty {
  margin: 0;
  padding: 14px;
  border: 1px dashed var(--border);
  border-radius: 12px;
  color: var(--muted);
  font-size: 12.5px;
  line-height: 1.6;
}
.settings-toggle {
  display: flex;
  align-items: center;
  gap: 11px;
  color: var(--text);
  font-size: 13px;
  font-weight: 550;
  cursor: pointer;
}
.settings-toggle input { position: absolute; opacity: 0; pointer-events: none; }
.settings-toggle-track {
  position: relative;
  width: 40px;
  height: 23px;
  flex: 0 0 auto;
  border-radius: 999px;
  background: color-mix(in srgb, var(--muted) 32%, var(--surface));
  transition: background .16s ease;
}
.settings-toggle-track > span {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 17px;
  height: 17px;
  border-radius: 50%;
  background: var(--surface);
  box-shadow: 0 1px 3px rgb(0 0 0 / 18%);
  transition: transform .16s ease;
}
.settings-toggle input:checked + .settings-toggle-track { background: var(--accent); }
.settings-toggle input:checked + .settings-toggle-track > span { transform: translateX(17px); }
.settings-toggle input:focus-visible + .settings-toggle-track { outline: 2px solid var(--focus); outline-offset: 2px; }
@media (max-width: 640px) {
  dialog.calendar-dialog {
    width: 100vw;
    max-width: 100vw;
    height: 100dvh;
    max-height: 100dvh;
    border-radius: 0;
    border: 0;
  }
  .calendar-dialog-heading { padding: 18px 16px 14px; }
  .calendar-dialog-heading > svg { width: 34px; height: 34px; padding: 8px; }
  .calendar-dialog-heading h2 { font-size: 18px; }
  .calendar-settings-section { margin: 12px 16px; padding: 14px; }
  .settings-section-heading { flex-direction: column; gap: 10px; }
  .settings-section-heading .button { align-self: flex-start; }
  .timezone-form { flex-direction: column; align-items: stretch; }
  .timezone-form .button { align-self: flex-start; }
  .calendar-form-grid { grid-template-columns: 1fr; }
  .calendar-form-grid .span-two { grid-column: auto; }
  .period-list li {
    flex-direction: column;
    align-items: stretch;
    gap: 10px;
    padding: 12px;
  }
  .period-main { width: 100%; }
  .period-times {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: end;
    width: 100%;
  }
  .period-times label { flex-direction: column; align-items: flex-start; gap: 4px; }
  .period-times input { width: 100%; }
  .period-sep { padding-bottom: 12px; }
  .dialog-actions { padding: 4px 16px 20px; }
}
</style>
