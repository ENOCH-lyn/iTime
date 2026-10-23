<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Plus,
  Repeat2,
  Trash2,
  X,
} from 'lucide-vue-next'
import QuickTaskDialog, {
  type TaskFormValues,
  type TaskRepeat,
} from '../components/QuickTaskDialog.vue'
import AppShell from '../shell/AppShell.vue'
import { useAppShell } from '../shell/useAppShell'
import { failure, type Failure } from '../lib/api'
import { identity, refreshSession } from '../lib/auth'
import {
  getTimeSettings,
  listTerms,
  saveTimeSettings,
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
  emptyRecurrence,
  listTaskOccurrences,
  updateTaskOccurrenceStatus,
  updateTaskSchedule,
} from '../lib/task-schedules'
import {
  addDays,
  dateInZone,
  parseLocalDate,
} from '../lib/timetable'

type Filter = 'today' | 'week' | 'all' | 'completed'

const router = useRouter()
const browserTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai'

const loading = ref(true)
const tasksLoading = ref(false)
const error = ref<Failure | null>(null)
const notice = ref('')
const tasks = ref<TaskSummary[]>([])
const filter = ref<Filter>('all')
const search = ref('')
const settings = ref<TimeSettings | null>(null)
const terms = ref<Term[]>([])
const timeZone = computed(() => settings.value?.time_zone ?? browserTimeZone)
const today = computed(() => dateInZone(new Date(), timeZone.value))
const activeTerm = computed(() => (
  terms.value.find(term => term.start_date <= today.value && term.end_date >= today.value)
  ?? terms.value.at(-1)
  ?? null
))
const defaultUntil = computed(() => {
  const end = activeTerm.value?.end_date
  return end && end >= today.value ? end : addDays(today.value, 120)
})

const selectedTask = ref<TaskSummary | null>(null)
const detailOpen = ref(false)
const taskActionBusy = ref(false)
const dialogOpen = ref(false)
const dialogBusy = ref(false)
const dialogError = ref('')
const editingTask = ref<TaskSummary | null>(null)
const taskInitial = ref<{
  title: string
  date: string
  repeat?: TaskRepeat
  weekdays?: number[]
  estimatedMinutes?: number | null
  priority?: number
  until?: string | null
}>({ title: '', date: today.value })
const quickTitle = ref('')
const quickBusy = ref(false)
let noticeTimer = 0

const filters: { key: Filter; label: string }[] = [
  { key: 'today', label: '今天' },
  { key: 'week', label: '本周' },
  { key: 'all', label: '全部' },
  { key: 'completed', label: '已完成' },
]

const filteredTasks = computed(() => {
  const keyword = search.value.trim().toLowerCase()
  return tasks.value.filter(task => {
    if (keyword && !task.title.toLowerCase().includes(keyword)) return false
    const due = task.schedule.due_date
    if (filter.value === 'today') {
      return task.status === 'open' && due !== null && due <= today.value
    }
    if (filter.value === 'week') {
      return task.status === 'open' && due !== null
        && due >= today.value && due <= addDays(today.value, 6)
    }
    if (filter.value === 'all') return task.status === 'open'
    return task.status === 'completed'
  })
})

const groups = computed(() => {
  const items = [...filteredTasks.value].sort((left, right) => {
    const leftDue = left.schedule.due_date ?? '9999-12-31'
    const rightDue = right.schedule.due_date ?? '9999-12-31'
    if (leftDue !== rightDue) return leftDue < rightDue ? -1 : 1
    return right.priority - left.priority
  })
  if (filter.value !== 'all') {
    return [{ key: filter.value, title: '', items }]
  }
  const overdue: TaskSummary[] = []
  const todayItems: TaskSummary[] = []
  const thisWeek: TaskSummary[] = []
  const later: TaskSummary[] = []
  for (const task of items) {
    const due = task.schedule.due_date
    if (!due) later.push(task)
    else if (due < today.value) overdue.push(task)
    else if (due === today.value) todayItems.push(task)
    else if (due <= addDays(today.value, 6)) thisWeek.push(task)
    else later.push(task)
  }
  return [
    { key: 'overdue', title: '已逾期', items: overdue },
    { key: 'today', title: '今天', items: todayItems },
    { key: 'week', title: '本周', items: thisWeek },
    { key: 'later', title: '以后 / 无日期', items: later },
  ].filter(group => group.items.length > 0)
})

