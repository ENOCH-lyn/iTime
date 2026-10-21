<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  ArrowRight,
  CalendarPlus,
  Check,
  ChevronLeft,
  ChevronRight,
  Clock3,
  ImagePlus,
  MapPin,
  Mic,
  NotebookText,
  Plus,
  Sparkles,
  Trash2,
  UserRound,
  X,
} from 'lucide-vue-next'
import TimetableBoard from '../components/TimetableBoard.vue'
import AgentProposalMessageCard from '../components/AgentProposalMessageCard.vue'
import CalendarSettingsDialog from '../components/CalendarSettingsDialog.vue'
import TimetableEventDialog, {
  type CourseSlotForm,
  type CourseWeekMode,
  type EventFormValues,
} from '../components/TimetableEventDialog.vue'
import TimetableImportMessageCard from '../components/TimetableImportMessageCard.vue'
import TaskActionProposalCard from '../components/TaskActionProposalCard.vue'
import CourseActionProposalCard from '../components/CourseActionProposalCard.vue'
import AppShell from '../shell/AppShell.vue'
import { useAppShell } from '../shell/useAppShell'
import QuickTaskDialog, {
  type TaskFormValues,
  type TaskRepeat,
} from '../components/QuickTaskDialog.vue'
import { failure, type Failure } from '../lib/api'
import { getAIJob, type AIJob } from '../lib/ai-jobs'
import { createAIImport } from '../lib/calendar-imports'
import { transcribeVoice } from '../lib/voice'
import { identity, refreshSession } from '../lib/auth'
import {
  getSeries,
  getTerm,
  getTimeSettings,
  listCalendarOccurrences,
  listSeries,
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
  getTask,
  listTasks,
  setTaskCompletion,
  updateTask,
  type TaskSummary,
} from '../lib/tasks'
import {
  listPlanningBlocks,
  type PlanningBlockProjection,
} from '../lib/plans'
import {
  emptyRecurrence,
  listTaskOccurrences,
  updateTaskOccurrenceStatus,
  updateTaskSchedule,
  type TaskOccurrence,
} from '../lib/task-schedules'
import {
  createConversation,
  listConversationMessages,
  sendConversationMessage,
} from '../lib/conversations'
import '../ai-workspace.css'
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
const activeTermDetail = ref<Term | null>(null)
const weekStart = ref(startOfWeek(dateInZone(new Date(), browserTimeZone)))
const calendarOccurrences = ref<CalendarOccurrence[]>([])
const taskOccurrences = ref<TaskOccurrence[]>([])
const planBlocks = ref<PlanningBlockProjection[]>([])
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
const periods = computed(() => activeTermDetail.value?.periods ?? [])
const termWeeks = computed(() => {
  const term = activeTerm.value
  if (!term) return []
  const total = Math.floor(daysBetween(term.first_week_monday, term.end_date) / 7) + 1
  return Array.from({ length: Math.max(1, total) }, (_, index) => index + 1)
})
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

