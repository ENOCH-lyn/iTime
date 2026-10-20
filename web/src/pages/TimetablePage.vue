<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowRight,
  CalendarPlus,
  Check,
  ChevronLeft,
  ChevronRight,
  Clock3,
  MapPin,
  Plus,
  Sparkles,
  Trash2,
  X,
} from 'lucide-vue-next'
import TimetableBoard from '../components/TimetableBoard.vue'
import CalendarSettingsDialog from '../components/CalendarSettingsDialog.vue'
import TimetableEventDialog, {
  type EventFormValues,
} from '../components/TimetableEventDialog.vue'
import QuickTaskDialog, {
  type TaskFormValues,
} from '../components/QuickTaskDialog.vue'
import { failure, type Failure } from '../lib/api'
import { identity, refreshSession } from '../lib/auth'
import {
  getSeries,
  getTimeSettings,
  listCalendarOccurrences,
  listTerms,
  saveSeries,
  saveTimeSettings,
  trashCalendarSeries,
  type CalendarOccurrence,
  type CalendarSeries,
  type SeriesDraft,
  type Term,
  type TimeSettings,
} from '../lib/calendar'
import {
  createTask,
  deleteTask,
  listTasks,
  setTaskCompletion,
  type TaskSummary,
} from '../lib/tasks'
import {
  emptyRecurrence,
  listTaskOccurrences,
  updateTaskSchedule,
  type TaskOccurrence,
} from '../lib/task-schedules'
import {
  addDays,
  type BoardEvent,
  colorIndexFor,
  dateInZone,
  daysBetween,
  formatRange,
  isoWeekday,
  makeWeek,
  minutesInZone,
  minutesToLabel,
  parseLocalDate,
  startOfWeek,
  timeToMinutes,
  weekdayLabels,
} from '../lib/timetable'

const router = useRouter()
const browserTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai'

const loading = ref(true)
const weekLoading = ref(false)
const tasksLoading = ref(false)
const error = ref<Failure | null>(null)
const notice = ref('')
const settings = ref<TimeSettings | null>(null)
const terms = ref<Term[]>([])
const termId = ref<string | null>(null)
const weekStart = ref(startOfWeek(dateInZone(new Date(), browserTimeZone)))
const calendarOccurrences = ref<CalendarOccurrence[]>([])
const taskOccurrences = ref<TaskOccurrence[]>([])
const tasks = ref<TaskSummary[]>([])
const quickTitle = ref('')
const quickBusy = ref(false)
const aiPrompt = ref('')
const nowTick = ref(Date.now())
let noticeTimer = 0
let clockTimer = 0

const timeZone = computed(() => settings.value?.time_zone ?? browserTimeZone)
const today = computed(() => dateInZone(new Date(nowTick.value), timeZone.value))
const weekDays = computed(() => makeWeek(weekStart.value, today.value))
const activeTerm = computed(() => (
  terms.value.find(term => term.id === termId.value) ?? null
))
const weekNumber = computed(() => {
  const term = activeTerm.value
  if (!term) return null
  const number = Math.floor(daysBetween(term.first_week_monday, weekStart.value) / 7) + 1
  return number > 0 ? number : null
})
const todayIndex = computed(() => {
  const index = weekDays.value.findIndex(day => day.isToday)
  return index === -1 ? 0 : index
})
const todayLabel = computed(() => {
  const date = parseLocalDate(today.value)
  const weekday = weekdayLabels()[(date.getDay() || 7) - 1]
  return `${date.getMonth() + 1}月${date.getDate()}日 · ${weekday}`
})

const range = computed(() => {
  let start = 8 * 60
  let end = 18 * 60
  const periods = activeTerm.value?.periods ?? []
  if (periods.length) {
    const starts = periods
      .map(period => timeToMinutes(period.starts_at))
      .filter((value): value is number => value !== null)
    const ends = periods
      .map(period => timeToMinutes(period.ends_at))
      .filter((value): value is number => value !== null)
    if (starts.length) start = Math.min(start, ...starts)
    if (ends.length) end = Math.max(end, ...ends)
  }
  for (const occurrence of calendarOccurrences.value) {
    const occurrenceStart = timeToMinutes(occurrence.local_start_time)
    const occurrenceEnd = timeToMinutes(occurrence.local_end_time)
    if (occurrenceStart !== null) start = Math.min(start, occurrenceStart)
    if (occurrenceEnd !== null) end = Math.max(end, occurrenceEnd)
  }
  for (const task of taskOccurrences.value) {
    if (task.due_kind !== 'datetime') continue
    const taskStart = timeToMinutes(task.due_time)
    if (taskStart === null) continue
    const duration = Math.min(Math.max(task.estimated_minutes ?? 30, 15), 120)
    start = Math.min(start, taskStart)
    end = Math.max(end, taskStart + duration)
  }
  start = Math.max(0, Math.floor((start - 15) / 30) * 30)
  end = Math.min(1440, Math.ceil((end + 15) / 30) * 30)
  if (end - start < 240) end = start + 240
  return { start, end }
})

const nowMinutes = computed(() => {
  if (weekDays.value[todayIndex.value]?.date !== today.value) return null
  if (today.value !== dateInZone(new Date(nowTick.value), timeZone.value)) return null
  return minutesInZone(new Date(nowTick.value), timeZone.value)
})