const openCount = computed(() => tasks.value.filter(task => task.status === 'open').length)

function showNotice(message: string) {
  notice.value = message
  window.clearTimeout(noticeTimer)
  noticeTimer = window.setTimeout(() => {
    notice.value = ''
  }, 3200)
}

async function loadTasks() {
  tasksLoading.value = true
  error.value = null
  try {
    const page = await listTasks({
      lifecycle: 'active',
      search: '',
      status: filter.value === 'completed' ? 'completed' : 'open',
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
    loading.value = false
  }
}

async function initialize() {
  try {
    let current = await getTimeSettings()
    if (!current.initialized) current = await saveTimeSettings(current, browserTimeZone)
    settings.value = current
    terms.value = await listTerms()
    await loadTasks()
  } catch (reason) {
    error.value = failure(reason)
    loading.value = false
  }
}

onMounted(async () => {
  document.title = '任务 · iTime'
  await refreshSession()
  if (!identity.value) {
    await router.replace('/login')
    return
  }
  await initialize()
})

watch(filter, () => {
  void loadTasks()
})

function taskDueLabel(task: TaskSummary) {
  const due = task.schedule.due_date
  if (!due) return '无日期'
  if (due === today.value) return '今天'
  if (due === addDays(today.value, 1)) return '明天'
  const date = parseLocalDate(due)
  return `${date.getMonth() + 1}月${date.getDate()}日`
}

function taskMeta(task: TaskSummary) {
  const parts: string[] = []
  if (task.estimated_minutes) parts.push(`${task.estimated_minutes} 分钟`)
  if (task.schedule.recurrence.frequency !== 'none') parts.push('重复')
  if (task.priority > 0) {
    parts.push(['', '低优先', '中优先', '高优先'][task.priority] ?? '')
  }
  return parts.join(' · ')
}

function isOverdue(task: TaskSummary) {
  return task.status === 'open'
    && task.schedule.due_date !== null
    && task.schedule.due_date < today.value
}

async function completeTask(task: TaskSummary) {
  if (taskActionBusy.value) return
  taskActionBusy.value = true
  error.value = null
  try {
    if (task.status === 'completed') {
      await setTaskCompletion(task, crypto.randomUUID(), false)
      detailOpen.value = false
      selectedTask.value = null
      showNotice('任务已重新打开')
      await loadTasks()
      return
    }
    if (task.schedule.recurrence.frequency !== 'none') {
      const page = await listTaskOccurrences(
        today.value,
        addDays(today.value, 14),
      )
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
      detailOpen.value = false
      selectedTask.value = null
      showNotice('本次重复任务已完成')
      await loadTasks()
      return
    }
    await setTaskCompletion(task, crypto.randomUUID(), true)
    detailOpen.value = false
    selectedTask.value = null
    showNotice('任务已完成')
    await loadTasks()
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    taskActionBusy.value = false
  }
}

function openTask(task: TaskSummary) {
  selectedTask.value = task
  detailOpen.value = true
}

function repeatValue(frequency: string): TaskRepeat {
  return ['daily', 'weekdays', 'weekly', 'monthly', 'yearly'].includes(frequency)
    ? frequency as TaskRepeat
    : 'none'
}

function openCreate(title = '') {
  editingTask.value = null
  taskInitial.value = {
    title,
    date: today.value,
    repeat: 'none',
    weekdays: [parseLocalDate(today.value).getDay() || 7],
    estimatedMinutes: null,
    priority: 2,
    until: defaultUntil.value,
  }
  dialogError.value = ''
  dialogOpen.value = true
}

function editTask(task: TaskSummary) {
  editingTask.value = task
  const date = task.schedule.due_date ?? today.value
  const weekday = parseLocalDate(date).getDay() || 7
  taskInitial.value = {
    title: task.title,
    date,
    repeat: repeatValue(task.schedule.recurrence.frequency),
    weekdays: task.schedule.recurrence.weekdays.length
      ? [...task.schedule.recurrence.weekdays]
      : [weekday],
    estimatedMinutes: task.estimated_minutes,
    priority: task.priority || 2,
    until: task.schedule.recurrence.until ?? defaultUntil.value,
  }
  detailOpen.value = false
  dialogError.value = ''
  dialogOpen.value = true
}

function buildRecurrence(
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
  } else {
    recurrence.until = values.until
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

async function submitTask(values: TaskFormValues) {
  dialogBusy.value = true
  dialogError.value = ''
  try {
    if (editingTask.value) {
      const detail = await getTask(editingTask.value.id)
      const updated = await updateTask(detail.id, crypto.randomUUID(), detail.version, {
        title: values.title,
        description: detail.description,
        project_id: detail.project?.id ?? null,
        estimated_minutes: values.estimatedMinutes,
        priority: values.priority,
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
        recurrence: buildRecurrence(values, detail.schedule.recurrence),
      })
      showNotice('任务已更新')
    } else {
      const task = await createTask(crypto.randomUUID(), {
        title: values.title,
        description: '',
        project_id: null,
        estimated_minutes: values.estimatedMinutes,
        priority: values.priority,
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
        recurrence: buildRecurrence(values),
      })
      showNotice('任务已创建')
    }
    dialogOpen.value = false
    await loadTasks()
  } catch (reason) {
    dialogError.value = failure(reason).message
  } finally {
    dialogBusy.value = false
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
    detailOpen.value = false
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
    detailOpen.value = false
    selectedTask.value = null
    await loadTasks()
    showNotice('任务已移入回收站')
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    taskActionBusy.value = false
  }
}

const { pendingLogout, logout } = useAppShell()
</script>

<template>
  <AppShell
    current="tasks"
    title="任务"
    :pending-logout="pendingLogout"
    @logout="logout"
  >
    <template #actions>
      <button type="button" class="m3-button m3-button--filled tw-add" aria-label="添加任务" @click="openCreate()">
        <Plus :size="18" aria-hidden="true" />
        <span class="tw-add-label">添加任务</span>
      </button>
    </template>

    <div class="tw-page">
      <div v-if="error" class="tw-banner tw-banner--error" role="alert">
        <AlertCircle :size="18" aria-hidden="true" />
        <span>{{ error.message }}</span>
        <button type="button" class="m3-icon-button" aria-label="关闭提示" @click="error = null">
          <X :size="18" />
        </button>
      </div>
      <div v-if="notice" class="tw-banner tw-banner--success" role="status">
        <CheckCircle2 :size="18" aria-hidden="true" />
        <span>{{ notice }}</span>
      </div>

      <div class="tw-main">
        <p class="tw-count">
          <strong>{{ openCount }}</strong> 个待办
          <span aria-hidden="true">·</span>
          用周期和优先级把时间管起来
        </p>

        <div class="tw-toolbar">
          <div class="m3-segmented tw-filters" role="tablist" aria-label="任务筛选">
            <button
              v-for="item in filters"
              :key="item.key"
              type="button"
              role="tab"
              :aria-selected="filter === item.key"
              :class="{ selected: filter === item.key }"
              @click="filter = item.key"
            >
              {{ item.label }}
            </button>
          </div>
          <label class="tw-search">
            <span class="material-symbols-rounded" aria-hidden="true">search</span>
            <input v-model="search" type="search" placeholder="搜索任务" aria-label="搜索任务">
          </label>
        </div>

        <form class="tw-quickadd" @submit.prevent="quickAdd">
          <span class="material-symbols-rounded tw-quickadd-icon" aria-hidden="true">add_task</span>
          <input
            v-model="quickTitle"
            type="text"
            maxlength="200"
            placeholder="添加一个任务"
            aria-label="快速添加任务"
            :disabled="quickBusy"
          >
          <button
            type="submit"
            class="m3-icon-button m3-icon-button--filled tw-quickadd-submit"
            aria-label="添加任务"
            :disabled="!quickTitle.trim() || quickBusy"
          >
            <span class="material-symbols-rounded" aria-hidden="true">arrow_upward</span>
          </button>
        </form>

        <div v-if="loading || tasksLoading" class="tw-skeleton" aria-hidden="true">
          <span v-for="index in 5" :key="index" />
        </div>

        <section v-else class="tw-list" aria-live="polite">
          <div v-for="group in groups" :key="group.key" class="tw-group">
            <h2 v-if="group.title" class="tw-group-title">
              {{ group.title }}
              <span>{{ group.items.length }}</span>
            </h2>
            <div
              v-for="task in group.items"
              :key="task.id"
              class="tw-task"
              :class="{ done: task.status === 'completed', overdue: isOverdue(task) }"
            >
              <button
                type="button"
                class="tw-check"
                :class="{ checked: task.status === 'completed' }"
                :aria-label="task.status === 'completed' ? `重新打开：${task.title}` : `完成任务：${task.title}`"
                @click="completeTask(task)"
              >
                <span class="tw-check-mark">
                  <Check v-if="task.status === 'completed'" :size="16" />
                </span>
              </button>
              <button type="button" class="tw-task-open" @click="openTask(task)">
                <span class="tw-task-title">{{ task.title }}</span>
                <span v-if="taskMeta(task)" class="tw-task-meta">{{ taskMeta(task) }}</span>
              </button>
              <span class="tw-due" :class="{ overdue: isOverdue(task) }">
                <AlertCircle v-if="isOverdue(task)" :size="14" aria-hidden="true" />
                <Repeat2 v-else-if="task.schedule.recurrence.frequency !== 'none'" :size="14" aria-hidden="true" />
                {{ taskDueLabel(task) }}
              </span>
            </div>
          </div>

          <div v-if="!filteredTasks.length" class="tw-empty">
            <span class="material-symbols-rounded" aria-hidden="true">task_alt</span>
            <strong>{{ filter === 'completed' ? '还没有完成的任务' : '这里没有任务' }}</strong>
            <p>{{ filter === 'completed' ? '完成一个任务后会出现在这里。' : '在上方输入，或让 AI 帮你安排。' }}</p>
            <button v-if="filter !== 'completed'" type="button" class="m3-button m3-button--tonal" @click="openCreate()">
              添加任务
            </button>
          </div>
        </section>
      </div>

      <ElDrawer
        v-model="detailOpen"
        direction="rtl"
        size="min(400px, 100vw)"
        :with-header="false"
        class="tw-detail-drawer"
      >
        <div v-if="selectedTask" class="tw-detail">
          <header class="tw-detail-header">
            <div>
              <span class="tw-detail-kind">
                {{ selectedTask.status === 'completed' ? '已完成' : '任务' }}
              </span>
              <h2>{{ selectedTask.title }}</h2>
            </div>
            <button type="button" class="m3-icon-button" aria-label="关闭详情" title="关闭" @click="detailOpen = false">
              <X :size="20" />
            </button>
          </header>
          <div class="tw-detail-body">
            <p v-if="selectedTask.description" class="tw-detail-desc">{{ selectedTask.description }}</p>
            <dl class="tw-detail-facts">
              <div>
                <dt>截止</dt>
                <dd>{{ taskDueLabel(selectedTask) }}</dd>
              </div>
              <div>
                <dt>重复</dt>
                <dd>{{ selectedTask.schedule.recurrence.frequency === 'none' ? '不重复' : '周期任务' }}</dd>
              </div>
              <div v-if="selectedTask.estimated_minutes">
                <dt>预计用时</dt>
                <dd>{{ selectedTask.estimated_minutes }} 分钟</dd>
              </div>
            </dl>
            <div class="tw-detail-actions">
              <button
                type="button"
                class="m3-button m3-button--filled"
                :disabled="taskActionBusy"
                @click="completeTask(selectedTask)"
              >
                {{ selectedTask.status === 'completed' ? '重新打开' : '完成' }}
              </button>
              <button type="button" class="m3-button m3-button--tonal" @click="editTask(selectedTask)">
                编辑
              </button>
              <button
                v-if="selectedTask.schedule.recurrence.frequency === 'none'"
                type="button"
                class="m3-button m3-button--outlined"
                :disabled="taskActionBusy"
                @click="postponeTask(selectedTask)"
              >
                推迟到明天
              </button>
              <button type="button" class="m3-button m3-button--text tw-detail-delete" :disabled="taskActionBusy" @click="removeTask(selectedTask)">
                <Trash2 :size="16" aria-hidden="true" />
                删除任务
              </button>
            </div>
          </div>
        </div>
      </ElDrawer>

      <QuickTaskDialog
        v-model="dialogOpen"
        :mode="editingTask ? 'edit' : 'create'"
        :default-until="defaultUntil"
        :initial="taskInitial"
        :busy="dialogBusy"
        :error="dialogError"
        @submit="submitTask"
      />
    </div>
  </AppShell>
</template>

<style scoped>
.tw-page {
  display: flex;
  flex-direction: column;
  min-height: 100%;
  background: var(--m3-surface);
}

.tw-main {
  width: 100%;
  max-width: 960px;
  margin: 0 auto;
  padding: 24px 24px 64px;
}

.tw-count {
  margin: 0 0 16px;
  color: var(--m3-on-surface-variant);
  font: 400 14px/20px var(--font);
  letter-spacing: 0.25px;
}
.tw-count strong {
  color: var(--m3-on-surface);
  font-weight: 500;
}

.tw-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px 16px;
  margin-bottom: 16px;
}

.tw-filters {
  background: var(--m3-surface-container-low);
  border-color: var(--m3-outline-variant);
}
.tw-filters > button {
  min-width: 68px;
  padding: 0 16px;
  border-right-color: var(--m3-outline-variant);
}
.tw-filters > button.selected {
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
}

.tw-search {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 280px;
  min-height: 56px;
  padding: 0 16px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-surface-container-high);
  color: var(--m3-on-surface-variant);
  cursor: text;
}
.tw-search .material-symbols-rounded {
  flex: 0 0 auto;
  font-size: 22px;
}
.tw-search input {
  min-width: 0;
  flex: 1 1 auto;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--m3-on-surface);
  font: 400 16px/24px var(--font);
  letter-spacing: 0.3px;
  outline: 0;
}
.tw-search input::placeholder {
  color: var(--m3-on-surface-variant);
}

