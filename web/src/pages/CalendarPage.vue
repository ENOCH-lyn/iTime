<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  AlertTriangle,
  CalendarPlus,
  ChevronLeft,
  ChevronRight,
  CirclePause,
  Plus,
  RefreshCw,
  Settings2,
  Trash2,
  Upload,
} from 'lucide-vue-next'
import CalendarGrid from '../components/CalendarGrid.vue'
import CalendarImportDialog from '../components/CalendarImportDialog.vue'
import CalendarOccurrencePanel from '../components/CalendarOccurrencePanel.vue'
import CalendarSeriesDialog from '../components/CalendarSeriesDialog.vue'
import CalendarSettingsDialog from '../components/CalendarSettingsDialog.vue'
import CalendarTrashDialog from '../components/CalendarTrashDialog.vue'
import StatusMessage from '../components/StatusMessage.vue'
import TimetableBoard from '../components/TimetableBoard.vue'
import AppShell from '../shell/AppShell.vue'
import { useAppShell } from '../shell/useAppShell'
import { failure, type Failure } from '../lib/api'
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
  identity,
  refreshSession,
} from '../lib/auth'
import { listTaskOccurrences } from '../lib/task-schedules'
import {
  listPlanningBlocks,
  type PlanningBlockProjection,
  type PlanSourceConflictSummary,
} from '../lib/plans'
import { useSyncRefresh } from '../lib/use-sync-refresh'
import {
  colorIndexFor,
  minutesToLabel,
  timeToMinutes,
  type BoardEvent,
  type TimetableDay,
} from '../lib/timetable'
import '../calendar.css'
import '../calendar-hub.css'
import '../calendar-page.css'

const route = useRoute()
const router = useRouter()
const browserTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
const view = ref<'week' | 'month'>(
  route.query.view === 'month' ? 'month' : 'week',
)
const routeDate = typeof route.query.date === 'string' ? route.query.date : ''
const anchor = ref(validDate(routeDate)
  ? routeDate
  : dateInZone(new Date(), browserTimeZone))