const boardEvents = computed<BoardEvent[]>(() => {
  const startDate = weekStart.value
  const output: BoardEvent[] = []
  for (const occurrence of calendarOccurrences.value) {
    if (!occurrence.local_start_time) continue
    if (occurrence.local_start_date < startDate || occurrence.local_start_date > addDays(startDate, 6)) {
      continue
    }
    const startMin = timeToMinutes(occurrence.local_start_time)
    if (startMin === null) continue
    let endMin = timeToMinutes(occurrence.local_end_time) ?? startMin + 60
    if (occurrence.local_end_date !== occurrence.local_start_date) endMin = 1440
    if (endMin <= startMin) endMin = startMin + 30
    output.push({
      id: `course:${occurrence.series_id ?? occurrence.subscription_event_id}:${occurrence.occurrence_key}`,
      title: occurrence.name,
      subtitle: occurrence.location,
      dayIndex: daysBetween(startDate, occurrence.local_start_date),
      startMin,
      endMin,
      kind: occurrence.kind,
      colorIndex: colorIndexFor(occurrence.name),
      cancelled: occurrence.status !== 'scheduled',
      occurrence,
    })
  }
  for (const task of taskOccurrences.value) {
    if (task.due_kind !== 'datetime' || !task.due_time) continue
    if (task.due_date < startDate || task.due_date > addDays(startDate, 6)) continue
    const startMin = timeToMinutes(task.due_time)
    if (startMin === null) continue
    const duration = Math.min(Math.max(task.estimated_minutes ?? 30, 15), 120)
    output.push({
      id: `task:${task.task_id}:${task.occurrence_key}`,
      title: task.title,
      subtitle: task.estimated_minutes ? `预计 ${task.estimated_minutes} 分钟` : '任务',
      dayIndex: daysBetween(startDate, task.due_date),
      startMin,
      endMin: startMin + duration,
      kind: 'task',
      colorIndex: 0,
      cancelled: task.status !== 'open',
      task,
    })
  }
  return output
})

const openTasks = computed(() => tasks.value.filter(task => (
  task.status === 'open' && task.lifecycle === 'active'
)))

const taskGroups = computed(() => {
  const overdue: TaskSummary[] = []
  const todayItems: TaskSummary[] = []
  const thisWeek: TaskSummary[] = []
  const later: TaskSummary[] = []
  for (const task of openTasks.value) {
    const due = task.schedule.due_date
    if (!due) {
      later.push(task)
    } else if (due < today.value) {
      overdue.push(task)
    } else if (due === today.value) {
      todayItems.push(task)
    } else if (due <= addDays(today.value, 6)) {
      thisWeek.push(task)
    } else {
      later.push(task)
    }
  }
  return [
    { key: 'overdue', title: '已逾期', items: overdue.slice(0, 4) },
    { key: 'today', title: '今天', items: todayItems.slice(0, 5) },
    { key: 'week', title: '本周', items: thisWeek.slice(0, 4) },
    { key: 'later', title: '更晚 / 无日期', items: later.slice(0, 3) },
  ].filter(group => group.items.length > 0)
})

const todayTaskCount = computed(() => openTasks.value.filter(task => {
  const due = task.schedule.due_date
  return due === today.value || (due !== null && due < today.value)
}).length)

const aiSuggestions = [
  '帮我把这周的作业排进日程',
  '每周三 19:00 加一节健身课',
  '明天下午三点开组会',
  '复盘今天',
]

const selectedEvent = ref<BoardEvent | null>(null)
const detailOpen = ref(false)
const selectedSeries = computed(() => selectedEvent.value?.occurrence?.series_id ?? null)
const selectedCanEdit = computed(() => (
  selectedEvent.value?.kind !== 'task'
  && !!selectedEvent.value?.occurrence?.series_id
  && selectedEvent.value?.occurrence?.series_version !== null
))

const eventDialogOpen = ref(false)
const eventDialogMode = ref<'create' | 'edit'>('create')
const eventDialogBusy = ref(false)
const eventDialogError = ref('')
const editingSeries = ref<CalendarSeries | null>(null)
const eventInitial = ref<EventFormValues>({
  name: '',
  kind: 'course',
  location: '',
  date: today.value,
  startTime: '09:00',
  endTime: '10:00',
  repeat: true,
  weekdays: [isoWeekday(today.value)],
})

const taskDialogOpen = ref(false)
const taskDialogBusy = ref(false)
const taskDialogError = ref('')
const taskInitial = ref({ title: '', date: today.value })
const settingsOpen = ref(false)
const selectedTask = ref<TaskSummary | null>(null)
const taskDetailOpen = ref(false)
const taskActionBusy = ref(false)

function showNotice(message: string) {
  notice.value = message
  window.clearTimeout(noticeTimer)
  noticeTimer = window.setTimeout(() => {
    notice.value = ''
  }, 3200)
}

async function loadWeek() {
  weekLoading.value = true
  error.value = null
  const start = weekStart.value
  const end = addDays(start, 7)
  try {
    const [calendar, taskPage] = await Promise.all([
      listCalendarOccurrences({ start, end }),
      listTaskOccurrences(start, end),
    ])
    calendarOccurrences.value = calendar.items
    taskOccurrences.value = taskPage.items
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    weekLoading.value = false
  }
}

async function loadTasks() {
  tasksLoading.value = true
  try {
    const page = await listTasks({
      lifecycle: 'active',
      search: '',
      status: 'open',
      project_id: null,
      inbox: false,
      priority: null,
      label: '',
    })
    tasks.value = page.items
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    tasksLoading.value = false
  }
}

async function initialize() {
  try {
    let current = await getTimeSettings()
    if (!current.initialized) {
      current = await saveTimeSettings(current, browserTimeZone)
    }
    settings.value = current
    terms.value = await listTerms()
    const todayValue = dateInZone(new Date(), current.time_zone ?? browserTimeZone)
    termId.value = terms.value.find(term => (
      term.start_date <= todayValue && term.end_date >= todayValue
    ))?.id ?? terms.value.at(-1)?.id ?? null
    weekStart.value = startOfWeek(todayValue)
    await Promise.all([loadWeek(), loadTasks()])
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  document.title = '课表 · iTime'
  await refreshSession()
  if (!identity.value) {
    await router.replace('/login')
    return
  }
  await initialize()
  clockTimer = window.setInterval(() => {
    nowTick.value = Date.now()
  }, 30_000)
})

onUnmounted(() => {
  window.clearInterval(clockTimer)
  window.clearTimeout(noticeTimer)
})

function moveWeek(offset: number) {
  weekStart.value = addDays(weekStart.value, offset * 7)
  void loadWeek()
}

function goToday() {
  weekStart.value = startOfWeek(today.value)
  void loadWeek()
}