.tw-quickadd {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 12px;
  min-height: 56px;
  margin-bottom: 24px;
  padding: 8px 8px 8px 16px;
  border-radius: var(--m3-shape-lg);
  background: var(--m3-surface-container-high);
}
.tw-quickadd-icon {
  flex: 0 0 auto;
  color: var(--m3-on-surface-variant);
  font-size: 22px;
}
.tw-quickadd input {
  min-width: 0;
  flex: 1 1 auto;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--m3-on-surface);
  font: 400 16px/24px var(--font);
  letter-spacing: 0.3px;
  outline: 0;
}
.tw-quickadd input::placeholder {
  color: var(--m3-on-surface-variant);
}
.tw-quickadd-submit {
  flex: 0 0 auto;
}
.tw-quickadd-submit:disabled {
  background: var(--m3-surface-container-highest);
  color: color-mix(in srgb, var(--m3-on-surface) 38%, transparent);
  cursor: not-allowed;
}

.tw-list {
  display: flex;
  flex-direction: column;
}

.tw-group {
  display: flex;
  flex-direction: column;
}
.tw-group + .tw-group {
  margin-top: 24px;
}

.tw-group-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  margin: 0 0 4px;
  padding: 0 4px;
  color: var(--m3-on-surface-variant);
  font: 500 12px/16px var(--font);
  letter-spacing: 0.5px;
}
.tw-group-title span {
  display: inline-grid;
  place-items: center;
  min-width: 20px;
  height: 20px;
  padding: 0 6px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-surface-container-high);
  color: var(--m3-on-surface-variant);
  font: 500 11px/16px var(--font);
}