const jumpDate = ref(anchor.value)
const jumpInput = ref<HTMLInputElement | null>(null)
const settings = ref<TimeSettings | null>(null)
const terms = ref<Term[]>([])
const series = ref<CalendarSeries[]>([])
const calendarItems = ref<CalendarDisplayItem[]>([])
const planSourceConflicts = ref<PlanSourceConflictSummary>({
  open_count: 0,
  overlap_count: 0,
  items: [],
})
const selected = ref<CalendarDisplayItem | null>(null)
const loading = ref(false)
const loaded = ref(false)
const { pendingLogout, logoutError, logout } = useAppShell()
const pageError = ref<Failure | null>(null)
const pageSuccess = ref('')
const importOpen = ref(false)
const importBatchId = ref<string | null>(null)
const subscriptionTarget = ref<{ sourceId: string; eventId: string | null } | null>(null)
const settingsOpen = ref(false)
const trashOpen = ref(false)
const trashDialog = ref<InstanceType<typeof CalendarTrashDialog> | null>(null)
const seriesOpen = ref(false)
const createDate = ref(anchor.value)
const createTime = ref<{ startsAt: string; endsAt: string } | null>(null)
const editingSeries = ref<CalendarSeries | null>(null)
const splittingOccurrence = ref<CalendarOccurrence | null>(null)
const showTasks = ref(true)
const showPlans = ref(true)
const showCourses = ref(true)
const showFixed = ref(true)
const showSubscriptions = ref(true)
const showCancelled = ref(true)
const compact = ref(false)
const mobileDay = ref(anchor.value)
let loadRevision = 0
let anchorTouched = validDate(routeDate)
let importOpener: HTMLElement | null = null
let trashOpener: HTMLElement | null = null
let compactQuery: MediaQueryList | null = null
let successTimer = 0

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
  if (view.value === 'month') {
    return new Intl.DateTimeFormat('zh-CN', {
      year: 'numeric',
      month: 'long',
    }).format(parseLocalDate(anchor.value))
  }
  const sameMonth = first.getFullYear() === last.getFullYear()
    && first.getMonth() === last.getMonth()
  const sameYear = first.getFullYear() === last.getFullYear()
  const firstLabel = new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  }).format(first)
  const lastLabel = sameMonth
    ? `${last.getDate()}日`
    : new Intl.DateTimeFormat('zh-CN', {
      year: sameYear ? undefined : 'numeric',
      month: 'long',
      day: 'numeric',
    }).format(last)
  return `${firstLabel} – ${lastLabel}`
})
const weekStart = computed(() => startOfWeek(anchor.value))
const activeTerm = computed(() => terms.value.find(term => (
  term.start_date <= weekStart.value && term.end_date >= addDays(weekStart.value, 6)
)) ?? null)
const weekNumber = computed(() => {
  const term = activeTerm.value
  if (!term) return null
  const days = Math.round(
    (parseLocalDate(weekStart.value).getTime() - parseLocalDate(term.first_week_monday).getTime())
      / 86_400_000,
  )
  if (days < 0) return null
  return Math.floor(days / 7) + 1
})
const contextLabel = computed(() => {
  const parts: string[] = []
  if (activeTerm.value) parts.push(activeTerm.value.name)
  if (view.value === 'week' && weekNumber.value) parts.push(`第 ${weekNumber.value} 周`)
  return parts.join(' · ')
})
const filters = computed(() => [
  { key: 'courses', label: '课程', value: showCourses.value, set: (value: boolean) => { showCourses.value = value } },
  { key: 'fixed', label: '固定安排', value: showFixed.value, set: (value: boolean) => { showFixed.value = value } },
  { key: 'tasks', label: '任务', value: showTasks.value, set: (value: boolean) => { showTasks.value = value } },
  { key: 'plans', label: '计划块', value: showPlans.value, set: (value: boolean) => { showPlans.value = value } },
  { key: 'subscriptions', label: '订阅', value: showSubscriptions.value, set: (value: boolean) => { showSubscriptions.value = value } },
  { key: 'cancelled', label: '已取消', value: showCancelled.value, set: (value: boolean) => { showCancelled.value = value } },
])
function resetFilters() {
  showCourses.value = true
  showFixed.value = true
  showTasks.value = true
  showPlans.value = true
  showSubscriptions.value = true
  showCancelled.value = true
}

const visibleItems = computed(() => calendarItems.value.filter(item => (
  (
    item.kind === 'task'
      ? showTasks.value
      : item.kind === 'plan'
        ? showPlans.value
      : item.calendar?.source_type === 'subscription'
        ? showSubscriptions.value
        : item.kind === 'course'
          ? showCourses.value
          : showFixed.value
  )
  && (
    showCancelled.value
    || !['cancelled', 'source_cancelled', 'skipped'].includes(item.state)
  )
)))
const weekDays = computed(() => days.value.slice(0, 7))
const boardDays = computed<TimetableDay[]>(() => weekDays.value.map(date => {
  const value = parseLocalDate(date)
  return {
    date,
    weekday: new Intl.DateTimeFormat('zh-CN', { weekday: 'short' }).format(value),
    dayNumber: value.getDate(),
    month: value.getMonth() + 1,
    isToday: date === today.value,
  }
}))
const inWeek = (date: string) => (
  weekDays.value.length > 0
  && date >= weekDays.value[0]
  && date <= weekDays.value[weekDays.value.length - 1]
)
const weekTimedItems = computed(() => (
  view.value === 'week'
    ? visibleItems.value.filter(item => inWeek(item.date) && !!item.time)
    : []
))
const weekUntimedItems = computed(() => (
  view.value === 'week'
    ? visibleItems.value.filter(item => inWeek(item.date) && !item.time)
    : []
))
const mobileDayItems = computed(() => visibleItems.value
  .filter(item => item.date === mobileDay.value)
  .sort((left, right) => (left.time ?? '99:99').localeCompare(right.time ?? '99:99')))