function openCreate(dayIndex: number, startMinutes: number) {
  if (!activeTerm.value) {
    settingsOpen.value = true
    showNotice('先设置学期，课程才能按周重复。')
    return
  }
  const date = weekDays.value[dayIndex]?.date ?? today.value
  eventDialogMode.value = 'create'
  editingSeries.value = null
  eventDialogError.value = ''
  eventInitial.value = {
    name: '',
    kind: 'course',
    location: '',
    date,
    startTime: minutesToLabel(startMinutes),
    endTime: minutesToLabel(Math.min(startMinutes + 60, 24 * 60 - 30)),
    repeat: true,
    weekdays: [isoWeekday(date)],
  }
  eventDialogOpen.value = true
}

function openCreateFromHeader() {
  openCreate(todayIndex.value, 9 * 60)
}

function selectEvent(event: BoardEvent) {
  selectedEvent.value = event
  detailOpen.value = true
}

async function editSelected() {
  const occurrence = selectedEvent.value?.occurrence
  if (!occurrence?.series_id) return
  try {
    const series = await getSeries(occurrence.series_id)
    editingSeries.value = series
    eventDialogMode.value = 'edit'
    eventDialogError.value = ''
    eventInitial.value = {
      name: series.name,
      kind: series.kind,
      location: series.location,
      date: series.start_date,
      startTime: (series.starts_at || '09:00').slice(0, 5),
      endTime: (series.ends_at || '10:00').slice(0, 5),
      repeat: series.recurrence.frequency !== 'none',
      weekdays: series.recurrence.weekdays.length
        ? [...series.recurrence.weekdays]
        : [isoWeekday(series.start_date)],
    }
    eventDialogOpen.value = true
  } catch (reason) {
    error.value = failure(reason)
  }
}

async function submitEvent(values: EventFormValues) {
  eventDialogBusy.value = true
  eventDialogError.value = ''
  try {
    let recurrence = emptyRecurrence()
    if (values.kind === 'course') {
      const term = activeTerm.value
      if (!term) {
        throw new Error('请先在设置里创建学期，再添加课程。')
      }
      const firstWeek = Math.floor(daysBetween(term.first_week_monday, values.date) / 7) + 1
      const lastWeek = Math.floor(daysBetween(term.first_week_monday, term.end_date) / 7) + 1
      if (firstWeek < 1 || firstWeek > lastWeek) {
        throw new Error('课程开始日期不在当前学期内。')
      }
      recurrence = {
        ...emptyRecurrence(),
        frequency: 'weekly',
        weekdays: values.weekdays.length ? values.weekdays : [isoWeekday(values.date)],
        week_numbers: Array.from(
          { length: lastWeek - firstWeek + 1 },
          (_, index) => firstWeek + index,
        ),
      }
    } else if (values.repeat) {
      recurrence = {
        ...emptyRecurrence(),
        frequency: 'weekly',
        weekdays: values.weekdays.length ? values.weekdays : [isoWeekday(values.date)],
        until: activeTerm.value?.end_date ?? null,
      }
    }
    const draft: SeriesDraft = {
      kind: values.kind,
      term_id: activeTerm.value?.id ?? null,
      name: values.name,
      description: editingSeries.value?.description ?? '',
      location: values.location,
      busy: true,
      time_zone: timeZone.value,
      start_date: values.date,
      starts_at: `${values.startTime}:00`,
      ends_at: `${values.endTime}:00`,
      end_day_offset: 0,
      start_period_number: null,
      end_period_number: null,
      ambiguous_utc_offset_minutes: null,
      recurrence,
    }
    if (editingSeries.value) {
      await saveSeries(editingSeries.value, draft)
    } else {
      await saveSeries(null, draft)
    }
    eventDialogOpen.value = false
    detailOpen.value = false
    showNotice(editingSeries.value ? '安排已更新' : '已加入课表')
    await loadWeek()
  } catch (reason) {
    eventDialogError.value = reason instanceof Error
      ? reason.message
      : failure(reason).message
  } finally {
    eventDialogBusy.value = false
  }
}

async function deleteSelected() {
  const occurrence = selectedEvent.value?.occurrence
  if (!occurrence?.series_id || occurrence.series_version === null) return
  if (!window.confirm(`删除「${occurrence.name}」及之后的重复安排？`)) return
  try {
    await trashCalendarSeries({
      id: occurrence.series_id,
      version: occurrence.series_version,
    })
    detailOpen.value = false
    showNotice('安排已移入回收站')
    await loadWeek()
  } catch (reason) {
    error.value = failure(reason)
  }
}

async function completeTask(task: TaskSummary) {
  try {
    await setTaskCompletion(task, crypto.randomUUID(), true)
    tasks.value = tasks.value.filter(item => item.id !== task.id)
    if (selectedTask.value?.id === task.id) {
      taskDetailOpen.value = false
      selectedTask.value = null
    }
    showNotice('任务已完成')
  } catch (reason) {
    error.value = failure(reason)
  }
}

function openTask(task: TaskSummary) {
  selectedTask.value = task
  taskDetailOpen.value = true
}

function taskDueText(task: TaskSummary) {
  const due = task.schedule.due_date
  if (!due) return '没有截止日期'
  if (due === today.value) return '今天'
  if (due === addDays(today.value, 1)) return '明天'
  const date = parseLocalDate(due)
  return `${date.getMonth() + 1}月${date.getDate()}日`
}

function taskRepeatText(task: TaskSummary) {
  const recurrence = task.schedule.recurrence
  if (recurrence.frequency === 'none') return '不重复'
  if (recurrence.frequency === 'daily') return '每天重复'
  if (recurrence.frequency === 'weekdays') return '每个工作日'
  if (recurrence.frequency === 'weekly') {
    const labels = ['一', '二', '三', '四', '五', '六', '日']
    const days = recurrence.weekdays.map(day => labels[day - 1]).join('、')
    return days ? `每周 ${days}` : '每周重复'
  }
  return '重复任务'
}

