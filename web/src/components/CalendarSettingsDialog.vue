<script setup lang="ts">
import { nextTick, reactive, ref, watch } from 'vue'
import { Clock3, Pencil, Plus, Trash2 } from 'lucide-vue-next'
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

const props = defineProps<{
  open: boolean
  settings: TimeSettings | null
  terms: Term[]
  browserTimeZone: string
}>()

const emit = defineEmits<{
  close: []
  changed: [message: string]
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
            <input v-model.number="period.period_number" :aria-label="`节次 ${index + 1} 编号`"
              type="number" min="1" max="99" :disabled="pendingTerm" />
            <input v-model="period.name" :aria-label="`节次 ${index + 1} 名称`"
              maxlength="80" :disabled="pendingTerm" />
            <input v-model="period.starts_at" :aria-label="`节次 ${index + 1} 开始时间`"
              type="time" :disabled="pendingTerm" />
            <span aria-hidden="true">至</span>
            <input v-model="period.ends_at" :aria-label="`节次 ${index + 1} 结束时间`"
              type="time" :disabled="pendingTerm" />
            <button type="button" class="small-icon-button danger-icon" :aria-label="`删除节次 ${index + 1}`"
              title="删除节次" :disabled="pendingTerm" @click="removePeriod(index)">
              <Trash2 :size="16" aria-hidden="true" />
            </button>
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
