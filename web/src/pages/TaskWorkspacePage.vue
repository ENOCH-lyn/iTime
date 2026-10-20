<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronRight,
  Plus,
  Repeat2,
  Sparkles,
  Trash2,
  X,
} from 'lucide-vue-next'
import QuickTaskDialog, {
  type TaskFormValues,
  type TaskRepeat,
} from '../components/QuickTaskDialog.vue'
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
import { emptyRecurrence, updateTaskSchedule } from '../lib/task-schedules'
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
    await setTaskCompletion(task, crypto.randomUUID(), task.status !== 'completed')
    detailOpen.value = false
    selectedTask.value = null
    showNotice(task.status === 'completed' ? '任务已重新打开' : '任务已完成')
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
        recurrence: buildRecurrence(values, detail.schedule.recurrence),
      })
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

const avatarLetter = computed(() => (
  identity.value?.user.email?.slice(0, 1).toUpperCase() ?? 'i'
))
</script>

<template>
  <div class="tw-page">
    <header class="tw-header">
      <RouterLink to="/timetable" class="tw-brand">iTime<span>.</span></RouterLink>
      <nav class="tw-nav" aria-label="主导航">
        <RouterLink to="/timetable">课表</RouterLink>
        <RouterLink to="/tasks" class="active">任务</RouterLink>
        <RouterLink to="/ai">AI</RouterLink>
        <RouterLink to="/reviews">复盘</RouterLink>
      </nav>
      <div class="tw-header-actions">
        <button type="button" class="tw-add" @click="openCreate()">
          <Plus :size="16" /> 添加任务
        </button>
        <RouterLink to="/ai" class="tw-ai-link"><Sparkles :size="16" /> AI</RouterLink>
        <RouterLink to="/me" class="tw-avatar" :aria-label="`账号 ${identity?.user.email ?? ''}`">
          {{ avatarLetter }}
        </RouterLink>
      </div>
    </header>

    <div v-if="error" class="tw-banner error" role="alert">
      {{ error.message }}
      <button type="button" @click="error = null"><X :size="15" /></button>
    </div>
    <div v-if="notice" class="tw-banner success" role="status">{{ notice }}</div>

    <main class="tw-main">
      <section class="tw-title-row">
        <div>
          <h1>任务</h1>
          <p>{{ openCount }} 个待办 · 用周期和优先级把时间管起来</p>
        </div>
        <RouterLink to="/tasks/full" class="tw-advanced">
          高级管理（步骤 / 标签 / 回收站）<ChevronRight :size="15" />
        </RouterLink>
      </section>

      <section class="tw-panel">
        <div class="tw-toolbar">
          <div class="tw-filters" role="tablist" aria-label="任务筛选">
            <button
              v-for="item in filters"
              :key="item.key"
              type="button"
              role="tab"
              :aria-selected="filter === item.key"
              :class="{ active: filter === item.key }"
              @click="filter = item.key"
            >
              {{ item.label }}
            </button>
          </div>
          <input v-model="search" type="search" placeholder="搜索任务" aria-label="搜索任务">
        </div>

        <form class="tw-quickadd" @submit.prevent="quickAdd">
          <Plus :size="17" aria-hidden="true" />
          <input
            v-model="quickTitle"
            type="text"
            maxlength="200"
            placeholder="今天要做什么？回车添加"
            :disabled="quickBusy"
          >
          <button type="submit" :disabled="!quickTitle.trim() || quickBusy">
            添加
          </button>
        </form>

        <div v-if="loading || tasksLoading" class="tw-skeleton" aria-hidden="true">
          <span v-for="index in 5" :key="index" />
        </div>

        <template v-else>
          <div v-for="group in groups" :key="group.key" class="tw-group">
            <h2 v-if="group.title">{{ group.title }}</h2>
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
                <Check v-if="task.status === 'completed'" :size="12" />
              </button>
              <button type="button" class="tw-task-copy" @click="openTask(task)">
                <strong>{{ task.title }}</strong>
                <small v-if="taskMeta(task)">{{ taskMeta(task) }}</small>
              </button>
              <span class="tw-due" :class="{ overdue: isOverdue(task) }">
                <AlertCircle v-if="isOverdue(task)" :size="13" />
                <Repeat2 v-else-if="task.schedule.recurrence.frequency !== 'none'" :size="13" />
                {{ taskDueLabel(task) }}
              </span>
            </div>
          </div>

          <div v-if="!filteredTasks.length" class="tw-empty">
            <CheckCircle2 :size="26" aria-hidden="true" />
            <strong>{{ filter === 'completed' ? '还没有完成的任务' : '这里没有任务' }}</strong>
            <p>{{ filter === 'completed' ? '完成一个任务后会出现在这里。' : '在上方输入，或让 AI 帮你安排。' }}</p>
          </div>
        </template>
      </section>
    </main>

    <ElDrawer
      v-model="detailOpen"
      direction="rtl"
      size="min(400px, 94vw)"
      :with-header="false"
    >
      <div v-if="selectedTask" class="tw-detail">
        <button type="button" class="tw-detail-close" aria-label="关闭" @click="detailOpen = false">
          <X :size="18" />
        </button>
        <span class="tw-detail-kind">
          {{ selectedTask.status === 'completed' ? '已完成' : '任务' }}
        </span>
        <h2>{{ selectedTask.title }}</h2>
        <p v-if="selectedTask.description" class="tw-detail-desc">{{ selectedTask.description }}</p>
        <dl>
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
            class="tw-button primary"
            :disabled="taskActionBusy"
            @click="completeTask(selectedTask)"
          >
            {{ selectedTask.status === 'completed' ? '重新打开' : '完成' }}
          </button>
          <button type="button" class="tw-button secondary" @click="editTask(selectedTask)">
            编辑
          </button>
          <button
            v-if="selectedTask.schedule.recurrence.frequency === 'none'"
            type="button"
            class="tw-button secondary"
            :disabled="taskActionBusy"
            @click="postponeTask(selectedTask)"
          >
            推迟到明天
          </button>
          <RouterLink to="/tasks/full" class="tw-button ghost">高级管理</RouterLink>
          <button type="button" class="tw-button danger" :disabled="taskActionBusy" @click="removeTask(selectedTask)">
            <Trash2 :size="15" /> 删除
          </button>
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
</template>