const mobileDayLabel = computed(() => {
  const value = parseLocalDate(mobileDay.value)
  const label = new Intl.DateTimeFormat('zh-CN', {
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  }).format(value)
  return label.replace('日星期', '日 星期')
})
const boardEvents = computed<BoardEvent[]>(() => weekTimedItems.value.map(item => {
  const dayIndex = weekDays.value.indexOf(item.date)
  const startMin = timeToMinutes(item.time) ?? 0
  let endMin = timeToMinutes(item.end_time) ?? startMin + 60
  if (endMin <= startMin) endMin = item.end_date !== item.date ? 1440 : startMin + 60
  return {
    id: item.id,
    title: item.title,
    subtitle: item.subtitle,
    dayIndex,
    startMin,
    endMin,
    kind: item.kind,
    colorIndex: colorIndexFor(item.title),
    cancelled: ['cancelled', 'source_cancelled', 'skipped'].includes(item.state),
    occurrence: item.calendar,
    task: item.task,
    plan: item.plan,
  }
}))
const boardRange = computed(() => {
  let start = 8 * 60
  let end = 22 * 60
  for (const item of weekTimedItems.value) {
    const itemStart = timeToMinutes(item.time)
    const itemEnd = timeToMinutes(item.end_time)
    if (itemStart !== null) start = Math.min(start, itemStart)
    if (itemEnd !== null) end = Math.max(end, itemEnd)
  }
  start = Math.max(0, Math.floor(start / 60) * 60)
  end = Math.min(1440, Math.ceil(end / 60) * 60)
  return { start, end: Math.max(end, start + 60) }
})
const boardPeriods = computed(() => (
  terms.value.find(term => (
    term.start_date <= weekDays.value[0] && term.end_date >= weekDays.value[6]
  ))?.periods ?? []
))
const showTeacher = ref(localStorage.getItem('itime.showTeacher') !== 'false')
watch(showTeacher, value => {
  localStorage.setItem('itime.showTeacher', String(value))
})
const nowMinutes = computed(() => {
  if (!weekDays.value.includes(today.value)) return null
  const now = new Date()
  return now.getHours() * 60 + now.getMinutes()
})
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
watch([weekDays, today], () => {
  if (!weekDays.value.length) return
  if (!weekDays.value.includes(mobileDay.value)) {
    mobileDay.value = weekDays.value.includes(today.value) ? today.value : weekDays.value[0]
  }
}, { immediate: true })

function setView(next: 'week' | 'month') {
  if (view.value === next) return
  view.value = next
  selected.value = null
}

watch([view, anchor], () => {
  if (route.path !== '/calendar') return
  if (route.query.view === view.value && route.query.date === anchor.value) return
  void router.replace({
    path: '/calendar',
    query: { ...route.query, view: view.value, date: anchor.value },
  })
})

watch(() => route.query, query => {
  if (route.path !== '/calendar') return
  const nextView = query.view === 'month' ? 'month' : 'week'
  if (view.value !== nextView) view.value = nextView
  const nextDate = typeof query.date === 'string' && validDate(query.date)
    ? query.date
    : anchor.value
  if (nextDate !== anchor.value) {
    anchorTouched = true
    anchor.value = nextDate
  }
}, { deep: true })

function selectBoardEvent(event: BoardEvent) {
  const item = calendarItems.value.find(candidate => candidate.id === event.id)
  if (item) void selectItem(item)
}

function onCompactChange(event: MediaQueryListEvent) {
  compact.value = event.matches
  if (event.matches && !weekDays.value.includes(mobileDay.value)) {
    mobileDay.value = weekDays.value.includes(today.value) ? today.value : weekDays.value[0] ?? anchor.value
  }
}

function onKeydown(event: KeyboardEvent) {
  if (event.key !== 'Escape' || !selected.value) return
  if (settingsOpen.value || seriesOpen.value || importOpen.value || trashOpen.value) return
  event.preventDefault()
  void closeDetail()
}

function onCalendarCommand(command: string | number | object) {
  if (command === 'import') void openImport()
  else if (command === 'trash') void openTrash()
  else if (command === 'settings') settingsOpen.value = true
  else if (command === 'refresh') void loadOccurrences()
}

