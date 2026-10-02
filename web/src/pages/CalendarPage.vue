<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  CalendarPlus,
  ChevronLeft,
  ChevronRight,
  CirclePause,
  Filter,
  RefreshCw,
} from 'lucide-vue-next'
import CalendarGrid from '../components/CalendarGrid.vue'
import CalendarImportDialog from '../components/CalendarImportDialog.vue'
import CalendarNavigation from '../components/CalendarNavigation.vue'
import CalendarOccurrencePanel from '../components/CalendarOccurrencePanel.vue'
import CalendarSeriesDialog from '../components/CalendarSeriesDialog.vue'
import CalendarSettingsDialog from '../components/CalendarSettingsDialog.vue'
import StatusMessage from '../components/StatusMessage.vue'
import { api, failure, type Failure } from '../lib/api'
import {
  getSeries,
  getTimeSettings,
  listCalendarOccurrences,
  listSeries,
  listTerms,
  saveTimeSettings,
  type CalendarDisplayItem,
  type CalendarOccurrence,
  type CalendarSeries,
  type Term,
  type TimeSettings,
} from '../lib/calendar'
import {
  authError,
  clearSession,
  identity,
  notifySessionChange,
  refreshSession,
} from '../lib/auth'
import { listTaskOccurrences } from '../lib/task-schedules'
import '../calendar.css'

const router = useRouter()
const browserTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
const view = ref<'week' | 'month'>('week')
const anchor = ref(dateInZone(new Date(), browserTimeZone))
const jumpDate = ref(anchor.value)
const settings = ref<TimeSettings | null>(null)
const terms = ref<Term[]>([])
const series = ref<CalendarSeries[]>([])
const calendarItems = ref<CalendarDisplayItem[]>([])
const selected = ref<CalendarDisplayItem | null>(null)
const loading = ref(false)
const loaded = ref(false)
const pendingLogout = ref(false)
const pageError = ref<Failure | null>(null)
const pageSuccess = ref('')
const importOpen = ref(false)
const settingsOpen = ref(false)
const seriesOpen = ref(false)
const editingSeries = ref<CalendarSeries | null>(null)
const splittingOccurrence = ref<CalendarOccurrence | null>(null)
const filtersOpen = ref(false)
const showTasks = ref(true)
const showCourses = ref(true)
const showFixed = ref(true)
const showCancelled = ref(true)
let loadRevision = 0
let anchorTouched = false

function parseLocalDate(value: string) {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day, 12)
}