async function postponeTask(task: TaskSummary) {
  if (taskActionBusy.value) return
  taskActionBusy.value = true
  try {
    const schedule = task.schedule
    await updateTaskSchedule(task, crypto.randomUUID(), {
      due_kind: schedule.due_kind === 'none' ? 'date' : schedule.due_kind,
      due_date: addDays(today.value, 1),
      due_time: schedule.due_time,
      time_zone: schedule.time_zone ?? timeZone.value,
      ambiguous_utc_offset_minutes: schedule.ambiguous_utc_offset_minutes,
      splittable: schedule.splittable,
      minimum_block_minutes: schedule.minimum_block_minutes,
      recurrence: schedule.recurrence,
    })
    taskDetailOpen.value = false
    selectedTask.value = null
    await loadTasks()
    showNotice('已推迟到明天')
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    taskActionBusy.value = false
  }
}

async function removeTask(task: TaskSummary) {
  if (!window.confirm(`将「${task.title}」移入回收站？`)) return
  taskActionBusy.value = true
  try {
    await deleteTask(task, crypto.randomUUID())
    tasks.value = tasks.value.filter(item => item.id !== task.id)
    taskDetailOpen.value = false
    selectedTask.value = null
    showNotice('任务已移入回收站')
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    taskActionBusy.value = false
  }
}

async function quickAdd() {
  const title = quickTitle.value.trim()
  if (!title || quickBusy.value) return
  quickBusy.value = true
  try {
    const task = await createTask(crypto.randomUUID(), {
      title,
      description: '',
      project_id: null,
      estimated_minutes: null,
      priority: 2,
      labels: [],
      links: [],
      steps: [],
    })
    await updateTaskSchedule(task, crypto.randomUUID(), {
      due_kind: 'date',
      due_date: today.value,
      due_time: null,
      time_zone: timeZone.value,
      ambiguous_utc_offset_minutes: null,
      splittable: false,
      minimum_block_minutes: 15,
      recurrence: emptyRecurrence(),
    })
    quickTitle.value = ''
    await loadTasks()
    showNotice('已添加到今天')
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    quickBusy.value = false
  }
}

function openTaskDialog() {
  taskInitial.value = { title: quickTitle.value.trim(), date: today.value }
  taskDialogError.value = ''
  taskDialogOpen.value = true
}

async function submitTask(values: TaskFormValues) {
  taskDialogBusy.value = true
  taskDialogError.value = ''
  try {
    const task = await createTask(crypto.randomUUID(), {
      title: values.title,
      description: '',
      project_id: null,
      estimated_minutes: values.estimatedMinutes,
      priority: 2,
      labels: [],
      links: [],
      steps: [],
    })
    const recurrence = emptyRecurrence()
    if (values.repeat === 'daily') {
      recurrence.frequency = 'daily'
    } else if (values.repeat === 'weekdays') {
      recurrence.frequency = 'weekdays'
    } else if (values.repeat === 'weekly') {
      recurrence.frequency = 'weekly'
      recurrence.weekdays = values.weekdays
    }
    await updateTaskSchedule(task, crypto.randomUUID(), {
      due_kind: 'date',
      due_date: values.date,
      due_time: null,
      time_zone: timeZone.value,
      ambiguous_utc_offset_minutes: null,
      splittable: false,
      minimum_block_minutes: 15,
      recurrence,
    })
    taskDialogOpen.value = false
    quickTitle.value = ''
    await loadTasks()
    showNotice('任务已创建')
  } catch (reason) {
    taskDialogError.value = failure(reason).message
  } finally {
    taskDialogBusy.value = false
  }
}

function askAI(prompt = aiPrompt.value) {
  const text = prompt.trim()
  if (!text) return
  void router.push({ path: '/ai', query: { prompt: text } })
}

async function settingsChanged(message: string) {
  showNotice(message)
  try {
    terms.value = await listTerms()
    const todayValue = today.value
    if (!terms.value.some(term => term.id === termId.value)) {
      termId.value = terms.value.find(term => (
        term.start_date <= todayValue && term.end_date >= todayValue
      ))?.id ?? terms.value.at(-1)?.id ?? null
    }
    await loadWeek()
  } catch (reason) {
    error.value = failure(reason)
  }
}

function taskMeta(task: TaskSummary) {
  const parts: string[] = []
  if (task.schedule.due_time) parts.push(task.schedule.due_time.slice(0, 5))
  if (task.schedule.recurrence.frequency !== 'none') parts.push('重复')
  if (task.estimated_minutes) parts.push(`${task.estimated_minutes} 分钟`)
  if (task.schedule.due_date && task.schedule.due_date !== today.value) {
    parts.push(task.schedule.due_date.slice(5).replace('-', '/'))
  }
  return parts.join(' · ')
}

function eventDateLabel(event: BoardEvent) {
  return weekDays.value[event.dayIndex]?.date ?? ''
}

function eventDateText(event: BoardEvent) {
  const date = parseLocalDate(eventDateLabel(event))
  return `${date.getMonth() + 1}月${date.getDate()}日`
}

function eventKindLabel(event: BoardEvent) {
  if (event.kind === 'course') return '课程'
  if (event.kind === 'fixed') return '固定安排'
  return '任务'
}

const avatarLetter = computed(() => (
  identity.value?.user.email?.slice(0, 1).toUpperCase() ?? 'i'
))
</script>