.tw-task {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 72px;
  margin: 0 -12px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--m3-outline-variant);
  border-radius: var(--m3-shape-sm);
}
.tw-task:hover {
  background: color-mix(in srgb, var(--m3-on-surface) 4%, transparent);
}
.tw-task:last-child {
  border-bottom: 0;
}

.tw-check {
  display: grid;
  flex: 0 0 48px;
  place-items: center;
  width: 48px;
  height: 48px;
  padding: 0;
  border: 0;
  border-radius: var(--m3-shape-full);
  background: transparent;
  cursor: pointer;
}
.tw-check-mark {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border: 2px solid var(--m3-outline);
  border-radius: var(--m3-shape-full);
  background: transparent;
  color: var(--m3-on-primary);
  transition:
    background-color var(--m3-duration-short) var(--m3-easing-standard),
    border-color var(--m3-duration-short) var(--m3-easing-standard);
}
.tw-check:hover .tw-check-mark {
  border-color: var(--m3-primary);
  background: color-mix(in srgb, var(--m3-primary) 8%, transparent);
}
.tw-check.checked .tw-check-mark {
  border-color: var(--m3-primary);
  background: var(--m3-primary);
}

.tw-task-open {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  min-height: 48px;
  padding: 8px 0;
  border: 0;
  background: transparent;
  color: var(--m3-on-surface);
  text-align: left;
  cursor: pointer;
}
.tw-task-title {
  overflow: hidden;
  font: 500 16px/24px var(--font);
  letter-spacing: 0.15px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tw-task-meta {
  overflow: hidden;
  color: var(--m3-on-surface-variant);
  font: 400 12px/16px var(--font);
  letter-spacing: 0.4px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tw-due {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 4px;
  color: var(--m3-on-surface-variant);
  font: 500 12px/16px var(--font);
  white-space: nowrap;
}
.tw-due.overdue {
  color: var(--m3-error);
}
.tw-task.done .tw-task-title {
  color: var(--m3-on-surface-variant);
  text-decoration: line-through;
}

.tw-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 64px 24px;
  color: var(--m3-on-surface-variant);
  text-align: center;
}
.tw-empty .material-symbols-rounded {
  margin-bottom: 8px;
  color: var(--m3-primary);
  font-size: 40px;
}
.tw-empty strong {
  color: var(--m3-on-surface);
  font: 500 16px/24px var(--font);
}
.tw-empty p {
  max-width: 320px;
  margin: 0;
  font: 400 14px/20px var(--font);
}
.tw-empty .m3-button {
  margin-top: 16px;
}

.tw-skeleton {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-top: 8px;
}
.tw-skeleton span {
  display: block;
  height: 56px;
  border-radius: var(--m3-shape-sm);
  background: linear-gradient(
    90deg,
    var(--m3-surface-container-high),
    var(--m3-surface-container-low),
    var(--m3-surface-container-high)
  );
  background-size: 200% 100%;
  animation: tw-shimmer 1.4s ease-in-out infinite;
}
@keyframes tw-shimmer {
  from { background-position: 200% 0; }
  to { background-position: -200% 0; }
}

.tw-banner {
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: 912px;
  min-height: 48px;
  margin: 8px auto 0;
  padding: 8px 8px 8px 16px;
  border-radius: var(--m3-shape-sm);
  background: var(--m3-surface-container-high);
  color: var(--m3-on-surface);
  font: 400 14px/20px var(--font);
}
.tw-banner > span {
  flex: 1 1 auto;
  min-width: 0;
}
.tw-banner--error {
  background: var(--m3-error-container);
  color: var(--m3-on-error-container);
}
.tw-banner--success {
  background: var(--m3-secondary-container);
  color: var(--m3-on-secondary-container);
}

/* Detail side sheet ---------------------------------------------------- */
:global(.tw-detail-drawer .el-drawer__body) {
  padding: 0;
}
.tw-detail {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--m3-surface-container-low);
}
.tw-detail-header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 16px 16px 16px 24px;
  border-bottom: 1px solid var(--m3-outline-variant);
}
.tw-detail-header > div {
  flex: 1 1 auto;
  min-width: 0;
}
.tw-detail-kind {
  display: block;
  margin-bottom: 4px;
  color: var(--m3-on-surface-variant);
  font: 500 12px/16px var(--font);
  letter-spacing: 0.5px;
}
.tw-detail-header h2 {
  margin: 0;
  overflow-wrap: anywhere;
  color: var(--m3-on-surface);
  font: 500 22px/28px var(--font);
  letter-spacing: 0;
}
.tw-detail-body {
  flex: 1 1 auto;
  padding: 20px 24px 32px;
  overflow-y: auto;
}
.tw-detail-desc {
  margin: 0 0 16px;
  padding: 16px;
  border-radius: var(--m3-shape-md);
  background: var(--m3-surface-container);
  color: var(--m3-on-surface);
  font: 400 14px/20px var(--font);
  white-space: pre-wrap;
}
.tw-detail-facts {
  display: flex;
  flex-direction: column;
  gap: 0;
  margin: 0;
}
.tw-detail-facts > div {
  display: grid;
  grid-template-columns: 92px minmax(0, 1fr);
  gap: 12px;
  align-items: baseline;
  padding: 14px 0;
  border-bottom: 1px solid var(--m3-outline-variant);
}
.tw-detail-facts dt {
  color: var(--m3-on-surface-variant);
  font: 500 12px/16px var(--font);
  letter-spacing: 0.5px;
}
.tw-detail-facts dd {
  margin: 0;
  color: var(--m3-on-surface);
  font: 400 14px/20px var(--font);
}
.tw-detail-actions {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 24px;
}
.tw-detail-actions .m3-button {
  width: 100%;
}
.tw-detail-delete {
  margin-top: 8px;
  color: var(--m3-error);
}

@media (max-width: 839px) {
  .tw-main {
    padding: 16px 16px 40px;
  }
  .tw-toolbar {
    align-items: stretch;
    flex-direction: column;
    gap: 8px;
  }
  .tw-filters {
    width: 100%;
  }
  .tw-filters > button {
    flex: 1 1 0;
    min-width: 0;
    padding: 0 4px;
    font-size: 13px;
  }
  .tw-search {
    width: 100%;
  }
  .tw-quickadd {
    margin-bottom: 16px;
  }
  .tw-task {
    gap: 8px;
    min-height: 64px;
    margin: 0 -8px;
    padding: 6px 8px;
  }
  .tw-task-title {
    font-size: 15px;
  }
  .tw-due {
    max-width: 82px;
    text-align: right;
    white-space: normal;
  }
  .tw-add {
    width: 40px;
    min-height: 40px;
    padding: 0;
  }
  .tw-add-label {
    display: none;
  }
  .tw-banner {
    margin: 8px 16px 0;
  }
  .tw-detail-facts > div {
    grid-template-columns: 76px minmax(0, 1fr);
  }
}

@media (min-width: 840px) {
  .tw-add-label {
    display: inline;
  }
}
</style>