function localDate(value: Date) {
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`
}

function dateInZone(value: Date, timeZone: string) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(value)
  const part = (type: Intl.DateTimeFormatPartTypes) => (
    parts.find(candidate => candidate.type === type)?.value ?? ''
  )
  return `${part('year')}-${part('month')}-${part('day')}`
}

function instantInZone(value: string, timeZone: string) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(value))
  const part = (type: Intl.DateTimeFormatPartTypes) => (
    parts.find(candidate => candidate.type === type)?.value ?? ''
  )
  return {
    date: `${part('year')}-${part('month')}-${part('day')}`,
    time: `${part('hour')}:${part('minute')}:${part('second')}`,
  }
}

function validDate(value: string) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value)
    && localDate(parseLocalDate(value)) === value
}

function addDays(value: string, amount: number) {
  const date = parseLocalDate(value)
  date.setDate(date.getDate() + amount)
  return localDate(date)
}

function startOfWeek(value: string) {
  const date = parseLocalDate(value)
  const weekday = date.getDay() || 7
  date.setDate(date.getDate() - weekday + 1)
  return localDate(date)
}

function monthGridStart(value: string) {
  const date = parseLocalDate(value)
  date.setDate(1)
  return startOfWeek(localDate(date))
}

const days = computed(() => {
  const start = view.value === 'week' ? startOfWeek(anchor.value) : monthGridStart(anchor.value)
  return Array.from({ length: view.value === 'week' ? 7 : 42 }, (_, index) => addDays(start, index))
})
const range = computed(() => ({
  start: days.value[0],
  end: addDays(days.value.at(-1) ?? days.value[0], 1),
}))
const today = computed(() => dateInZone(
  new Date(),
  settings.value?.time_zone ?? browserTimeZone,
))
const monthKey = computed(() => anchor.value.slice(0, 7))
const title = computed(() => {
  const first = parseLocalDate(days.value[0])
  const last = parseLocalDate(days.value.at(-1) ?? days.value[0])
  const formatter = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long' })
  if (view.value === 'month') return formatter.format(parseLocalDate(anchor.value))
  const firstLabel = new Intl.DateTimeFormat('zh-CN', {
    year: first.getFullYear() === last.getFullYear() ? undefined : 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(first)
  const lastLabel = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(last)
  return `${firstLabel} - ${lastLabel}`
})
const visibleItems = computed(() => calendarItems.value.filter(item => (
  (item.kind === 'task' ? showTasks.value : item.kind === 'course' ? showCourses.value : showFixed.value)
  && (showCancelled.value || !['cancelled', 'skipped'].includes(item.state))
)))
const selectedSeries = computed(() => {
  const id = selected.value?.calendar?.series_id
  return id ? series.value.find(item => item.id === id) ?? null : null
})
const detailOpen = computed(() => !!selected.value)

watch(authError, value => {
  if (value?.code === 'unauthenticated') void router.replace('/login')
})
watch([view, anchor], () => { void loadOccurrences() })
watch(anchor, value => { jumpDate.value = value })

onMounted(async () => {
  document.title = '日历 · iTime'
  await refreshSession()
  if (!identity.value) {
    await router.replace('/login')
    return
  }
  await initialize()
})

async function initialize() {
  loading.value = true
  pageError.value = null
  try {
    let current = await getTimeSettings()
    if (!current.initialized) {
      current = await saveTimeSettings(current, browserTimeZone)
    }
    settings.value = current
    if (!anchorTouched && !loaded.value) {
      anchor.value = dateInZone(new Date(), current.time_zone ?? browserTimeZone)
    }
    const [termItems, seriesItems] = await Promise.all([listTerms(), listSeries()])
    terms.value = termItems
    series.value = seriesItems
    await loadOccurrences()
  } catch (reason) {
    pageError.value = failure(reason)
  } finally {
    loading.value = false
  }
}

function mergeOccurrences(
  calendar: Awaited<ReturnType<typeof listCalendarOccurrences>>['items'],
  tasks: Awaited<ReturnType<typeof listTaskOccurrences>>['items'],
) {
  const displayTimeZone = settings.value?.time_zone ?? browserTimeZone
  const merged: CalendarDisplayItem[] = [
    ...calendar.map(item => {
      const start = item.start_at
        ? instantInZone(item.start_at, displayTimeZone)
        : { date: item.local_start_date, time: item.local_start_time }
      const end = item.end_at
        ? instantInZone(item.end_at, displayTimeZone)
        : { date: item.local_end_date, time: item.local_end_time }
      return {
        id: `calendar:${item.series_id}:${item.occurrence_type}:${item.occurrence_key}`,
        source: 'calendar' as const,
        date: start.date,
        end_date: end.date,
        time: start.time,
        end_time: end.time,
        title: item.name,
        subtitle: item.location,
        state: item.status,
        kind: item.kind,
        conflict: item.conflict_with.length > 0,
        calendar: item,
      }
    }),
    ...tasks.map(item => {
      const due = item.due_kind === 'datetime' && item.due_at
        ? instantInZone(item.due_at, displayTimeZone)
        : { date: item.due_date, time: item.due_time }
      return {
        id: `task:${item.task_id}:${item.occurrence_key}`,
        source: 'task' as const,
        date: due.date,
        end_date: due.date,
        time: due.time,
        end_time: null,
        title: item.title,
        subtitle: item.estimated_minutes ? `预计 ${item.estimated_minutes} 分钟` : '',
        state: item.status,
        kind: 'task' as const,
        conflict: false,
        task: item,
      }
    }),
  ]
  return merged.sort((left, right) => (
    left.date.localeCompare(right.date)
    || (left.time ?? '').localeCompare(right.time ?? '')
    || left.title.localeCompare(right.title, 'zh-CN')
  ))
}

async function loadOccurrences() {
  if (!settings.value?.initialized) return
  const revision = ++loadRevision
  loading.value = true
  pageError.value = null
  try {
    const [calendarResult, taskResult] = await Promise.all([
      listCalendarOccurrences(range.value),
      listTaskOccurrences(range.value.start, range.value.end),
    ])
    if (revision !== loadRevision) return
    calendarItems.value = mergeOccurrences(calendarResult.items, taskResult.items)
    settings.value = {
      ...settings.value,
      time_zone: calendarResult.time_zone,
    }
    loaded.value = true
    if (selected.value) {
      selected.value = calendarItems.value.find(item => item.id === selected.value?.id) ?? null
    }
  } catch (reason) {
    if (revision !== loadRevision) return
    pageError.value = failure(reason)
  } finally {
    if (revision === loadRevision) loading.value = false
  }
}

function moveRange(direction: -1 | 1) {
  anchorTouched = true
  if (view.value === 'week') {
    anchor.value = addDays(anchor.value, direction * 7)
    return
  }
  const date = parseLocalDate(anchor.value)
  date.setMonth(date.getMonth() + direction, 1)
  anchor.value = localDate(date)
}

function goToday() {
  anchorTouched = true
  anchor.value = today.value
}

function applyJumpDate(event: Event) {
  const value = (event.target as HTMLInputElement).value
  jumpDate.value = value
  if (validDate(value)) {
    anchorTouched = true
    anchor.value = value
  }
}

function restoreJumpDate() {
  if (!validDate(jumpDate.value)) jumpDate.value = anchor.value
}

async function selectItem(item: CalendarDisplayItem) {
  if (selected.value?.id !== item.id) pageSuccess.value = ''
  selected.value = item
  await nextTick()
  if (window.matchMedia('(max-width: 840px)').matches) {
    document.querySelector<HTMLElement>('.occurrence-header h1')?.focus()
  }
}

async function closeDetail() {
  const selectedId = selected.value?.id
  selected.value = null
  await nextTick()
  if (!selectedId) return
  const trigger = [...document.querySelectorAll<HTMLElement>('[data-event-id]')]
    .find(element => element.dataset.eventId === selectedId && element.offsetParent !== null)
  trigger?.focus()
}

function openCreate() {
  editingSeries.value = null
  splittingOccurrence.value = null
  seriesOpen.value = true
}

async function openImport() {
  importOpen.value = false
  await nextTick()
  importOpen.value = true
}

async function closeImport() {
  importOpen.value = false
  await nextTick()
  document.querySelector<HTMLElement>('[data-calendar-import-trigger]')?.focus()
}

async function openSeriesEditor(occurrence: CalendarOccurrence, split = false) {
  pageError.value = null
  try {
    editingSeries.value = await getSeries(occurrence.series_id)
    splittingOccurrence.value = split ? occurrence : null
    seriesOpen.value = true
  } catch (reason) {
    pageError.value = failure(reason)
  }
}

async function refreshMetadata() {
  const [currentSettings, currentTerms, currentSeries] = await Promise.all([
    getTimeSettings(),
    listTerms(),
    listSeries(),
  ])
  settings.value = currentSettings
  terms.value = currentTerms
  series.value = currentSeries
}

async function onSettingsChanged(message: string) {
  pageSuccess.value = message
  try {
    await refreshMetadata()
    await loadOccurrences()
  } catch (reason) {
    pageError.value = failure(reason)
  }
}

async function onCalendarChanged(message: string, conflictCount: number) {
  pageSuccess.value = conflictCount
    ? `${message} 检测到 ${conflictCount} 处时间冲突，已保留所有安排。`
    : message
  pageError.value = null
  try {
    await Promise.all([refreshMetadata(), loadOccurrences()])
  } catch (reason) {
    pageError.value = failure(reason)
  }
}

async function onSeriesSaved(message: string, conflictCount: number) {
  if (!editingSeries.value) selected.value = null
  await onCalendarChanged(message, conflictCount)
}

async function logout() {
  if (pendingLogout.value) return
  pendingLogout.value = true
  pageError.value = null
  try {
    await api('/auth/logout', {})
    clearSession()
    notifySessionChange()
    await router.replace('/login')
  } catch (reason) {
    pageError.value = failure(reason)
  } finally {
    pendingLogout.value = false
  }
}

async function focusError() {
  await nextTick()
  document.querySelector<HTMLElement>('.calendar-content .error-status')?.focus()
}

watch(pageError, value => {
  if (value) void focusError()
})
</script>

<template>
  <div class="calendar-workspace" :class="{ 'detail-open': detailOpen }">
    <CalendarNavigation
      :email="identity?.user.email ?? ''"
      :pending-logout="pendingLogout"
      :series-count="series.length"
      :term-count="terms.length"
      @create-series="openCreate"
      @open-import="openImport"
      @open-settings="settingsOpen = true"
      @logout="logout"
    />

    <main id="main-content" class="calendar-main">
      <section class="calendar-content">
        <header class="calendar-toolbar">
          <div class="calendar-title">
            <span>{{ settings?.time_zone ?? browserTimeZone }}</span>
            <h1 tabindex="-1">{{ title }}</h1>
          </div>
          <div class="calendar-toolbar-actions">
            <div class="calendar-stepper" aria-label="日期导航">
              <button type="button" class="small-icon-button" aria-label="上一段日期" title="上一段日期" @click="moveRange(-1)">
                <ChevronLeft :size="19" aria-hidden="true" />
              </button>
              <button type="button" class="text-button today-button" @click="goToday">今天</button>
              <button type="button" class="small-icon-button" aria-label="下一段日期" title="下一段日期" @click="moveRange(1)">
                <ChevronRight :size="19" aria-hidden="true" />
              </button>
            </div>
            <label class="calendar-jump">
              <span class="sr-only">跳转日期</span>
              <input v-model="jumpDate" type="date" aria-label="跳转日期"
                @input="applyJumpDate" @blur="restoreJumpDate" />
            </label>
            <fieldset class="segmented-field calendar-view-switch">
              <legend class="sr-only">日历视图</legend>
              <label :class="{ selected: view === 'week' }">
                <input v-model="view" type="radio" value="week" />周
              </label>
              <label :class="{ selected: view === 'month' }">
                <input v-model="view" type="radio" value="month" />月
              </label>
            </fieldset>
            <button type="button" class="small-icon-button filter-button" aria-label="筛选日历"
              title="筛选日历" :aria-expanded="filtersOpen" @click="filtersOpen = !filtersOpen">
              <Filter :size="18" aria-hidden="true" />
            </button>
            <button type="button" class="button calendar-new-button" aria-label="新建安排" @click="openCreate">
              <CalendarPlus :size="18" aria-hidden="true" /><span>新建安排</span>
            </button>
          </div>
        </header>

        <div v-if="filtersOpen" class="calendar-filters">
          <label class="checkbox-label"><input v-model="showCourses" type="checkbox" />课程</label>
          <label class="checkbox-label"><input v-model="showFixed" type="checkbox" />固定安排</label>
          <label class="checkbox-label"><input v-model="showTasks" type="checkbox" />任务截止</label>
          <label class="checkbox-label"><input v-model="showCancelled" type="checkbox" />已取消/跳过</label>
        </div>

        <div v-if="pageError || pageSuccess || identity?.user.ai_access === 'frozen'" class="calendar-status">
          <div v-if="identity?.user.ai_access === 'frozen'" class="status restriction-status" role="status">
            <CirclePause :size="20" aria-hidden="true" />
            <p>此账号的 AI 功能已暂停，日历和手动编辑仍可使用。</p>
          </div>
          <StatusMessage :error="pageError" :success="detailOpen ? '' : pageSuccess"
            :retry-label="pageError?.retryable || pageError?.code.includes('version_conflict') ? '重新读取' : undefined"
            @retry="initialize" />
        </div>

        <CalendarGrid
          :days="days"
          :items="visibleItems"
          :view="view"
          :month-key="monthKey"
          :today="today"
          :selected-id="selected?.id ?? null"
          :loading="loading && !loaded"
          @select="selectItem"
        />

        <div v-if="loaded && !loading && !visibleItems.length" class="calendar-empty">
          <CalendarPlus :size="28" aria-hidden="true" />
          <h2>这段日期还没有安排</h2>
          <p>可以创建课程、固定安排，或为任务设置截止日期。</p>
          <button type="button" class="button" @click="openCreate">新建安排</button>
        </div>

        <button v-if="loaded" type="button" class="calendar-refresh small-icon-button"
          aria-label="刷新日历" title="刷新日历" :disabled="loading" @click="loadOccurrences">
          <RefreshCw :size="18" aria-hidden="true" />
        </button>
      </section>

      <CalendarOccurrencePanel
        :item="selected"
        :time-zone="settings?.time_zone ?? browserTimeZone"
        :recurring="selectedSeries?.recurrence.frequency !== 'none'"
        :series-busy="selectedSeries?.busy ?? true"
        :success="pageSuccess"
        @close="closeDetail"
        @changed="onCalendarChanged"
        @edit-series="openSeriesEditor($event)"
        @split-series="openSeriesEditor($event, true)"
      />
    </main>

    <CalendarSettingsDialog
      :open="settingsOpen"
      :settings="settings"
      :terms="terms"
      :browser-time-zone="browserTimeZone"
      @close="settingsOpen = false"
      @changed="onSettingsChanged"
    />
    <CalendarImportDialog
      :open="importOpen"
      :terms="terms"
      :time-zone="settings?.time_zone ?? browserTimeZone"
      :initial-date="anchor"
      @close="closeImport"
      @changed="onCalendarChanged"
    />
    <CalendarSeriesDialog
      :open="seriesOpen"
      :series="editingSeries"
      :split-occurrence="splittingOccurrence"
      :terms="terms"
      :time-zone="settings?.time_zone ?? browserTimeZone"
      :initial-date="anchor"
      @close="seriesOpen = false"
      @saved="onSeriesSaved"
    />
  </div>
</template>