<template>
  <div class="tt-page">
    <header class="tt-header">
      <RouterLink to="/timetable" class="tt-brand">iTime<span>.</span></RouterLink>
      <nav class="tt-nav" aria-label="主导航">
        <RouterLink to="/timetable" class="active">课表</RouterLink>
        <RouterLink to="/tasks">任务</RouterLink>
        <RouterLink to="/ai">AI</RouterLink>
        <RouterLink to="/reviews">复盘</RouterLink>
      </nav>

      <div class="tt-weeknav">
        <button type="button" class="tt-icon-btn" aria-label="上一周" @click="moveWeek(-1)">
          <ChevronLeft :size="18" />
        </button>
        <button type="button" class="tt-weeklabel" @click="goToday">
          <strong>{{ weekNumber ? `第 ${weekNumber} 周` : '本周' }}</strong>
          <small>{{ formatRange(weekStart) }}</small>
        </button>
        <button type="button" class="tt-icon-btn" aria-label="下一周" @click="moveWeek(1)">
          <ChevronRight :size="18" />
        </button>
        <button type="button" class="tt-today" @click="goToday">今天</button>
      </div>

      <div class="tt-header-actions">
        <button type="button" class="tt-icon-btn add" aria-label="添加课程" @click="openCreateFromHeader">
          <Plus :size="18" />
        </button>
        <RouterLink to="/ai" class="tt-ai-link">
          <Sparkles :size="16" /> AI
        </RouterLink>
        <RouterLink to="/me" class="tt-avatar" :aria-label="`账号 ${identity?.user.email ?? ''}`">
          {{ avatarLetter }}
        </RouterLink>
      </div>
    </header>

    <div v-if="error" class="tt-banner error" role="alert">
      {{ error.message }}
      <button type="button" @click="error = null"><X :size="15" /></button>
    </div>
    <div v-if="notice" class="tt-banner success" role="status">{{ notice }}</div>

    <main class="tt-main">
      <aside class="tt-panel tt-tasks" aria-label="任务">
        <header class="tt-panel-head">
          <div>
            <h2>今天</h2>
            <p>{{ todayLabel }}</p>
          </div>
          <span class="tt-count">{{ todayTaskCount }}</span>
        </header>

        <form class="tt-quickadd" @submit.prevent="quickAdd">
          <input
            v-model="quickTitle"
            type="text"
            maxlength="200"
            placeholder="加一个任务，回车保存"
            :disabled="quickBusy"
          >
          <button
            type="submit"
            class="tt-quickadd-btn"
            :disabled="!quickTitle.trim() || quickBusy"
            aria-label="添加任务"
          >
            <Plus :size="17" />
          </button>
        </form>

        <div v-if="tasksLoading && !openTasks.length" class="tt-skeleton" aria-hidden="true">
          <span v-for="index in 4" :key="index" />
        </div>

        <div v-for="group in taskGroups" :key="group.key" class="tt-task-group">
          <h3>{{ group.title }}</h3>
          <div
            v-for="task in group.items"
            :key="task.id"
            class="tt-task"
          >
            <button
              type="button"
              class="tt-check"
              :aria-label="`完成任务：${task.title}`"
              @click="completeTask(task)"
            >
              <Check :size="12" />
            </button>
            <button type="button" class="tt-task-copy" @click="openTask(task)">
              <strong>{{ task.title }}</strong>
              <small v-if="taskMeta(task)">{{ taskMeta(task) }}</small>
            </button>
          </div>
        </div>

        <p v-if="!tasksLoading && !openTasks.length" class="tt-empty">
          今天还没有任务。<br>
          在上方输入，或让 AI 帮你安排。
        </p>

        <div class="tt-panel-foot">
          <button type="button" @click="openTaskDialog">更多选项</button>
          <RouterLink to="/tasks">全部任务</RouterLink>
        </div>
      </aside>

      <section class="tt-board-section" aria-label="课表">
        <div class="tt-board-top">
          <div>
            <h1>本周课表</h1>
            <p>
              {{ activeTerm?.name || '还没有设置学期' }}
              <template v-if="activeTerm"> · {{ formatRange(weekStart) }}</template>
            </p>
          </div>
          <div class="tt-board-tools">
            <select v-if="terms.length > 1" v-model="termId" aria-label="选择学期">
              <option v-for="term in terms" :key="term.id" :value="term.id">
                {{ term.name }}
              </option>
            </select>
            <button type="button" class="tt-term-btn" @click="settingsOpen = true">
              学期设置
            </button>
            <button type="button" class="tt-add-btn" @click="openCreateFromHeader">
              <CalendarPlus :size="16" /> 添加课程
            </button>
          </div>
        </div>

        <div v-if="!activeTerm" class="tt-term-empty">
          <div>
            <strong>先设置学期</strong>
            <span>填好开学日期和周次，课程就能按周重复显示。</span>
          </div>
          <button type="button" @click="settingsOpen = true">去设置</button>
        </div>

        <TimetableBoard
          :days="weekDays"
          :items="boardEvents"
          :range-start="range.start"
          :range-end="range.end"
          :now-minutes="nowMinutes"
          :loading="loading || weekLoading"
          @select="selectEvent"
          @create="openCreate"
        />

        <p class="tt-board-hint">
          点击空白时间块可以快速添加课程或安排；点击已有块查看详情。
        </p>
      </section>

      <aside class="tt-panel tt-ai" aria-label="AI 助手">
        <header class="tt-ai-head">
          <span class="tt-ai-mark"><Sparkles :size="16" /></span>
          <h2>AI 助手</h2>
          <span class="tt-ai-tag">随时接管</span>
        </header>
        <p class="tt-ai-copy">
          把想法说出来：整理课表、拆解作业、安排今天。AI 会接着你的话做。
        </p>
        <div class="tt-ai-chips">
          <button
            v-for="suggestion in aiSuggestions"
            :key="suggestion"
            type="button"
            @click="askAI(suggestion)"
          >
            {{ suggestion }}
          </button>
        </div>
        <form class="tt-ai-form" @submit.prevent="askAI()">
          <textarea
            v-model="aiPrompt"
            rows="3"
            maxlength="500"
            placeholder="例如：每周三 19:00 健身，持续到期末"
          />
          <button type="submit" :disabled="!aiPrompt.trim()">
            交给 AI <ArrowRight :size="16" />
          </button>
        </form>
        <RouterLink to="/ai" class="tt-ai-all">打开完整对话</RouterLink>
        <p class="tt-ai-note">复杂安排会在对话里继续澄清。</p>
      </aside>
    </main>

    <ElDrawer
      v-model="detailOpen"
      direction="rtl"
      size="min(380px, 94vw)"
      :with-header="false"
      class="tt-detail-drawer"
    >
      <div v-if="selectedEvent" class="tt-detail">
        <button type="button" class="tt-detail-close" aria-label="关闭" @click="detailOpen = false">
          <X :size="18" />
        </button>
        <span class="tt-detail-kind">{{ eventKindLabel(selectedEvent) }}</span>
        <h2>{{ selectedEvent.title }}</h2>
        <dl>
          <div>
            <dt><Clock3 :size="15" /> 时间</dt>
            <dd>{{ eventDateText(selectedEvent) }} {{ minutesToLabel(selectedEvent.startMin) }}–{{ minutesToLabel(selectedEvent.endMin) }}</dd>
          </div>
          <div v-if="selectedEvent.subtitle">
            <dt><MapPin :size="15" /> 地点</dt>
            <dd>{{ selectedEvent.subtitle }}</dd>
          </div>
        </dl>
        <div class="tt-detail-actions">
          <button
            v-if="selectedCanEdit"
            type="button"
            class="tt-button primary"
            @click="editSelected"
          >
            编辑
          </button>
          <button
            v-if="selectedCanEdit"
            type="button"
            class="tt-button danger"
            @click="deleteSelected"
          >
            <Trash2 :size="15" /> 删除
          </button>
          <RouterLink
            v-if="!selectedSeries && selectedEvent.kind === 'task'"
            to="/tasks"
            class="tt-button primary"
          >
            打开任务
          </RouterLink>
        </div>
      </div>
    </ElDrawer>

    <ElDrawer
      v-model="taskDetailOpen"
      direction="rtl"
      size="min(380px, 94vw)"
      :with-header="false"
      class="tt-detail-drawer"
    >
      <div v-if="selectedTask" class="tt-detail">
        <button type="button" class="tt-detail-close" aria-label="关闭" @click="taskDetailOpen = false">
          <X :size="18" />
        </button>
        <span class="tt-detail-kind">任务</span>
        <h2>{{ selectedTask.title }}</h2>
        <p v-if="selectedTask.description" class="tt-detail-desc">
          {{ selectedTask.description }}
        </p>
        <dl>
          <div>
            <dt>截止</dt>
            <dd>
              {{ taskDueText(selectedTask) }}
              <template v-if="selectedTask.schedule.due_time">
                {{ selectedTask.schedule.due_time.slice(0, 5) }}
              </template>
            </dd>
          </div>
          <div>
            <dt>重复</dt>
            <dd>{{ taskRepeatText(selectedTask) }}</dd>
          </div>
          <div v-if="selectedTask.estimated_minutes">
            <dt>预计用时</dt>
            <dd>{{ selectedTask.estimated_minutes }} 分钟</dd>
          </div>
        </dl>
        <div class="tt-detail-actions">
          <button
            type="button"
            class="tt-button primary"
            :disabled="taskActionBusy"
            @click="completeTask(selectedTask)"
          >
            完成
          </button>
          <button
            v-if="selectedTask.schedule.recurrence.frequency === 'none'"
            type="button"
            class="tt-button secondary"
            :disabled="taskActionBusy"
            @click="postponeTask(selectedTask)"
          >
            推迟到明天
          </button>
          <RouterLink to="/tasks" class="tt-button ghost">任务页</RouterLink>
          <button
            type="button"
            class="tt-button danger"
            :disabled="taskActionBusy"
            @click="removeTask(selectedTask)"
          >
            <Trash2 :size="15" /> 删除
          </button>
        </div>
      </div>
    </ElDrawer>

    <TimetableEventDialog
      v-model="eventDialogOpen"
      :mode="eventDialogMode"
      :initial="eventInitial"
      :busy="eventDialogBusy"
      :error="eventDialogError"
      @submit="submitEvent"
    />

    <QuickTaskDialog
      v-model="taskDialogOpen"
      :initial="taskInitial"
      :busy="taskDialogBusy"
      :error="taskDialogError"
      @submit="submitTask"
    />

    <CalendarSettingsDialog
      :open="settingsOpen"
      :settings="settings"
      :terms="terms"
      :browser-time-zone="browserTimeZone"
      @close="settingsOpen = false"
      @changed="settingsChanged"
    />
  </div>