onMounted(async () => {
  compactQuery = window.matchMedia('(max-width: 839px)')
  compact.value = compactQuery.matches
  compactQuery.addEventListener('change', onCompactChange)
  window.addEventListener('keydown', onKeydown)
  document.title = '日历 · iTime'
  await refreshSession()
  if (!identity.value) {
    await router.replace('/login')
    return
  }
  await initialize()
  const sourceId = typeof route.query.source === 'string'
    ? route.query.source
    : null
  const eventId = typeof route.query.event === 'string'
    ? route.query.event
    : null
  const requestedImport = typeof route.query.import === 'string'
    ? route.query.import
    : null
  if (requestedImport) await openImport(requestedImport)
  else if (sourceId) await openSubscription(sourceId, eventId)
})

onUnmounted(() => {
  window.clearTimeout(successTimer)
  compactQuery?.removeEventListener('change', onCompactChange)
  window.removeEventListener('keydown', onKeydown)
})

useSyncRefresh(
  [
    'time_settings',
    'term',
    'calendar_series',
    'calendar_exception',
    'calendar_addition',
    'calendar_source',
    'task',
    'task_occurrence',
    'plan',
  ],
  async () => {
    await Promise.all([refreshMetadata(), loadOccurrences()])
    if (trashOpen.value) await trashDialog.value?.refresh(true)
    if (settingsOpen.value || seriesOpen.value || importOpen.value || trashOpen.value) {
      pageSuccess.value = '其他设备有更新；当前编辑内容已保留。'
    }
  },
)

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
  plans: PlanningBlockProjection[],
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
        id: `calendar:${item.series_id ?? item.subscription_event_id}:${item.occurrence_type}:${item.occurrence_key}`,
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
      let endTime: string | null = null
      const estimated = Number(item.estimated_minutes || 0)
      if (due.time && estimated > 0) {
        const [hours, minutes] = due.time.split(':').map(Number)
        const total = hours * 60 + minutes + estimated
        if (total < 1440) {
          endTime = `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
        }
      }
      return {
        id: `task:${item.task_id}:${item.occurrence_key}`,
        source: 'task' as const,
        date: due.date,
        end_date: due.date,
        time: due.time,
        end_time: endTime,
        title: item.title,
        subtitle: item.estimated_minutes ? `预计 ${item.estimated_minutes} 分钟` : '',
        state: item.status,
        kind: 'task' as const,
        conflict: false,
        task: item,
      }
    }),
    ...plans.map(item => ({
      id: `plan:${item.id}`,
      source: 'plan' as const,
      date: item.local_date,
      end_date: item.local_date,
      time: item.local_starts_at,
      end_time: item.local_ends_at,
      title: item.task_title_snapshot,
      subtitle: item.step_titles_snapshot.length
        ? item.step_titles_snapshot.join('、')
        : `计划第 ${item.plan_version} 版`,
      state: 'scheduled' as const,
      kind: 'plan' as const,
      conflict: item.source_conflict_count > 0,
      plan: item,
    })),
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
    const [calendarResult, taskResult, planResult] = await Promise.all([
      listCalendarOccurrences(range.value),
      listTaskOccurrences(range.value.start, range.value.end),
      listPlanningBlocks(range.value.start, range.value.end),
    ])
    if (revision !== loadRevision) return
    calendarItems.value = mergeOccurrences(
      calendarResult.items,
      taskResult.items,
      planResult.items,
    )
    planSourceConflicts.value = planResult.source_conflicts ?? {
      open_count: 0,
      overlap_count: 0,
      items: [],
    }
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
    const weekdayIndex = (parseLocalDate(mobileDay.value).getDay() || 7) - 1
    anchor.value = addDays(anchor.value, direction * 7)
    if (compact.value) {
      mobileDay.value = addDays(startOfWeek(anchor.value), weekdayIndex)
    }
    return
  }
  const date = parseLocalDate(anchor.value)
  date.setMonth(date.getMonth() + direction, 1)
  anchor.value = localDate(date)
}

function goToday() {
  anchorTouched = true
  anchor.value = today.value
  mobileDay.value = today.value
}

function applyJumpDate(event: Event) {
  const value = (event.target as HTMLInputElement).value
  jumpDate.value = value
  if (validDate(value)) {
    anchorTouched = true
    anchor.value = value
    mobileDay.value = value
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
    document.querySelector<HTMLElement>('.occurrence-header h2')?.focus()
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

function openDatePicker() {
  const input = jumpInput.value
  if (!input) return
  if (typeof input.showPicker === 'function') {
    try {
      input.showPicker()
    } catch {
      input.focus()
    }
  } else {
    input.focus()
  }
}

function openCreate() {
  createDate.value = anchor.value
  createTime.value = null
  editingSeries.value = null
  splittingOccurrence.value = null
  seriesOpen.value = true
}

function openDay(date: string) {
  anchorTouched = true
  anchor.value = date
  mobileDay.value = date
  if (view.value !== 'week') view.value = 'week'
  selected.value = null
}

function openCreateAt(dayIndex: number, startMinutes: number) {
  const date = weekDays.value[dayIndex] ?? anchor.value
  const startsAt = minutesToLabel(startMinutes)
  const endsAt = minutesToLabel(Math.min(23 * 60 + 59, startMinutes + 60))
  createDate.value = date
  createTime.value = { startsAt, endsAt }
  editingSeries.value = null
  splittingOccurrence.value = null
  seriesOpen.value = true
}

async function openImport(batchId?: string) {
  importOpener = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null
  importBatchId.value = typeof batchId === 'string' ? batchId : null
  subscriptionTarget.value = null
  importOpen.value = false
  await nextTick()
  importOpen.value = true
}

async function closeImport() {
  const opener = importOpener
  importOpener = null
  importOpen.value = false
  importBatchId.value = null
  subscriptionTarget.value = null
  if (typeof route.query.import === 'string') {
    const query = { ...route.query }
    delete query.import
    await router.replace({ path: '/calendar', query })
  }
  await nextTick()
  if (opener?.isConnected && opener.offsetParent !== null) {
    opener.focus()
    return
  }
  document.querySelector<HTMLElement>(
    '.occurrence-header h2, [data-calendar-import-trigger]',
  )?.focus()
}

async function openTrash() {
  trashOpener = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null
  importOpen.value = false
  importBatchId.value = null
  subscriptionTarget.value = null
  settingsOpen.value = false
  seriesOpen.value = false
  await nextTick()
  trashOpen.value = true
}

async function closeTrash() {
  const opener = trashOpener
  trashOpener = null
  trashOpen.value = false
  await nextTick()
  if (opener?.isConnected && opener.offsetParent !== null) {
    opener.focus()
    return
  }
  document.querySelector<HTMLElement>('[data-calendar-trash-trigger]')?.focus()
}

async function openSeriesEditor(occurrence: CalendarOccurrence, split = false) {
  if (!occurrence.series_id) return
  pageError.value = null
  try {
    editingSeries.value = await getSeries(occurrence.series_id)
    splittingOccurrence.value = split ? occurrence : null
    seriesOpen.value = true
  } catch (reason) {
    pageError.value = failure(reason)
  }
}

async function openSubscription(sourceId: string, eventId: string | null) {
  importOpener = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null
  subscriptionTarget.value = { sourceId, eventId }
  importBatchId.value = null
  importOpen.value = false
  await nextTick()
  importOpen.value = true
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

async function onTrashChanged(message: string) {
  await onCalendarChanged(message, 0)
}

async function focusError() {
  await nextTick()
  document.querySelector<HTMLElement>('.calendar-page .error-status')?.focus()
}

watch(pageSuccess, value => {
  window.clearTimeout(successTimer)
  if (!value) return
  if (value.includes('冲突') || value.includes('复核')) return
  successTimer = window.setTimeout(() => {
    pageSuccess.value = ''
  }, 8000)
})

watch(pageError, value => {
  if (value) void focusError()
})
</script>

<template>
  <AppShell
    current="timetable"
    title="日历"
    :pending-logout="pendingLogout"
    @logout="logout"
  >
    <template #actions>
      <ElDropdown
        trigger="click"
        placement="bottom-end"
        @command="onCalendarCommand"
      >
        <button
          type="button"
          class="m3-icon-button calendar-more"
          aria-label="更多日历操作"
          title="更多日历操作"
        >
          <span class="material-symbols-rounded" aria-hidden="true">more_vert</span>
        </button>
        <template #dropdown>
          <ElDropdownMenu class="calendar-overflow-menu">
            <ElDropdownItem command="import">
              <Upload :size="18" aria-hidden="true" />
              <span>导入课表</span>
            </ElDropdownItem>
            <ElDropdownItem command="trash">
              <Trash2 :size="18" aria-hidden="true" />
              <span>日历回收站</span>
            </ElDropdownItem>
            <ElDropdownItem command="settings">
              <Settings2 :size="18" aria-hidden="true" />
              <span>时间与学期</span>
            </ElDropdownItem>
            <ElDropdownItem command="refresh" divided>
              <RefreshCw :size="18" aria-hidden="true" />
              <span>刷新日历</span>
            </ElDropdownItem>
          </ElDropdownMenu>
        </template>
      </ElDropdown>
      <button
        type="button"
        class="m3-button m3-button--filled calendar-new-button"
        aria-label="新建日程"
        @click="openCreate"
      >
        <Plus :size="18" aria-hidden="true" />
        <span class="calendar-new-label">新建日程</span>
      </button>
    </template>

    <div class="calendar-page">
      <div class="calendar-toolbar">
        <div class="calendar-title-block">
          <h2 tabindex="-1">{{ title }}</h2>
          <p v-if="contextLabel">{{ contextLabel }}</p>
        </div>
        <div class="calendar-toolbar-controls">
          <div class="calendar-date-controls" role="group" aria-label="日期导航">
            <button
              type="button"
              class="m3-icon-button"
              aria-label="上一段日期"
              title="上一段日期"
              @click="moveRange(-1)"
            >
              <ChevronLeft :size="20" aria-hidden="true" />
            </button>
            <button
              type="button"
              class="m3-button m3-button--tonal calendar-today"
              @click="goToday"
            >
              今天
            </button>
            <button
              type="button"
              class="m3-icon-button"
              aria-label="下一段日期"
              title="下一段日期"
              @click="moveRange(1)"
            >
              <ChevronRight :size="20" aria-hidden="true" />
            </button>
          </div>
          <div class="m3-segmented calendar-view-switch" role="tablist" aria-label="日历视图">
            <button
              type="button"
              role="tab"
              :aria-selected="view === 'week'"
              :class="{ selected: view === 'week' }"
              @click="setView('week')"
            >
              周
            </button>
            <button
              type="button"
              role="tab"
              :aria-selected="view === 'month'"
              :class="{ selected: view === 'month' }"
              @click="setView('month')"
            >
              月
            </button>
          </div>
          <label class="calendar-jump" title="跳到指定日期" @click.prevent="openDatePicker">
            <span class="material-symbols-rounded" aria-hidden="true">calendar_month</span>
            <input
              ref="jumpInput"
              v-model="jumpDate"
              type="date"
              aria-label="跳转日期"
              @input="applyJumpDate"
              @blur="restoreJumpDate"
            >
          </label>
        </div>
      </div>

      <div class="calendar-filter-row" role="group" aria-label="显示内容">
        <span class="calendar-filter-label">显示</span>
        <button
          v-for="filter in filters"
          :key="filter.key"
          type="button"
          class="m3-filter-chip"
          :class="{ selected: filter.value }"
          :aria-pressed="filter.value"
          @click="filter.set(!filter.value)"
        >
          <span
            v-if="filter.value"
            class="material-symbols-rounded"
            aria-hidden="true"
          >check</span>
          {{ filter.label }}
        </button>
      </div>

      <div
        v-if="pageError || pageSuccess || logoutError || identity?.user.ai_access === 'frozen' || planSourceConflicts.open_count"
        class="calendar-status"
      >
        <StatusMessage :error="logoutError" />
        <div v-if="identity?.user.ai_access === 'frozen'" class="status restriction-status" role="status">
          <CirclePause :size="20" aria-hidden="true" />
          <p>此账号的 AI 功能已暂停，日历和手动编辑仍可使用。</p>
        </div>
        <StatusMessage :error="pageError" :success="detailOpen ? '' : pageSuccess"
          :retry-label="pageError?.retryable || pageError?.code.includes('version_conflict') ? '重新读取' : undefined"
          @retry="initialize" />
        <div v-if="planSourceConflicts.open_count" class="status calendar-plan-source-status" role="status">
          <AlertTriangle :size="20" aria-hidden="true" />
          <p>
            当前范围有 {{ planSourceConflicts.open_count }} 个订阅来源变化需要复核；
            {{ planSourceConflicts.overlap_count
              ? `${planSourceConflicts.overlap_count} 个来源与计划块重叠。`
              : '计划内容尚未自动调整。' }}
          </p>
        </div>
      </div>

      <div
        v-if="!compact && loaded && !loading && !pageError && !calendarItems.length"
        class="calendar-onboarding"
      >
        <span class="calendar-onboarding-icon" aria-hidden="true">
          <CalendarPlus :size="24" />
        </span>
        <div class="calendar-onboarding-copy">
          <h2>{{ series.length || terms.length ? (view === 'week' ? '这一周还没有安排' : '这个月还没有安排') : '开始安排你的日程' }}</h2>
          <p v-if="series.length || terms.length">
            新建一个日程，或者切换视图看看其它日期。
          </p>
          <p v-else>
            新建课程或固定安排；已经有课表文件的话，可以直接导入。
          </p>
        </div>
        <div class="calendar-onboarding-actions">
          <button type="button" class="m3-button m3-button--filled" @click="openCreate">
            新建日程
          </button>
          <template v-if="series.length || terms.length">
            <button type="button" class="m3-button m3-button--text" @click="setView(view === 'week' ? 'month' : 'week')">
              {{ view === 'week' ? '查看月视图' : '查看周视图' }}
            </button>
          </template>
          <template v-else>
            <button type="button" class="m3-button m3-button--outlined" @click="openImport()">
              导入课表
            </button>
            <button type="button" class="m3-button m3-button--text" @click="settingsOpen = true">
              设置学期
            </button>
          </template>
        </div>
      </div>

      <div v-if="loading" class="calendar-progress" role="progressbar" aria-label="正在刷新日历"></div>

      <div
        v-if="loaded && !loading && !visibleItems.length && calendarItems.length"
        class="calendar-filter-empty"
      >
        <span>当前筛选隐藏了全部安排。</span>
        <button type="button" class="m3-button m3-button--text" @click="resetFilters">
          恢复全部
        </button>
      </div>

      <div v-if="view === 'week'" class="calendar-week-view">
        <section v-if="compact" class="mobile-day-view" aria-label="日安排">
          <div class="mobile-day-strip" role="tablist" aria-label="选择日期">
            <button
              v-for="day in boardDays"
              :key="day.date"
              type="button"
              role="tab"
              :aria-selected="mobileDay === day.date"
              :class="{ selected: mobileDay === day.date, today: day.isToday }"
              @click="mobileDay = day.date"
            >
              <span>{{ day.weekday.replace('周', '') }}</span>
              <strong>{{ day.dayNumber }}</strong>
            </button>
          </div>
          <div class="mobile-day-heading">
            <div>
              <h3>{{ mobileDayLabel }}</h3>
              <p>{{ mobileDayItems.length }} 项安排</p>
            </div>
          </div>
          <div v-if="mobileDayItems.length" class="mobile-agenda-list">
            <button
              v-for="item in mobileDayItems"
              :key="item.id"
              type="button"
              class="mobile-agenda-item"
              :data-event-id="item.id"
              :class="[
                `kind-${item.kind}`,
                {
                  cancelled: ['cancelled', 'source_cancelled', 'skipped'].includes(item.state),
                  selected: selected?.id === item.id,
                },
              ]"
              @click="selectItem(item)"
            >
              <time>{{ item.time ? item.time.slice(0, 5) : '全天' }}</time>
              <span class="mobile-agenda-bar" aria-hidden="true" />
              <span class="mobile-agenda-copy">
                <strong>{{ item.title }}</strong>
                <small>{{ item.subtitle || (item.kind === 'task' ? '任务截止' : '无地点') }}</small>
              </span>
              <AlertTriangle v-if="item.conflict" :size="18" aria-label="存在时间冲突" />
              <span v-else class="material-symbols-rounded" aria-hidden="true">chevron_right</span>
            </button>
          </div>
          <div v-else class="mobile-day-empty">
            <CalendarPlus :size="26" aria-hidden="true" />
            <strong>{{ series.length || terms.length ? '这一天没有安排' : '开始安排你的日程' }}</strong>
            <p v-if="series.length || terms.length">
              新建一个日程，或切到月视图查看全月。
            </p>
            <p v-else>
              新建课程或固定安排，也可以直接导入已有课表。
            </p>
            <div class="mobile-day-empty-actions">
              <button type="button" class="m3-button m3-button--filled" @click="openCreate">
                新建日程
              </button>
              <button
                v-if="!series.length && !terms.length"
                type="button"
                class="m3-button m3-button--text"
                @click="openImport()"
              >
                导入课表
              </button>
            </div>
          </div>
        </section>

        <template v-else>
          <TimetableBoard
            :days="boardDays"
            :items="boardEvents"
            :periods="boardPeriods"
            :range-start="boardRange.start"
            :range-end="boardRange.end"
            :now-minutes="nowMinutes"
            :loading="loading && !loaded"
            :show-teacher="showTeacher"
            @select="selectBoardEvent"
            @create="openCreateAt"
          />
          <section v-if="weekUntimedItems.length" class="calendar-unscheduled" aria-label="本周待安排">
            <header>
              <h2>本周待安排</h2>
              <span>{{ weekUntimedItems.length }} 项</span>
            </header>
            <div class="calendar-unscheduled-list">
              <button
                v-for="item in weekUntimedItems"
                :key="item.id"
                type="button"
                :data-event-id="item.id"
                class="calendar-unscheduled-item"
                @click="selectItem(item)"
              >
                <span class="material-symbols-rounded" aria-hidden="true">
                  {{ item.kind === 'task' ? 'task_alt' : 'event' }}
                </span>
                <span>
                  <strong>{{ item.title }}</strong>
                  <small>{{ item.subtitle || '未设置时间' }}</small>
                </span>
              </button>
            </div>
          </section>
        </template>
      </div>

      <CalendarGrid
        v-else
        :days="days"
        :items="visibleItems"
        :view="view"
        :month-key="monthKey"
        :today="today"
        :selected-id="selected?.id ?? null"
        :loading="loading && !loaded"
        @select="selectItem"
        @select-day="openDay"
      />

      <div v-if="compact && view === 'month' && loaded && !loading && !calendarItems.length" class="calendar-empty">
        <CalendarPlus :size="28" aria-hidden="true" />
        <h2>这段日期还没有安排</h2>
        <p>可以创建课程、固定安排，或为任务设置截止日期。</p>
        <button type="button" class="m3-button m3-button--filled" @click="openCreate">
          新建日程
        </button>
      </div>
    </div>

    <div
      v-if="selected"
      class="calendar-sheet-scrim"
      aria-hidden="true"
      @click="closeDetail"
    />
    <CalendarOccurrencePanel
      v-if="selected"
      :item="selected"
      :time-zone="settings?.time_zone ?? browserTimeZone"
      :recurring="selectedSeries?.recurrence.frequency !== 'none'"
      :series-busy="selectedSeries?.busy ?? true"
      :success="pageSuccess"
      @close="closeDetail"
      @changed="onCalendarChanged"
      @edit-series="openSeriesEditor($event)"
      @split-series="openSeriesEditor($event, true)"
      @open-subscription="openSubscription"
    />

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
      :initial-batch-id="importBatchId"
      :subscription-target="subscriptionTarget"
      @close="closeImport"
      @changed="onCalendarChanged"
    />
    <CalendarSeriesDialog
      :open="seriesOpen"
      :series="editingSeries"
      :split-occurrence="splittingOccurrence"
      :terms="terms"
      :time-zone="settings?.time_zone ?? browserTimeZone"
      :initial-date="createDate"
      :initial-time="createTime"
      @close="seriesOpen = false"
      @saved="onSeriesSaved"
    />
    <CalendarTrashDialog
      ref="trashDialog"
      :open="trashOpen"
      @close="closeTrash"
      @changed="onTrashChanged"
    />
  </AppShell>
</template>