<style scoped>
.tw-page {
  min-height: 100dvh;
  background:
    radial-gradient(1100px 420px at 85% -10%, color-mix(in srgb, var(--accent) 7%, transparent), transparent 70%),
    var(--canvas);
  color: var(--text);
}
.tw-header {
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
.tw-brand {
  font-size: 21px;
  font-weight: 800;
  letter-spacing: -.04em;
  color: var(--text);
  text-decoration: none;
}
.tw-brand span { color: var(--accent); }
.tw-nav {
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  border-radius: 12px;
  background: color-mix(in srgb, var(--surface) 72%, transparent);
  border: 1px solid color-mix(in srgb, var(--border) 70%, transparent);
}
.tw-nav a {
  padding: 7px 13px;
  border-radius: 9px;
  color: var(--muted);
  font-size: 13px;
  font-weight: 550;
  text-decoration: none;
}
.tw-nav a:hover { color: var(--text); }
.tw-nav a.active {
  background: var(--surface);
  color: var(--text);
  box-shadow: 0 1px 3px rgb(31 42 36 / 8%);
}
.tw-header-actions { display: flex; align-items: center; gap: 8px; margin-left: auto; }
.tw-add {
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
.tw-add:hover { background: var(--accent-hover); }
.tw-ai-link {
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
.tw-avatar {
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
.tw-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 12px clamp(16px, 3vw, 32px) 0;
  padding: 10px 14px;
  border-radius: 12px;
  font-size: 13px;
}
.tw-banner.error { background: var(--error-bg); color: var(--error); border: 1px solid var(--error-border); }
.tw-banner.success { background: var(--selected); color: var(--accent); border: 1px solid var(--border); }
.tw-banner button { background: transparent; color: inherit; display: grid; place-items: center; }
.tw-main {
  max-width: 980px;
  margin: 0 auto;
  padding: 24px clamp(16px, 4vw, 32px) 64px;
}
.tw-title-row {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}
.tw-title-row h1 {
  font-size: clamp(26px, 3vw, 34px);
  font-weight: 750;
  letter-spacing: -.035em;
  line-height: 1.1;
}
.tw-title-row p { margin-top: 6px; font-size: 13px; color: var(--muted); }
.tw-advanced {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--muted);
  text-decoration: none;
  white-space: nowrap;
}
.tw-advanced:hover { color: var(--accent); }
.tw-panel {
  padding: 18px;
  border: 1px solid var(--border);
  border-radius: 22px;
  background: var(--surface);
  box-shadow: 0 1px 2px rgb(31 42 36 / 4%), 0 22px 50px -44px rgb(31 42 36 / 35%);
}
.tw-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 12px;
}
.tw-filters {
  display: flex;
  gap: 2px;
  padding: 3px;
  border-radius: 11px;
  background: color-mix(in srgb, var(--canvas) 65%, var(--surface));
}
.tw-filters button {
  padding: 7px 13px;
  border-radius: 8px;
  background: transparent;
  color: var(--muted);
  font-size: 12.5px;
  font-weight: 550;
}
.tw-filters button.active {
  background: var(--surface);
  color: var(--text);
  box-shadow: 0 1px 3px rgb(31 42 36 / 10%);
}
.tw-toolbar input[type="search"] {
  width: min(220px, 40vw);
  min-height: 38px;
  padding: 8px 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
  color: var(--text);
  font-size: 13px;
}
.tw-quickadd {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 8px;
  padding: 8px 8px 8px 12px;
  margin-bottom: 14px;
  border: 1px solid var(--border);
  border-radius: 13px;
  background: color-mix(in srgb, var(--canvas) 55%, var(--surface));
  color: var(--muted);
}
.tw-quickadd:focus-within {
  border-color: color-mix(in srgb, var(--accent) 55%, var(--border));
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 10%, transparent);
}
.tw-quickadd input {
  flex: 1;
  min-width: 0;
  padding: 7px 0;
  border: 0;
  background: transparent;
  color: var(--text);
  font-size: 14px;
  outline: none;
}
.tw-quickadd button {
  min-height: 34px;
  padding: 0 14px;
  border-radius: 9px;
  background: var(--accent);
  color: var(--on-accent);
  font-size: 12.5px;
  font-weight: 600;
}
.tw-quickadd button:disabled { opacity: .35; }
.tw-group { margin-top: 16px; }
.tw-group h2 {
  margin: 0 0 4px 4px;
  font-size: 11.5px;
  font-weight: 650;
  letter-spacing: .04em;
  color: var(--muted);
}
.tw-task {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 8px;
  border-bottom: 1px solid color-mix(in srgb, var(--border) 55%, transparent);
}
.tw-task:last-child { border-bottom: 0; }
.tw-task.done .tw-task-copy strong { text-decoration: line-through; color: var(--muted); }
.tw-check {
  display: grid;
  place-items: center;
  width: 19px;
  height: 19px;
  flex-shrink: 0;
  padding: 0;
  border: 1.5px solid color-mix(in srgb, var(--muted) 55%, transparent);
  border-radius: 50%;
  background: transparent;
  color: transparent;
}
.tw-check:hover { border-color: var(--accent); color: var(--accent); }
.tw-check.checked {
  border-color: var(--accent);
  background: var(--accent);
  color: var(--on-accent);
}
.tw-task-copy {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
}
.tw-task-copy strong {
  font-size: 14px;
  font-weight: 550;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.tw-task-copy small { font-size: 11.5px; color: var(--muted); }
.tw-due {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  font-size: 11.5px;
  color: var(--muted);
  white-space: nowrap;
}
.tw-due.overdue { color: var(--error); }
.tw-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 64px 20px;
  color: var(--muted);
  text-align: center;
}
.tw-empty svg { color: var(--accent); }
.tw-empty strong { color: var(--text); font-size: 15px; }
.tw-empty p { font-size: 12.5px; }
.tw-skeleton { display: grid; gap: 10px; padding: 12px 4px; }
.tw-skeleton span {
  height: 16px;
  border-radius: 6px;
  background: linear-gradient(90deg, var(--selected), transparent);
  animation: tw-shimmer 1.3s ease-in-out infinite;
}
@keyframes tw-shimmer {
  0%, 100% { opacity: .45; }
  50% { opacity: .9; }
}
.tw-detail { position: relative; padding: 8px 4px; }
.tw-detail-close {
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
.tw-detail-kind {
  display: inline-block;
  margin-bottom: 10px;
  padding: 4px 9px;
  border-radius: 999px;
  background: var(--selected);
  color: var(--accent);
  font-size: 11px;
  font-weight: 600;
}
.tw-detail h2 {
  padding-right: 36px;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -.02em;
  line-height: 1.3;
}
.tw-detail-desc { margin-top: 12px; font-size: 13px; line-height: 1.65; color: var(--muted); white-space: pre-wrap; }
.tw-detail dl { display: grid; gap: 14px; margin: 20px 0 26px; }
.tw-detail dt { margin-bottom: 3px; font-size: 11.5px; color: var(--muted); }
.tw-detail dd { margin: 0; font-size: 14px; }
.tw-detail-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.tw-button {
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
.tw-button.primary { background: var(--accent); color: var(--on-accent); }
.tw-button.primary:hover { background: var(--accent-hover); }
.tw-button.secondary { border: 1px solid var(--border); background: var(--surface); color: var(--text); }
.tw-button.secondary:hover { background: var(--selected); }
.tw-button.ghost { background: transparent; color: var(--muted); }
.tw-button.ghost:hover { background: var(--selected); color: var(--text); }
.tw-button.danger { border: 1px solid var(--error-border); background: var(--error-bg); color: var(--error); }
.tw-button:disabled { opacity: .5; }
@media (max-width: 760px) {
  .tw-header { flex-wrap: wrap; gap: 10px; }
  .tw-nav { order: 3; width: 100%; justify-content: space-between; }
  .tw-header-actions { margin-left: auto; }
  .tw-main { padding-top: 16px; }
  .tw-title-row { align-items: flex-start; flex-direction: column; gap: 8px; }
  .tw-toolbar { flex-direction: column; align-items: stretch; }
  .tw-toolbar input[type="search"] { width: 100%; }
  .tw-filters { justify-content: space-between; }
  .tw-filters button { flex: 1; }
  .tw-panel { padding: 12px; border-radius: 18px; }
  .tw-add { padding: 0 10px; }
  .tw-ai-link { display: none; }
}
</style>