watch(termId, async value => {
  if (!value) {
    activeTermDetail.value = null
    return
  }
  try {
    activeTermDetail.value = await getTerm(value)
  } catch (reason) {
    error.value = failure(reason)
  }
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
  for (const block of planBlocks.value) {
    const blockStart = timeToMinutes(block.local_starts_at)
    const blockEnd = timeToMinutes(block.local_ends_at)
    if (blockStart !== null) start = Math.min(start, blockStart)
    if (blockEnd !== null) end = Math.max(end, blockEnd)
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
  const calendarBySignature = new Map<string, BoardEvent>()
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
    const signature = [
      occurrence.kind,
      occurrence.source_type,
      occurrence.name,
      occurrence.teacher,
      occurrence.location,
      occurrence.local_start_date,
      occurrence.local_start_time,
      occurrence.local_end_time,
      occurrence.status,
      occurrence.exception_action ?? '',
    ].join('|')
    const duplicate = calendarBySignature.get(signature)
    if (duplicate) {
      duplicate.duplicates = [...(duplicate.duplicates ?? []), occurrence]
      duplicate.duplicateCount = (duplicate.duplicateCount ?? 1) + 1
      continue
    }
    const event: BoardEvent = {
      id: `course:${occurrence.series_id ?? occurrence.subscription_event_id}:${occurrence.occurrence_key}`,
      title: occurrence.name,
      subtitle: occurrence.location,
      teacher: occurrence.teacher,
      duplicateCount: 1,
      duplicates: [],
      dayIndex: daysBetween(startDate, occurrence.local_start_date),
      startMin,
      endMin,
      kind: occurrence.kind,
      colorIndex: colorIndexFor(occurrence.name),
      cancelled: occurrence.status !== 'scheduled',
      occurrence,
    }
    calendarBySignature.set(signature, event)
    output.push(event)
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
  for (const block of planBlocks.value) {
    if (block.local_date < startDate || block.local_date > addDays(startDate, 6)) continue
    const startMin = timeToMinutes(block.local_starts_at)
    if (startMin === null) continue
    const endMin = timeToMinutes(block.local_ends_at) ?? startMin + 60
    output.push({
      id: `plan:${block.id ?? block.task_id}:${block.local_date}`,
      title: block.task_title_snapshot,
      subtitle: '计划安排',
      dayIndex: daysBetween(startDate, block.local_date),
      startMin,
      endMin: Math.max(endMin, startMin + 15),
      kind: 'plan',
      colorIndex: 4,
      cancelled: false,
      plan: block,
    })
  }
  return output
})

const openTasks = computed(() => tasks.value.filter(task => (
  task.status === 'open' && task.lifecycle === 'active'
)))

const planByTask = computed(() => {
  const map = new Map<string, PlanningBlockProjection>()
  for (const block of planBlocks.value) {
    if (!map.has(block.task_id)) map.set(block.task_id, block)
  }
  return map
})

const openOccurrenceByTask = computed(() => {
  const map = new Map<string, TaskOccurrence>()
  for (const occurrence of taskOccurrences.value) {
    if (occurrence.status !== 'open') continue
    const current = map.get(occurrence.task_id)
    if (!current || occurrence.due_date < current.due_date) {
      map.set(occurrence.task_id, occurrence)
    }
  }
  return map
})

function effectiveDueDate(task: TaskSummary) {
  if (task.schedule.recurrence.frequency !== 'none') {
    const occurrence = openOccurrenceByTask.value.get(task.id)
    return occurrence?.due_date ?? task.schedule.due_date ?? null
  }
  return task.schedule.due_date ?? planByTask.value.get(task.id)?.local_date ?? null
}

function visibleInCurrentWeek(task: TaskSummary) {
  if (task.schedule.recurrence.frequency === 'none') return true
  return openOccurrenceByTask.value.has(task.id)
}

const taskGroups = computed(() => {
  const overdue: TaskSummary[] = []
  const todayItems: TaskSummary[] = []
  const thisWeek: TaskSummary[] = []
  const later: TaskSummary[] = []
  for (const task of openTasks.value) {
    if (!visibleInCurrentWeek(task)) continue
    const due = effectiveDueDate(task)
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

const todayTaskCount = computed(() => taskGroups.value
  .filter(group => group.key === 'today' || group.key === 'overdue')
  .reduce((sum, group) => sum + group.items.length, 0))

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
const editingSeriesList = ref<CalendarSeries[]>([])
const eventInitial = ref<EventFormValues>({
  name: '',
  kind: 'course',
  teacher: '',
  description: '',
  location: '',
  date: today.value,
  startTime: '09:00',
  endTime: '10:00',
  repeat: true,
  weekdays: [isoWeekday(today.value)],
  startWeek: 1,
  endWeek: 1,
  slots: [],
})

const taskDialogOpen = ref(false)
const taskDialogBusy = ref(false)
const taskDialogError = ref('')
const editingTask = ref<TaskSummary | null>(null)
const taskInitial = ref<{
  title: string
  date: string
  repeat?: TaskRepeat
  weekdays?: number[]
  estimatedMinutes?: number | null
  until?: string | null
}>({ title: '', date: today.value })
const settingsOpen = ref(false)
const showTeacher = ref(localStorage.getItem('itime.showTeacher') !== 'false')
watch(showTeacher, value => {
  localStorage.setItem('itime.showTeacher', String(value))
})
const selectedTask = ref<TaskSummary | null>(null)
const taskDetailOpen = ref(false)
const taskActionBusy = ref(false)
const aiBusy = ref(false)
const aiError = ref('')
const aiReply = ref('')
const aiProposalId = ref<string | null>(null)
const aiTaskProposalId = ref<string | null>(null)
const aiCourseProposalId = ref<string | null>(null)
const aiImportBatchId = ref<string | null>(null)
const aiImageBusy = ref(false)
const aiImageInput = ref<HTMLInputElement | null>(null)
const aiListening = ref(false)
const aiTranscribing = ref(false)
let mediaRecorder: MediaRecorder | null = null
let mediaStream: MediaStream | null = null
let audioChunks: Blob[] = []
const aiConversationId = ref<string | null>(null)
const aiReviewOpen = ref(false)

const defaultTaskRepeatUntil = computed(() => {
  const ends = activeTerm.value?.end_date
  return ends && ends >= today.value ? ends : addDays(today.value, 120)
})

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
    const [calendar, taskPage, planPage] = await Promise.all([
      listCalendarOccurrences({ start, end }),
      listTaskOccurrences(start, end),
      listPlanningBlocks(start, end),
    ])
    calendarOccurrences.value = calendar.items
    taskOccurrences.value = taskPage.items
    planBlocks.value = planPage.items
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
    activeTermDetail.value = termId.value
      ? await getTerm(termId.value)
      : null
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
  mediaRecorder?.stop()
  mediaStream?.getTracks().forEach(track => track.stop())
})

function moveWeek(offset: number) {
  weekStart.value = addDays(weekStart.value, offset * 7)
  void loadWeek()
}

function goToday() {
  weekStart.value = startOfWeek(today.value)
  void loadWeek()
}

function goWeek(event: Event) {
  const value = Number((event.target as HTMLSelectElement).value)
  const term = activeTerm.value
  if (!value || !term) return
  weekStart.value = addDays(term.first_week_monday, (value - 1) * 7)
  void loadWeek()
}

function inferWeekMode(
  weekNumbers: number[],
  startWeek: number,
  endWeek: number,
): CourseWeekMode {
  const range = Array.from(
    { length: Math.max(0, endWeek - startWeek + 1) },
    (_, index) => startWeek + index,
  )
  if (weekNumbers.length === range.length) return 'all'
  if (weekNumbers.every(week => week % 2 === 1)) return 'odd'
  if (weekNumbers.every(week => week % 2 === 0)) return 'even'
  return 'custom'
}

function slotFromSeries(series: CalendarSeries): CourseSlotForm {
  const weekNumbers = series.recurrence.week_numbers ?? []
  const fallbackWeeks = Math.max(1, termWeeks.value.length)
  const startWeek = weekNumbers.length ? Math.min(...weekNumbers) : 1
  const endWeek = weekNumbers.length
    ? Math.min(Math.max(...weekNumbers), fallbackWeeks)
    : fallbackWeeks
  return {
    id: series.id,
    version: series.version,
    weekdays: series.recurrence.weekdays.length
      ? [...series.recurrence.weekdays]
      : [isoWeekday(series.start_date)],
    startTime: (series.starts_at || '09:00').slice(0, 5),
    endTime: (series.ends_at || '10:00').slice(0, 5),
    location: series.location,
    startWeek,
    endWeek,
    weekMode: inferWeekMode(weekNumbers, startWeek, endWeek),
    weekNumbers: [...weekNumbers],
  }
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
  editingSeriesList.value = []
  eventDialogError.value = ''
  eventInitial.value = {
    name: '',
    kind: 'course',
    teacher: '',
    description: '',
    location: '',
    date,
    startTime: minutesToLabel(startMinutes),
    endTime: minutesToLabel(Math.min(startMinutes + 60, 24 * 60 - 30)),
    repeat: true,
    weekdays: [isoWeekday(date)],
    startWeek: 1,
    endWeek: Math.max(1, termWeeks.value.length),
    slots: [{
      id: null,
      version: null,
      weekdays: [isoWeekday(date)],
      startTime: minutesToLabel(startMinutes),
      endTime: minutesToLabel(Math.min(startMinutes + 60, 24 * 60 - 30)),
      location: '',
      startWeek: 1,
      endWeek: Math.max(1, termWeeks.value.length),
      weekMode: 'all',
      weekNumbers: [],
    }],
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
    const group = series.course_group_id
      ? (await listSeries()).filter(
        item => item.course_group_id === series.course_group_id,
      )
      : [series]
    editingSeriesList.value = group.length ? group : [series]
    editingSeries.value = editingSeriesList.value[0] ?? series
    const weekNumbers = series.recurrence.week_numbers ?? []
    const fallbackWeeks = Math.max(1, termWeeks.value.length)
    eventDialogMode.value = 'edit'
    eventDialogError.value = ''
    eventInitial.value = {
      name: series.name,
      kind: series.kind,
      teacher: series.teacher,
      description: series.description,
      location: series.location,
      date: series.start_date,
      startTime: (series.starts_at || '09:00').slice(0, 5),
      endTime: (series.ends_at || '10:00').slice(0, 5),
      repeat: series.recurrence.frequency !== 'none',
      weekdays: series.recurrence.weekdays.length
        ? [...series.recurrence.weekdays]
        : [isoWeekday(series.start_date)],
      startWeek: weekNumbers.length ? Math.min(...weekNumbers) : 1,
      endWeek: weekNumbers.length
        ? Math.min(Math.max(...weekNumbers), fallbackWeeks)
        : fallbackWeeks,
      slots: series.kind === 'course'
        ? editingSeriesList.value.map(slotFromSeries)
        : [],
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
    const term = activeTerm.value
    if (values.kind === 'course') {
      if (!term) {
        throw new Error('请先在设置里创建学期，再添加课程。')
      }
      if (!values.slots.length) {
        throw new Error('课程至少需要一个时间段。')
      }
      const wasEditing = editingSeriesList.value.length > 0
      const groupId = editingSeriesList.value[0]?.course_group_id
        ?? crypto.randomUUID()
      const existingById = new Map(
        editingSeriesList.value.map(item => [item.id, item]),
      )
      const keptIds = new Set<string>()
      for (const slot of values.slots) {
        const weekdays = [...slot.weekdays].sort((left, right) => left - right)
        const weekNumbers = [...slot.weekNumbers].sort((left, right) => left - right)
        if (!weekdays.length || !weekNumbers.length) {
          throw new Error('每个时间段都要有上课日和有效周次。')
        }
        const recurrence = {
          ...emptyRecurrence(),
          frequency: 'weekly' as const,
          weekdays,
          week_numbers: weekNumbers,
        }
        const draft: SeriesDraft = {
          kind: 'course',
          term_id: term.id,
          name: values.name,
          teacher: values.teacher,
          description: values.description,
          location: slot.location || values.location,
          course_group_id: groupId,
          busy: true,
          time_zone: timeZone.value,
          start_date: null,
          starts_at: `${slot.startTime}:00`,
          ends_at: `${slot.endTime}:00`,
          end_day_offset: 0,
          start_period_number: null,
          end_period_number: null,
          ambiguous_utc_offset_minutes: null,
          recurrence,
        }
        const existing = slot.id ? existingById.get(slot.id) : null
        const saved = await saveSeries(existing ?? null, draft)
        keptIds.add(saved.series.id)
        if (existing) keptIds.add(existing.id)
      }
      for (const item of editingSeriesList.value) {
        if (!keptIds.has(item.id)) {
          await trashCalendarSeries(item)
        }
      }
      editingSeries.value = null
      eventDialogOpen.value = false
      detailOpen.value = false
      showNotice(wasEditing ? '课程已保存' : '课程已加入课表')
      await loadWeek()
      return
    }

    let recurrence = emptyRecurrence()
    if (values.repeat) {
      if (!term) {
        throw new Error('重复安排需要先设置学期。')
      }
      recurrence = {
        ...emptyRecurrence(),
        frequency: 'weekly',
        weekdays: values.weekdays.length ? values.weekdays : [isoWeekday(values.date)],
        until: term.end_date,
      }
    }
    const draft: SeriesDraft = {
      kind: 'fixed',
      term_id: term?.id ?? null,
      name: values.name,
      teacher: '',
      description: values.description,
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

async function mergeDuplicateCourses(event: BoardEvent) {
  const duplicates = event.duplicates ?? []
  const canonicalId = event.occurrence?.series_id
  const bySeries = new Map<string, CalendarOccurrence>()
  for (const occurrence of duplicates) {
    if (occurrence.series_id && occurrence.series_id !== canonicalId) {
      bySeries.set(occurrence.series_id, occurrence)
    }
  }
  if (!bySeries.size) return
  if (!window.confirm(
    `发现 ${event.duplicateCount} 条相同的「${event.title}」。保留一条，清理其余 ${bySeries.size} 条课程数据？`,
  )) return
  try {
    for (const [seriesId, occurrence] of bySeries) {
      if (occurrence.series_version === null) continue
      await trashCalendarSeries({
        id: seriesId,
        version: occurrence.series_version,
      })
    }
    detailOpen.value = false
    showNotice(`已清理 ${bySeries.size} 条重复课程`)
    await loadWeek()
  } catch (reason) {
    error.value = failure(reason)
  }
}

async function deleteSelected() {  const occurrence = selectedEvent.value?.occurrence
  if (!occurrence?.series_id || occurrence.series_version === null) return
  try {
    const series = await getSeries(occurrence.series_id)
    const group = series.course_group_id
      ? (await listSeries()).filter(
        item => item.course_group_id === series.course_group_id,
      )
      : []
    const targets = group.length ? group : [series]
    const suffix = targets.length > 1 ? `的 ${targets.length} 个时间段` : ''
    if (!window.confirm(`删除「${occurrence.name}」${suffix}及之后的重复安排？`)) return
    for (const item of targets) {
      await trashCalendarSeries(item)
    }
    detailOpen.value = false
    showNotice('课程已移入回收站')
    await loadWeek()
  } catch (reason) {
    error.value = failure(reason)
  }
}

async function completeTask(task: TaskSummary) {
  try {
    if (task.schedule.recurrence.frequency !== 'none') {
      const start = today.value
      const page = await listTaskOccurrences(start, addDays(start, 14))
      const occurrence = page.items.find(
        item => item.task_id === task.id && item.status === 'open',
      )
      if (!occurrence) {
        showNotice('近两周没有可完成的重复实例')
        return
      }
      await updateTaskOccurrenceStatus(
        occurrence,
        crypto.randomUUID(),
        'completed',
      )
      if (selectedTask.value?.id === task.id) {
        taskDetailOpen.value = false
        selectedTask.value = null
      }
      await Promise.all([loadWeek(), loadTasks()])
      showNotice('本次重复任务已完成')
      return
    }
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
  const plan = planByTask.value.get(task.id)
  const due = task.schedule.due_date
  if (!due && plan) {
    const date = parseLocalDate(plan.local_date)
    return `已排入 ${date.getMonth() + 1}月${date.getDate()}日 ${plan.local_starts_at.slice(0, 5)}`
  }
  if (!due) return '没有截止日期'
  if (due === today.value) return '今天'
  if (due === addDays(today.value, 1)) return '明天'
  const date = parseLocalDate(due)
  return `${date.getMonth() + 1}月${date.getDate()}日`
}

function taskRepeatText(task: TaskSummary) {
  const recurrence = task.schedule.recurrence
  const suffix = recurrence.until
    ? ` · 至 ${parseLocalDate(recurrence.until).getMonth() + 1}月${parseLocalDate(recurrence.until).getDate()}日`
    : recurrence.count
      ? ` · ${recurrence.count} 次`
      : ''
  if (recurrence.frequency === 'none') return '不重复'
  if (recurrence.frequency === 'daily') return `每天重复${suffix}`
  if (recurrence.frequency === 'weekdays') return `每个工作日${suffix}`
  if (recurrence.frequency === 'weekly') {
    const labels = ['一', '二', '三', '四', '五', '六', '日']
    const days = recurrence.weekdays.map(day => labels[day - 1]).join('、')
    return days ? `每周 ${days}${suffix}` : `每周重复${suffix}`
  }
  if (recurrence.frequency === 'monthly') return `每月 ${recurrence.day_of_month ?? ''} 日${suffix}`
  if (recurrence.frequency === 'yearly') return `每年 ${recurrence.month ?? ''}月${recurrence.day_of_month ?? ''}日${suffix}`
  return `重复任务${suffix}`
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

function taskRepeatFromFrequency(frequency: string): TaskRepeat {
  return ['daily', 'weekdays', 'weekly', 'monthly', 'yearly'].includes(frequency)
    ? frequency as TaskRepeat
    : 'none'
}

function buildTaskRecurrence(
  values: TaskFormValues,
  existing?: TaskSummary['schedule']['recurrence'],
) {
  if (values.repeat === 'none') return emptyRecurrence()
  const recurrence = emptyRecurrence()
  const keepCount = !!existing
    && existing.frequency === values.repeat
    && existing.count !== null
    && existing.until === null
  if (existing && existing.frequency === values.repeat) {
    recurrence.interval = existing.interval || 1
    recurrence.week_numbers = [...existing.week_numbers]
  }
  if (keepCount) {
    recurrence.count = existing?.count ?? null
    recurrence.until = null
  } else {
    recurrence.until = values.until
    recurrence.count = null
  }
  recurrence.frequency = values.repeat
  if (values.repeat === 'weekly') {
    recurrence.weekdays = values.weekdays
  } else if (values.repeat === 'monthly' || values.repeat === 'yearly') {
    const date = parseLocalDate(values.date)
    recurrence.day_of_month = date.getDate()
    if (values.repeat === 'yearly') recurrence.month = date.getMonth() + 1
  }
  return recurrence
}

function openTaskDialog() {
  editingTask.value = null
  taskInitial.value = {
    title: quickTitle.value.trim(),
    date: today.value,
    repeat: 'none',
    weekdays: [isoWeekday(today.value)],
    estimatedMinutes: null,
    until: defaultTaskRepeatUntil.value,
  }
  taskDialogError.value = ''
  taskDialogOpen.value = true
}

function editTask(task: TaskSummary) {
  editingTask.value = task
  const date = task.schedule.due_date ?? today.value
  taskInitial.value = {
    title: task.title,
    date,
    repeat: taskRepeatFromFrequency(task.schedule.recurrence.frequency),
    weekdays: task.schedule.recurrence.weekdays.length
      ? [...task.schedule.recurrence.weekdays]
      : [isoWeekday(date)],
    estimatedMinutes: task.estimated_minutes,
    until: task.schedule.recurrence.until ?? defaultTaskRepeatUntil.value,
  }
  taskDetailOpen.value = false
  taskDialogError.value = ''
  taskDialogOpen.value = true
}

async function submitTask(values: TaskFormValues) {
  taskDialogBusy.value = true
  taskDialogError.value = ''
  try {
    if (editingTask.value) {
      const detail = await getTask(editingTask.value.id)
      const updated = await updateTask(detail.id, crypto.randomUUID(), detail.version, {
        title: values.title,
        description: detail.description,
        project_id: detail.project?.id ?? null,
        estimated_minutes: values.estimatedMinutes,
        priority: detail.priority,
        labels: detail.labels.map(label => label.name),
        links: detail.links.map(link => ({ title: link.title, url: link.url })),
      })
      await updateTaskSchedule(updated, crypto.randomUUID(), {
        due_kind: detail.schedule.due_kind === 'none' ? 'date' : detail.schedule.due_kind,
        due_date: values.date,
        due_time: detail.schedule.due_time,
        time_zone: detail.schedule.time_zone ?? timeZone.value,
        ambiguous_utc_offset_minutes: detail.schedule.ambiguous_utc_offset_minutes,
        splittable: detail.schedule.splittable,
        minimum_block_minutes: detail.schedule.minimum_block_minutes,
        recurrence: buildTaskRecurrence(values, detail.schedule.recurrence),
      })
      editingTask.value = null
      showNotice('任务已更新')
    } else {
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
      await updateTaskSchedule(task, crypto.randomUUID(), {
        due_kind: 'date',
        due_date: values.date,
        due_time: null,
        time_zone: timeZone.value,
        ambiguous_utc_offset_minutes: null,
        splittable: false,
        minimum_block_minutes: 15,
        recurrence: buildTaskRecurrence(values),
      })
      quickTitle.value = ''
      showNotice('任务已创建')
    }
    taskDialogOpen.value = false
    await loadTasks()
  } catch (reason) {
    taskDialogError.value = failure(reason).message
  } finally {
    taskDialogBusy.value = false
  }
}

async function waitForAIJob(jobId: string) {
  const deadline = Date.now() + 180_000
  while (Date.now() < deadline) {
    const job = await getAIJob(jobId)
    if (['succeeded', 'failed', 'cancelled', 'timed_out'].includes(job.status)) {
      return job
    }
    await new Promise(resolve => window.setTimeout(resolve, 1200))
  }
  throw new Error('AI 响应超时，请稍后重试。')
}

async function askAI(prompt = aiPrompt.value) {  const text = prompt.trim()
  if (!text || aiBusy.value) return
  aiBusy.value = true
  aiError.value = ''
  aiReply.value = ''
  aiProposalId.value = null
  aiTaskProposalId.value = null
  aiCourseProposalId.value = null
  aiImportBatchId.value = null
  try {
    const created = await createConversation(crypto.randomUUID())
    const conversation = created.conversation
    aiConversationId.value = conversation.id
    const result = await sendConversationMessage(
      conversation,
      crypto.randomUUID(),
      text,
      'auto',
    )
    aiPrompt.value = ''
    const job = await waitForAIJob(result.job.id)
    if (job.error) throw new Error(job.error.message)
    const output = job.output
    if (output?.proposal_kind === 'task_action' && output.proposal_id) {
      aiTaskProposalId.value = output.proposal_id
    } else if (output?.proposal_kind === 'course_action' && output.proposal_id) {
      aiCourseProposalId.value = output.proposal_id
    } else if (output?.proposal_id) {
      aiProposalId.value = output.proposal_id
    }
    if (output?.import_batch_id) {
      aiImportBatchId.value = output.import_batch_id
      aiReviewOpen.value = true
    }
    const page = await listConversationMessages(conversation.id)
    const assistant = [...page.items].reverse().find(
      message => message.role === 'assistant' && message.content,
    )
    aiReply.value = assistant?.content ?? ''
  } catch (reason) {
    aiError.value = reason instanceof Error
      ? reason.message
      : failure(reason).message
  } finally {
    aiBusy.value = false
  }
}

function openImagePicker() {
  if (!aiImageBusy.value) aiImageInput.value?.click()
}

async function handleImageSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || aiImageBusy.value) return
  aiImageBusy.value = true
  aiError.value = ''
  aiReply.value = ''
  aiProposalId.value = null
  aiTaskProposalId.value = null
  aiCourseProposalId.value = null
  aiImportBatchId.value = null
  try {
    const uploaded = await createAIImport({ sourceType: 'image', file })
    let job: AIJob = uploaded.job
    for (let attempt = 0; attempt < 120; attempt += 1) {
      if (['succeeded', 'failed', 'cancelled', 'timed_out'].includes(job.status)) {
        break
      }
      await new Promise(resolve => window.setTimeout(resolve, 1500))
      job = await getAIJob(job.id)
    }
    if (job.status !== 'succeeded') {
      throw new Error(job.error?.message ?? '图片识别失败，请重试。')
    }
    aiImportBatchId.value = uploaded.batch.id
    aiReviewOpen.value = true
    aiReply.value = '图片课表已识别，请核对候选课程后再确认。'
  } catch (reason) {
    aiError.value = reason instanceof Error
      ? reason.message
      : failure(reason).message
  } finally {
    aiImageBusy.value = false
  }
}

function toggleVoice() {
  if (aiTranscribing.value) return
  if (aiListening.value && mediaRecorder) {
    mediaRecorder.stop()
    return
  }
  void startRecording()
}

async function startRecording() {
  if (
    !navigator.mediaDevices?.getUserMedia
    || typeof MediaRecorder === 'undefined'
  ) {
    showNotice('当前浏览器不支持录音，请直接输入课表')
    return
  }
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
    audioChunks = []
    const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
      ? 'audio/webm;codecs=opus'
      : ''
    mediaRecorder = new MediaRecorder(
      mediaStream,
      mimeType ? { mimeType } : undefined,
    )
    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) audioChunks.push(event.data)
    }
    mediaRecorder.onstop = () => {
      aiListening.value = false
      const blob = new Blob(audioChunks, {
        type: mediaRecorder?.mimeType || 'audio/webm',
      })
      mediaStream?.getTracks().forEach(track => track.stop())
      mediaStream = null
      if (!blob.size) {
        showNotice('没有录到声音，请重试或直接输入')
        return
      }
      aiTranscribing.value = true
      void transcribeVoice(blob)
        .then(({ text }) => {
          if (!text.trim()) {
            showNotice('没有听清，请重试或直接输入课表')
            return
          }
          aiPrompt.value = `${aiPrompt.value.trim()} ${text.trim()}`.trim()
        })
        .catch((reason) => {
          showNotice(failure(reason).message)
        })
        .finally(() => {
          aiTranscribing.value = false
        })
    }
    mediaRecorder.start()
    aiListening.value = true
  } catch {
    showNotice('需要麦克风权限，或直接输入课表')
  }
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
    activeTermDetail.value = termId.value
      ? await getTerm(termId.value)
      : null
    await loadWeek()
  } catch (reason) {
    error.value = failure(reason)
  }
}

async function aiProposalApplied() {
  showNotice('AI 安排已写入')
  await Promise.all([loadWeek(), loadTasks()])
}

function taskMeta(task: TaskSummary) {
  const parts: string[] = []
  if (task.schedule.due_time) parts.push(task.schedule.due_time.slice(0, 5))
  if (task.schedule.recurrence.frequency !== 'none') parts.push('重复')
  if (task.estimated_minutes) parts.push(`${task.estimated_minutes} 分钟`)
  const plan = planByTask.value.get(task.id)
  if (!task.schedule.due_date && plan) {
    parts.push(`排期 ${plan.local_date.slice(5).replace('-', '/')}`)
  } else if (task.schedule.due_date && task.schedule.due_date !== today.value) {
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
  if (event.kind === 'plan') return '计划'
  return '任务'
}

const { pendingLogout, logout } = useAppShell()
</script>

<template>
  <AppShell
    current="timetable"
    title="课表"
    :pending-logout="pendingLogout"
    @logout="logout"
  >
  <div class="tt-page">
    <header class="tt-header">
      <div class="tt-weeknav">
        <button type="button" class="tt-icon-btn" aria-label="上一周" @click="moveWeek(-1)">
          <ChevronLeft :size="18" />
        </button>
        <div class="tt-weeklabel">
          <select
            v-if="termWeeks.length"
            class="tt-week-select"
            :value="weekNumber ?? ''"
            aria-label="选择周次"
            @change="goWeek"
          >
            <option v-if="weekNumber === null" value="">学期外</option>
            <option v-for="week in termWeeks" :key="week" :value="week">
              第 {{ week }} 周
            </option>
          </select>
          <strong v-else>本周</strong>
          <small>{{ formatRange(weekStart) }}</small>
        </div>
        <button type="button" class="tt-icon-btn" aria-label="下一周" @click="moveWeek(1)">
          <ChevronRight :size="18" />
        </button>
        <button type="button" class="tt-today" @click="goToday">今天</button>
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
          :periods="periods"
          :range-start="range.start"
          :range-end="range.end"
          :now-minutes="nowMinutes"
          :loading="loading || weekLoading"
          :show-teacher="showTeacher"
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
          <button type="submit" :disabled="!aiPrompt.trim() || aiBusy">
            {{ aiBusy ? 'AI 正在安排…' : '交给 AI' }} <ArrowRight :size="16" />
          </button>
        </form>

        <div class="tt-ai-image-row">
          <button
            type="button"
            class="tt-ai-image"
            :disabled="aiImageBusy"
            @click="openImagePicker"
          >
            <ImagePlus :size="15" aria-hidden="true" />
            {{ aiImageBusy ? '正在识别图片…' : '图片课表' }}
          </button>
          <button
            type="button"
            class="tt-ai-voice"
            :class="{ listening: aiListening }"
            @click="toggleVoice"
          >
            <Mic :size="15" aria-hidden="true" />
            {{ aiListening ? '录音中…' : (aiTranscribing ? '识别中…' : '口述') }}
          </button>
          <span>截图 / 相册 / 语音，自动识别课程</span>
          <input
            ref="aiImageInput"
            class="tt-ai-image-input"
            type="file"
            accept="image/*"
            @change="handleImageSelected"
          >
        </div>

        <div v-if="aiBusy" class="tt-ai-running" role="status">
          <span class="tt-ai-running-dot" /> 正在读取课表并生成安排
        </div>
        <p v-if="aiError" class="tt-ai-error" role="alert">{{ aiError }}</p>
        <p v-if="aiReply && !aiProposalId && !aiTaskProposalId && !aiCourseProposalId && !aiImportBatchId" class="tt-ai-reply">
          {{ aiReply }}
        </p>
        <AgentProposalMessageCard
          v-if="aiProposalId"
          :proposal-id="aiProposalId"
          class="tt-ai-proposal"
          @applied="aiProposalApplied"
        />
        <TaskActionProposalCard
          v-if="aiTaskProposalId"
          :proposal-id="aiTaskProposalId"
          class="tt-ai-proposal"
          @applied="aiProposalApplied"
        />
        <CourseActionProposalCard
          v-if="aiCourseProposalId"
          :proposal-id="aiCourseProposalId"
          class="tt-ai-proposal"
          @applied="aiProposalApplied"
        />
        <button
          v-if="aiImportBatchId"
          type="button"
          class="tt-ai-review"
          @click="aiReviewOpen = true"
        >
          查看并确认课表导入
        </button>

        <RouterLink
          v-if="aiConversationId"
          :to="{ path: '/ai', query: { conversation: aiConversationId } }"
          class="tt-ai-all"
        >
          在完整对话中继续
        </RouterLink>
        <RouterLink v-else to="/ai" class="tt-ai-all">打开完整对话</RouterLink>
        <p class="tt-ai-note">AI 只会生成预览，确认后才会写入课表或任务。</p>
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
          <div v-if="selectedEvent.occurrence?.teacher">
            <dt><UserRound :size="15" /> 教师</dt>
            <dd>{{ selectedEvent.occurrence.teacher }}</dd>
          </div>
        </dl>
        <div v-if="(selectedEvent.duplicateCount ?? 1) > 1" class="tt-duplicate-card">
          <strong>发现 {{ selectedEvent.duplicateCount }} 条完全相同的课程</strong>
          <p>它们会平分同一列宽度，看起来像“半宽课程”。可以保留一条，清理其余重复数据。</p>
          <button type="button" @click="mergeDuplicateCourses(selectedEvent)">
            保留一条并清理重复
          </button>
        </div>
        <p v-if="selectedEvent.occurrence?.description" class="tt-detail-desc">
          <NotebookText :size="15" aria-hidden="true" />
          {{ selectedEvent.occurrence.description }}
        </p>
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
          <RouterLink
            v-if="selectedEvent.kind === 'plan'"
            to="/plans"
            class="tt-button secondary"
          >
            查看规划
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
            <dt>{{ selectedTask.schedule.due_date ? '截止' : '排期' }}</dt>
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
            type="button"
            class="tt-button secondary"
            :disabled="taskActionBusy"
            @click="editTask(selectedTask)"
          >
            编辑
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
      :week-count="Math.max(1, termWeeks.length)"
      :initial="eventInitial"
      :busy="eventDialogBusy"
      :error="eventDialogError"
      @submit="submitEvent"
    />

    <QuickTaskDialog
      v-model="taskDialogOpen"
      :mode="editingTask ? 'edit' : 'create'"
      :default-until="defaultTaskRepeatUntil"
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
      :show-teacher="showTeacher"
      :show-display-settings="true"
      @update:show-teacher="showTeacher = $event"
      @close="settingsOpen = false"
      @changed="settingsChanged"
    />

    <ElDialog
      v-model="aiReviewOpen"
      title="确认课表导入"
      width="min(780px, 94vw)"
      align-center
      @closed="loadWeek"
    >
      <p v-if="aiReply" class="tt-ai-review-copy">{{ aiReply }}</p>
      <TimetableImportMessageCard
        v-if="aiImportBatchId"
        :batch-id="aiImportBatchId"
      />
    </ElDialog>
  </div>
  </AppShell>
</template>

<style scoped>
.tt-page {
  min-height: 100%;
  overflow-x: clip;
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
  max-width: 100%;
  padding: 3px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface) 72%, transparent);
  border: 1px solid color-mix(in srgb, var(--border) 70%, transparent);
  overflow-x: auto;
  scrollbar-width: none;
}
.tt-nav a { flex: 0 0 auto; }
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
  padding: 2px 10px;
  border-radius: 10px;
  background: transparent;
  color: var(--text);
}
.tt-weeklabel strong {
  font-size: 14px;
  font-weight: 650;
  line-height: 1.25;
}
.tt-week-select {
  max-width: 140px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--text);
  font-size: 14px;
  font-weight: 650;
  line-height: 1.25;
  text-align: center;
  text-align-last: center;
  cursor: pointer;
}
.tt-week-select:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
  border-radius: 4px;
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
.tt-ai-image-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}
.tt-ai-image {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  padding: 0 10px;
  border: 1px solid rgb(255 255 255 / 16%);
  border-radius: 9px;
  background: rgb(255 255 255 / 7%);
  color: #dbe9dc;
  font-size: 11.5px;
  font-weight: 550;
}
.tt-ai-voice {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-height: 32px;
  padding: 0 10px;
  border: 1px solid rgb(255 255 255 / 16%);
  border-radius: 9px;
  background: rgb(255 255 255 / 7%);
  color: #dbe9dc;
  font-size: 11.5px;
  font-weight: 550;
}
.tt-ai-voice:hover { background: rgb(255 255 255 / 12%); }
.tt-ai-voice.listening {
  border-color: #e7a08c;
  background: rgb(231 160 140 / 18%);
  color: #ffd9cf;
}
.tt-ai-image:hover { background: rgb(255 255 255 / 12%); }
.tt-ai-image:disabled { opacity: .55; }
.tt-ai-image-row > span { font-size: 10.5px; color: #8fa99a; }
.tt-ai-image-input { display: none; }
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
.tt-ai-running {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  font-size: 12px;
  color: #c4d8c8;
}
.tt-ai-running-dot {
  width: 7px;
  height: 7px;
  flex-shrink: 0;
  border-radius: 50%;
  background: #d9ebc9;
  animation: tt-pulse 1.1s ease-in-out infinite;
}
@keyframes tt-pulse {
  0%, 100% { opacity: .35; transform: scale(.8); }
  50% { opacity: 1; transform: scale(1); }
}
.tt-ai-error {
  margin: 12px 0 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgb(158 61 54 / 22%);
  color: #ffc2b8;
  font-size: 12px;
  line-height: 1.55;
}
.tt-ai-reply {
  margin: 12px 0 0;
  padding: 10px 12px;
  border-radius: 10px;
  background: rgb(255 255 255 / 7%);
  color: #e7f2e7;
  font-size: 12.5px;
  line-height: 1.6;
  white-space: pre-wrap;
}
.tt-ai-proposal {
  margin-top: 12px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  color: var(--text);
}
.tt-ai-review {
  width: 100%;
  min-height: 40px;
  margin-top: 12px;
  border-radius: 10px;
  background: #d9ebc9;
  color: #1b3023;
  font-size: 12.5px;
  font-weight: 650;
}
.tt-ai-review:hover { background: #e6f4d8; }
.tt-ai-review-copy {
  margin: 0 0 12px;
  padding: 10px 12px;
  border-radius: 10px;
  background: var(--canvas);
  color: var(--muted);
  font-size: 13px;
  line-height: 1.65;
}
.tt-ai-all {
  display: inline-block;
  margin-top: 12px;
  color: #c7dcc9;
  font-size: 12px;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.tt-ai-proposal :deep(.message-proposal) {
  background: transparent;
  border: 0;
  padding: 0;
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
  display: flex;
  align-items: flex-start;
  gap: 7px;
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
  .tt-today { white-space: nowrap; }
  .tt-header-actions { flex: 0 0 auto; }
  .tt-main { grid-template-columns: 1fr; }
  .tt-tasks { position: static; order: 2; }
  .tt-ai { order: 3; }
  .tt-board-section { order: 1; }
  .tt-tasks, .tt-ai { max-width: none; }
}
@media (max-width: 640px) {
  .tt-header { padding: 10px 14px 8px; }
  .tt-brand { font-size: 19px; }
  .tt-weeklabel { min-width: 118px; }
  .tt-weeklabel small { font-size: 10px; }
  .tt-today { padding: 6px 9px; font-size: 11px; }
  .tt-main { padding: 12px 14px 32px; gap: 14px; }
  .tt-board-top { align-items: flex-start; flex-direction: column; gap: 10px; }
  .tt-board-tools { width: 100%; justify-content: space-between; }
  .tt-ai-link { padding: 8px 10px; }
  .tt-header-actions { gap: 6px; }
  .tt-nav a { padding: 7px 10px; }
}
</style>