</template>

<style scoped>
.tt-page {
  min-height: 100dvh;
  background:
    radial-gradient(1200px 480px at 12% -10%, color-mix(in srgb, var(--accent) 8%, transparent), transparent 70%),
    var(--canvas);
  color: var(--text);
}
.tt-header {
  position: sticky;
  top: 0;
  z-index: 30;
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 12px clamp(16px, 3vw, 32px);
  background: color-mix(in srgb, var(--canvas) 86%, transparent);
  backdrop-filter: blur(16px);
  border-bottom: 1px solid color-mix(in srgb, var(--border) 70%, transparent);
}
.tt-brand {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -.04em;
  color: var(--text);
  text-decoration: none;
}
.tt-brand span { color: var(--accent); }
.tt-nav {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface) 72%, transparent);
  border: 1px solid color-mix(in srgb, var(--border) 70%, transparent);
}
.tt-nav a {
  padding: 7px 13px;
  border-radius: 9px;
  color: var(--muted);
  font-size: 13px;
  font-weight: 550;
  text-decoration: none;
  transition: background .14s ease, color .14s ease;
}
.tt-nav a:hover { color: var(--text); }
.tt-nav a.active {
  background: var(--surface);
  color: var(--text);
  box-shadow: 0 1px 3px rgb(31 42 36 / 8%);
}
.tt-weeknav {
  display: flex;
  align-items: center;
  gap: 2px;
  margin-left: auto;
}
.tt-icon-btn {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: transparent;
  color: var(--muted);
}
.tt-icon-btn:hover { background: var(--selected); color: var(--text); }
.tt-icon-btn.add {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
}
.tt-weeklabel {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
  min-width: 150px;
  padding: 4px 10px;
  border-radius: 10px;
  background: transparent;
  color: var(--text);
}
.tt-weeklabel:hover { background: var(--selected); }
.tt-weeklabel strong {
  font-size: 14px;
  font-weight: 650;
  line-height: 1.25;
}
.tt-weeklabel small {
  font-size: 11px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.tt-today {
  margin-left: 4px;
  padding: 8px 13px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: var(--surface);
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
}
.tt-today:hover { background: var(--selected); }
.tt-header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}
.tt-ai-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border-radius: 10px;
  background: #1e3529;
  color: #f3f8f4;
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
}
.tt-ai-link:hover { background: #264434; }
.tt-avatar {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--selected);
  color: var(--accent);
  font-size: 13px;
  font-weight: 700;
  text-decoration: none;
}
.tt-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 12px clamp(16px, 3vw, 32px) 0;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 13px;
}
.tt-banner.error {
  background: var(--error-bg);
  color: var(--error);
  border: 1px solid var(--error-border);
}
.tt-banner.success {
  background: var(--selected);
  color: var(--accent);
  border: 1px solid var(--border);
}
.tt-banner button { background: transparent; color: inherit; display: grid; place-items: center; }
.tt-main {
  display: grid;
  grid-template-columns: 288px minmax(0, 1fr) 332px;
  gap: 18px;
  align-items: start;
  max-width: 1660px;
  margin: 0 auto;
  padding: 18px clamp(16px, 3vw, 32px) 48px;
}
.tt-panel {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 20px;
  box-shadow: 0 1px 2px rgb(31 42 36 / 4%), 0 18px 44px -40px rgb(31 42 36 / 35%);
}
.tt-tasks {
  position: sticky;
  top: 86px;
  padding: 18px 16px 14px;
}
.tt-panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
}
.tt-panel-head h2 { font-size: 18px; font-weight: 700; letter-spacing: -.02em; }
.tt-panel-head p { margin-top: 2px; font-size: 12px; color: var(--muted); }
.tt-count {
  display: grid;
  place-items: center;
  min-width: 26px;
  height: 26px;
  padding: 0 8px;
  border-radius: 999px;
  background: var(--selected);
  color: var(--accent);
  font-size: 12px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.tt-quickadd {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 6px;
  padding: 3px 3px 3px 12px;
  margin-bottom: 14px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: color-mix(in srgb, var(--canvas) 55%, var(--surface));
}
.tt-quickadd:focus-within {
  border-color: color-mix(in srgb, var(--accent) 55%, var(--border));
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 10%, transparent);
}
.tt-quickadd input {
  flex: 1;
  min-width: 0;
  padding: 8px 0;
  border: 0;
  background: transparent;
  font-size: 13px;
  outline: none;
}
.tt-quickadd-btn {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  flex-shrink: 0;
  border-radius: 9px;
  background: var(--accent);
  color: var(--on-accent);
}
.tt-quickadd-btn:disabled { opacity: .35; }
.tt-task-group { margin-top: 12px; }
.tt-task-group h3 {
  margin: 0 0 4px;
  font-size: 11px;
  font-weight: 650;
  letter-spacing: .04em;
  color: var(--muted);
}
.tt-task {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  width: 100%;
  padding: 8px 6px;
  border-radius: 10px;
  background: transparent;
  text-align: left;
}
.tt-task:hover { background: color-mix(in srgb, var(--selected) 70%, transparent); }
.tt-check {
  display: grid;
  place-items: center;
  width: 17px;
  height: 17px;
  margin-top: 2px;
  flex-shrink: 0;
  padding: 0;
  border: 1.5px solid color-mix(in srgb, var(--muted) 55%, transparent);
  border-radius: 50%;
  background: transparent;
  color: transparent;
  transition: all .14s ease;
}
.tt-task:hover .tt-check,
.tt-check:focus-visible {
  border-color: var(--accent);
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
}
.tt-task-copy {
  min-width: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
}
.tt-task-copy strong {
  font-size: 13px;
  font-weight: 550;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.tt-task-copy small { font-size: 11px; color: var(--muted); }
.tt-empty {
  margin: 22px 6px;
  font-size: 12.5px;
  line-height: 1.7;
  color: var(--muted);
}
.tt-panel-foot {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid color-mix(in srgb, var(--border) 70%, transparent);
}
.tt-panel-foot button,
.tt-panel-foot a {
  font-size: 12px;
  color: var(--muted);
  background: transparent;
  text-decoration: none;
}
.tt-panel-foot button:hover,
.tt-panel-foot a:hover { color: var(--accent); }
.tt-skeleton { display: grid; gap: 8px; padding: 8px 4px; }
.tt-skeleton span {
  height: 14px;
  border-radius: 6px;
  background: linear-gradient(90deg, var(--selected), transparent);
  animation: shimmer 1.4s ease-in-out infinite;
}
@keyframes shimmer {
  0%, 100% { opacity: .45; }
  50% { opacity: .9; }
}
.tt-board-section { min-width: 0; }
.tt-board-top {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin: 4px 2px 12px;
}
.tt-board-top h1 {
  font-size: clamp(22px, 2.2vw, 30px);
  font-weight: 750;
  letter-spacing: -.035em;
  line-height: 1.15;
}
.tt-board-top p { margin-top: 4px; font-size: 12.5px; color: var(--muted); }
.tt-board-tools { display: flex; align-items: center; gap: 8px; }
.tt-board-tools select {
  min-height: 38px;
  padding: 6px 30px 6px 10px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text);
  font-size: 12px;
}
.tt-add-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 38px;
  padding: 0 14px;
  border-radius: 10px;
  background: var(--accent);
  color: var(--on-accent);
  font-size: 12.5px;
  font-weight: 600;
}
.tt-add-btn:hover { background: var(--accent-hover); }
.tt-term-btn {
  min-height: 38px;
  padding: 0 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--muted);
  font-size: 12px;
  font-weight: 550;
}
.tt-term-btn:hover { background: var(--selected); color: var(--text); }
.tt-term-empty {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
  padding: 14px 16px;
  border: 1px dashed color-mix(in srgb, var(--accent) 35%, var(--border));
  border-radius: 14px;
  background: color-mix(in srgb, var(--accent) 5%, var(--surface));
}
.tt-term-empty div { display: flex; flex-direction: column; gap: 2px; }
.tt-term-empty strong { font-size: 13.5px; }
.tt-term-empty span { font-size: 12px; color: var(--muted); }
.tt-term-empty button {
  min-height: 36px;
  padding: 0 14px;
  border-radius: 9px;
  background: var(--accent);
  color: var(--on-accent);
  font-size: 12.5px;
  font-weight: 600;
  white-space: nowrap;
}
.tt-board-hint {
  margin: 10px 4px 0;
  font-size: 11.5px;
  color: var(--muted);
}
@media (max-width: 720px) {
  .tt-board-hint::after { content: ' · 左右滑动查看周末'; }
}
.tt-ai {
  position: sticky;
  top: 86px;
  padding: 18px;
  background: #1d3529;
  border-color: #2c4b3a;
  color: #eef5ef;
  overflow: hidden;
}
.tt-ai::after {
  content: "";
  position: absolute;
  right: -60px;
  top: -70px;
  width: 190px;
  height: 190px;
  border-radius: 50%;
  background: radial-gradient(circle, rgb(196 224 180 / 18%), transparent 65%);
  pointer-events: none;
}
.tt-ai-head { display: flex; align-items: center; gap: 8px; }
.tt-ai-mark {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 9px;
  background: rgb(255 255 255 / 10%);
  color: #d7ebc9;
}
.tt-ai-head h2 { font-size: 16px; font-weight: 650; }
.tt-ai-tag {
  margin-left: auto;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgb(255 255 255 / 8%);
  color: #b9d3bd;
  font-size: 10.5px;
}
.tt-ai-copy {
  margin: 12px 0 14px;
  font-size: 12.5px;
  line-height: 1.65;
  color: #c4d8c8;
}
.tt-ai-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }
.tt-ai-chips button {
  padding: 7px 10px;
  border-radius: 9px;
  background: rgb(255 255 255 / 7%);
  border: 1px solid rgb(255 255 255 / 9%);
  color: #dcebdd;
  font-size: 11.5px;
  text-align: left;
}
.tt-ai-chips button:hover { background: rgb(255 255 255 / 14%); }
.tt-ai-form { display: flex; flex-direction: column; gap: 8px; }
.tt-ai-form textarea {
  width: 100%;
  resize: vertical;
  min-height: 76px;
  padding: 11px 12px;
  border: 1px solid rgb(255 255 255 / 14%);
  border-radius: 12px;
  background: rgb(255 255 255 / 7%);
  color: #f4f9f4;
  font-size: 13px;
  line-height: 1.55;
}
.tt-ai-form textarea::placeholder { color: #9db6a3; }
.tt-ai-form textarea:focus {
  outline: 2px solid rgb(214 235 201 / 45%);
  outline-offset: 1px;
}
.tt-ai-form button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 40px;
  border-radius: 11px;
  background: #d9ebc9;
  color: #1b3023;
  font-size: 13px;
  font-weight: 650;
}
.tt-ai-form button:hover:not(:disabled) { background: #e6f4d8; }
.tt-ai-form button:disabled { opacity: .45; }
.tt-ai-all {
  display: inline-block;
  margin-top: 12px;
  color: #c7dcc9;
  font-size: 12px;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.tt-ai-note { margin-top: 10px; font-size: 10.5px; color: #93ac98; }
.tt-detail { position: relative; padding: 8px 4px; }
.tt-detail-close {
  position: absolute;
  right: 0;
  top: 0;
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border-radius: 10px;
  background: transparent;
  color: var(--muted);
}
.tt-detail-close:hover { background: var(--selected); }
.tt-detail-kind {
  display: inline-block;
  margin-bottom: 10px;
  padding: 4px 9px;
  border-radius: 999px;
  background: var(--selected);
  color: var(--accent);
  font-size: 11px;
  font-weight: 600;
}
.tt-detail h2 {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -.02em;
  line-height: 1.3;
  padding-right: 36px;
}
.tt-detail dl { display: grid; gap: 14px; margin: 20px 0 26px; }
.tt-detail dt {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 3px;
  font-size: 11.5px;
  color: var(--muted);
}
.tt-detail dd { margin: 0; font-size: 14px; }
.tt-detail-desc {
  margin: 12px 0 0;
  font-size: 13px;
  line-height: 1.65;
  color: var(--muted);
  white-space: pre-wrap;
}
.tt-detail-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.tt-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  min-height: 42px;
  padding: 0 18px;
  border-radius: 11px;
  font-size: 13px;
  font-weight: 600;
  text-decoration: none;
}
.tt-button.primary { background: var(--accent); color: var(--on-accent); }
.tt-button.primary:hover { background: var(--accent-hover); }
.tt-button.secondary {
  border: 1px solid var(--border);
  background: var(--surface);
  color: var(--text);
}
.tt-button.secondary:hover { background: var(--selected); }
.tt-button.ghost { background: transparent; color: var(--muted); }
.tt-button.ghost:hover { background: var(--selected); color: var(--text); }
.tt-button.danger { background: var(--error-bg); color: var(--error); border: 1px solid var(--error-border); }
.tt-button:disabled { opacity: .5; }
@media (max-width: 1380px) {
  .tt-main { grid-template-columns: 270px minmax(0, 1fr); }
  .tt-ai {
    position: static;
    grid-column: 1 / -1;
  }
  .tt-ai-chips { max-width: 720px; }
}
@media (max-width: 980px) {
  .tt-header { flex-wrap: wrap; gap: 10px; }
  .tt-nav { order: 3; width: 100%; justify-content: space-between; }
  .tt-weeknav { margin-left: 0; flex: 1; justify-content: center; }
  .tt-main { grid-template-columns: 1fr; }
  .tt-tasks { position: static; order: 2; }
  .tt-ai { order: 3; }
  .tt-board-section { order: 1; }
  .tt-tasks, .tt-ai { max-width: none; }
}
@media (max-width: 640px) {
  .tt-header { padding: 10px 14px 8px; }
  .tt-brand { font-size: 19px; }
  .tt-weeklabel { min-width: 128px; }
  .tt-weeklabel small { font-size: 10px; }
  .tt-today { display: none; }
  .tt-main { padding: 12px 14px 32px; gap: 14px; }
  .tt-board-top { align-items: flex-start; flex-direction: column; gap: 10px; }
  .tt-board-tools { width: 100%; justify-content: space-between; }
  .tt-ai-link { padding: 8px 10px; }
  .tt-header-actions { gap: 6px; }
  .tt-nav a { padding: 7px 10px; }
}
</style>
