<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { LogOut, Plus, X } from 'lucide-vue-next'
import FocusHistory from '../components/FocusHistory.vue'
import FocusSetup from '../components/FocusSetup.vue'
import FocusTaskProgress from '../components/FocusTaskProgress.vue'
import FocusTimer from '../components/FocusTimer.vue'
import ProductNavigation from '../components/ProductNavigation.vue'
import StatusMessage from '../components/StatusMessage.vue'
import { failure, localFailure, type Failure } from '../lib/api'
import {
  authError,
  clearSession,
  identity,
  notifySessionChange,
  refreshSession,
} from '../lib/auth'
import { getTimeSettings, saveTimeSettings, type TimeSettings } from '../lib/calendar'
import {
  completeFocusTask,
  createManualFocus,
  getCurrentFocus,
  getFocusSession,
  listFocusSessions,
  reviseFocus,
  startFocus,
  transitionFocus,
  updateFocusProgress,
  type FocusManualDraft,
  type FocusMode,
  type FocusSession,
  type FocusTransition,
} from '../lib/execution'
import { getTask, listTasks, type TaskSummary } from '../lib/tasks'
import '../focus.css'

const route = useRoute()
const router = useRouter()
const browserTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
const settings = ref<TimeSettings | null>(null)
const tasks = ref<TaskSummary[]>([])
const current = ref<FocusSession | null>(null)
const selected = ref<FocusSession | null>(null)
const history = ref<FocusSession[]>([])
const mode = ref<FocusMode>('stopwatch')
const targetMinutes = ref(25)
const selectedTaskId = ref('')
const title = ref('')
const scheduledBlockId = ref<string | null>(null)
const pending = ref(false)
const loading = ref(true)
const historyLoading = ref(false)
const pendingLogout = ref(false)
const pageError = ref<Failure | null>(null)
const pageSuccess = ref('')
const manualOpen = ref(false)
const revisionTarget = ref<FocusSession | null>(null)
const manualTitle = ref('')
const manualStart = ref('')
const manualEnd = ref('')
const manualMinutes = ref(30)
const manualReason = ref('')
const tick = ref(0)
let elapsedAnchor = 0
let monotonicAnchor = performance.now()
let timer: number | null = null
let mutationOperation: { fingerprint: string; id: string } | null = null

const shownFocus = computed(() => current.value ?? selected.value)
const elapsedSeconds = computed(() => {
  tick.value
  if (!shownFocus.value) return 0
  if (shownFocus.value.status !== 'running') {
    return shownFocus.value.effective_duration_seconds
      ?? shownFocus.value.elapsed_seconds
  }
  return elapsedAnchor + Math.max(
    0,
    Math.floor((performance.now() - monotonicAnchor) / 1000),
  )
})

watch(authError, value => {
  if (value?.code === 'unauthenticated') void router.replace('/login')
})

onMounted(async () => {
  document.title = '专注 · iTime'
  timer = window.setInterval(() => { tick.value += 1 }, 1000)
  await refreshSession()
  if (!identity.value) {
    await router.replace('/login')
    return
  }
  await initialize()
})

onBeforeUnmount(() => {
  if (timer !== null) window.clearInterval(timer)
})

async function initialize() {
  loading.value = true
  pageError.value = null
  try {
    let currentSettings = await getTimeSettings()
    if (!currentSettings.initialized) {
      currentSettings = await saveTimeSettings(currentSettings, browserTimeZone)
    }
    settings.value = currentSettings
    await Promise.all([loadTasks(), loadCurrent(), loadHistory()])
    await applyRouteSelection()
  } catch (reason) {
    pageError.value = failure(reason)
  } finally {
    loading.value = false
  }
}

async function loadTasks() {
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
}

async function applyRouteSelection() {
  const taskId = typeof route.query.task === 'string' ? route.query.task : ''
  const blockId = typeof route.query.block === 'string' ? route.query.block : ''
  scheduledBlockId.value = blockId || null
  if (!taskId) return
  selectedTaskId.value = taskId
  if (!tasks.value.some(task => task.id === taskId)) {
    const task = await getTask(taskId)
    tasks.value = [task, ...tasks.value]
  }
}

function setFocus(value: FocusSession | null) {
  current.value = value && ['running', 'paused'].includes(value.status) ? value : null
  if (value) {
    elapsedAnchor = value.elapsed_seconds
    monotonicAnchor = performance.now()
    if (!current.value) selected.value = value
  }
}

async function loadCurrent() {
  const result = await getCurrentFocus()
  setFocus(result.focus)
}

function historyRange() {
  const start = new Date()
  start.setUTCDate(start.getUTCDate() - 30)
  const end = new Date()
  end.setUTCDate(end.getUTCDate() + 1)
  return { start: start.toISOString(), end: end.toISOString() }
}

async function loadHistory() {
  historyLoading.value = true
  try {
    const range = historyRange()
    history.value = (await listFocusSessions(range.start, range.end)).items
  } finally {
    historyLoading.value = false
  }
}

function stableOperation(fingerprint: string) {
  if (mutationOperation?.fingerprint !== fingerprint) {
    mutationOperation = { fingerprint, id: crypto.randomUUID() }
  }
  return mutationOperation.id
}

function clearMessages() {
  pageError.value = null
  pageSuccess.value = ''
}

async function beginFocus() {
  if (pending.value || current.value) return
  clearMessages()
  const taskId = selectedTaskId.value || null
  if (!taskId && !scheduledBlockId.value && !title.value.trim()) {
    pageError.value = localFailure('请输入专注内容。')
    return
  }
  if (mode.value === 'countdown' && (
    !Number.isInteger(targetMinutes.value)
    || targetMinutes.value < 1
    || targetMinutes.value > 1440
  )) {
    pageError.value = localFailure('目标分钟必须是 1—1440 的整数。')
    return
  }
  const fingerprint = [
    'start',
    mode.value,
    targetMinutes.value,
    title.value.trim(),
    taskId,
    scheduledBlockId.value,
  ].join(':')
  pending.value = true
  try {
    const result = await startFocus(stableOperation(fingerprint), {
      mode: mode.value,
      target_seconds: mode.value === 'countdown' ? targetMinutes.value * 60 : null,
      title: taskId || scheduledBlockId.value ? null : title.value.trim(),
      task_id: taskId,
      occurrence_key: null,
      scheduled_block_id: scheduledBlockId.value,
    })
    mutationOperation = null
    selected.value = null
    setFocus(result.focus)
    await router.replace('/focus')
    await loadHistory()
    pageSuccess.value = '专注已开始。'
  } catch (reason) {
    pageError.value = failure(reason)
    if (pageError.value.saved === 'not_saved') mutationOperation = null
  } finally {
    pending.value = false
  }
}

async function runTransition(action: FocusTransition) {
  if (!current.value || pending.value) return
  if (action === 'abandon' && !window.confirm('放弃本次专注？已记录的时间会保留。')) return
  clearMessages()
  const focus = current.value
  const fingerprint = `${action}:${focus.id}:${focus.version}`
  pending.value = true
  try {
    const result = await transitionFocus(
      focus,
      stableOperation(fingerprint),
      action,
    )
    mutationOperation = null
    setFocus(result.focus)
    if (['finish', 'abandon'].includes(action)) {
      await loadHistory()
      pageSuccess.value = action === 'finish'
        ? '本次专注已结束。'
        : '本次专注已放弃，已记录的时间仍会保留。'
    } else {
      pageSuccess.value = action === 'pause' ? '专注已暂停。' : '专注已继续。'
    }
  } catch (reason) {
    pageError.value = failure(reason)
    if (pageError.value.saved === 'not_saved') mutationOperation = null
  } finally {
    pending.value = false
  }
}

async function saveProgress(completedStepIds: string[]) {
  if (!current.value || pending.value) return
  clearMessages()
  const focus = current.value
  const fingerprint = `progress:${focus.id}:${focus.version}:${completedStepIds.join(',')}`
  pending.value = true
  try {
    const result = await updateFocusProgress(
      focus,
      stableOperation(fingerprint),
      completedStepIds,
    )
    mutationOperation = null
    setFocus(result.focus)
    pageSuccess.value = '步骤进度已保存，任务仍需你明确完成。'
  } catch (reason) {
    pageError.value = failure(reason)
    if (pageError.value.saved === 'not_saved') mutationOperation = null
  } finally {
    pending.value = false
  }
}

async function completeTask() {
  if (!shownFocus.value || pending.value) return
  clearMessages()
  const focus = shownFocus.value
  const fingerprint = `complete-task:${focus.id}:${focus.version}`
  pending.value = true
  try {
    const result = await completeFocusTask(
      focus,
      stableOperation(fingerprint),
    )
    mutationOperation = null
    selected.value = result.focus
    await loadHistory()
    pageSuccess.value = '关联任务已完成。'
  } catch (reason) {
    pageError.value = failure(reason)
    if (pageError.value.saved === 'not_saved') mutationOperation = null
  } finally {
    pending.value = false
  }
}

async function selectHistory(item: FocusSession) {
  clearMessages()
  try {
    const detail = await getFocusSession(item.id)
    selected.value = detail.focus
    setAnchor(detail.focus)
  } catch (reason) {
    pageError.value = failure(reason)
  }
}

function setAnchor(focus: FocusSession) {
  elapsedAnchor = focus.elapsed_seconds
  monotonicAnchor = performance.now()
}

function localInput(value: string, timeZone: string) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(new Date(value))
  const part = (type: Intl.DateTimeFormatPartTypes) => (
    parts.find(item => item.type === type)?.value ?? ''
  )
  return `${part('year')}-${part('month')}-${part('day')}T${part('hour')}:${part('minute')}`
}

function resetManualForm(focus: FocusSession | null = null) {
  const zone = focus?.time_zone ?? settings.value?.time_zone ?? browserTimeZone
  const start = new Date(
    focus?.effective_started_at
      ?? new Date(Date.now() - 30 * 60_000).toISOString(),
  )
  let end = new Date(focus?.effective_ended_at ?? new Date().toISOString())
  if (end.getTime() - start.getTime() < 60_000) {
    end = new Date(start.getTime() + 60_000)
  }
  manualTitle.value = focus?.title ?? ''
  manualStart.value = localInput(start.toISOString(), zone)
  manualEnd.value = localInput(end.toISOString(), zone)
  manualMinutes.value = Math.round(
    (focus?.effective_duration_seconds ?? 1800) / 60,
  )
  manualReason.value = ''
}

function openManual() {
  revisionTarget.value = null
  resetManualForm()
  manualOpen.value = true
  void nextTick(() => document.getElementById('manual-focus-title')?.focus())
}

async function openRevision(item: FocusSession) {
  try {
    const detail = await getFocusSession(item.id)
    revisionTarget.value = detail.focus
    resetManualForm(detail.focus)
    manualOpen.value = true
    void nextTick(() => document.getElementById('manual-focus-title')?.focus())
  } catch (reason) {
    pageError.value = failure(reason)
  }
}

function manualDraft(): FocusManualDraft {
  return {
    title: manualTitle.value.trim(),
    task_id: revisionTarget.value?.task_id ?? null,
    occurrence_key: revisionTarget.value?.occurrence_key ?? null,
    local_started_at: manualStart.value,
    local_ended_at: manualEnd.value,
    time_zone: revisionTarget.value?.time_zone
      ?? settings.value?.time_zone
      ?? browserTimeZone,
    start_utc_offset_minutes: null,
    end_utc_offset_minutes: null,
    effective_duration_seconds: Math.round(manualMinutes.value * 60),
  }
}

async function saveManual() {
  if (pending.value) return
  clearMessages()
  if (!manualTitle.value.trim() || !manualStart.value || !manualEnd.value) {
    pageError.value = localFailure('请填写标题、开始时间和结束时间。')
    return
  }
  if (!Number.isFinite(manualMinutes.value) || manualMinutes.value < 0) {
    pageError.value = localFailure('有效时长不能小于 0。')
    return
  }
  const fingerprint = revisionTarget.value
    ? `revise:${revisionTarget.value.id}:${revisionTarget.value.version}:${manualTitle.value}:${manualStart.value}:${manualEnd.value}:${manualMinutes.value}:${manualReason.value}`
    : `manual:${manualTitle.value}:${manualStart.value}:${manualEnd.value}:${manualMinutes.value}`
  pending.value = true
  try {
    const operationId = stableOperation(fingerprint)
    const result = revisionTarget.value
      ? await reviseFocus(
          revisionTarget.value,
          operationId,
          manualDraft(),
          manualReason.value.trim(),
        )
      : await createManualFocus(operationId, manualDraft())
    mutationOperation = null
    manualOpen.value = false
    selected.value = result.focus
    setAnchor(result.focus)
    const success = revisionTarget.value
      ? '专注记录已修订。'
      : '专注记录已补记。'
    revisionTarget.value = null
    await loadHistory()
    pageSuccess.value = success
  } catch (reason) {
    pageError.value = failure(reason)
    if (pageError.value.saved === 'not_saved') mutationOperation = null
  } finally {
    pending.value = false
  }
}

async function logout() {
  if (pendingLogout.value) return
  pendingLogout.value = true
  try {
    await clearSession()
    notifySessionChange()
    await router.replace('/login')
  } finally {
    pendingLogout.value = false
  }
}
</script>

<template>
  <div class="focus-workspace">
    <aside class="focus-sidebar">
      <div class="focus-sidebar-top">
        <RouterLink to="/ai" class="wordmark">iTime</RouterLink>
        <ProductNavigation current="focus" />
      </div>
      <FocusHistory
        :items="history"
        :selected-id="selected?.id ?? null"
        :loading="historyLoading"
        @select="selectHistory"
        @revise="openRevision"
      />
      <div class="focus-account">
        <RouterLink to="/account" class="nav-link">{{ identity?.user.email }}</RouterLink>
        <button
          type="button"
          class="icon-button"
          title="退出登录"
          aria-label="退出登录"
          :disabled="pendingLogout"
          @click="logout"
        >
          <LogOut :size="18" aria-hidden="true" />
        </button>
      </div>
    </aside>

    <main id="main-content" class="focus-main">
      <header class="focus-toolbar">
        <div>
          <span class="muted">执行</span>
          <h1 tabindex="-1">专注</h1>
        </div>
        <button type="button" class="button secondary" @click="openManual">
          <Plus :size="18" aria-hidden="true" />
          补记
        </button>
      </header>

      <div class="focus-status-region">
        <StatusMessage :error="pageError" :success="pageSuccess" />
      </div>

      <div v-if="loading" class="focus-loading" role="status">正在读取专注状态...</div>
      <div v-else class="focus-content">
        <FocusSetup
          v-if="!current"
          v-model:selected-task-id="selectedTaskId"
          v-model:title="title"
          v-model:mode="mode"
          v-model:target-minutes="targetMinutes"
          :tasks="tasks"
          :pending="pending"
          :scheduled-block-id="scheduledBlockId"
          @start="beginFocus"
        />
        <FocusTimer
          v-if="shownFocus"
          :focus="shownFocus"
          :elapsed-seconds="elapsedSeconds"
          :pending="pending"
          @transition="runTransition"
        />
        <div
          v-if="shownFocus && ['finished', 'abandoned'].includes(shownFocus.status)"
          class="focus-record-actions"
        >
          <button type="button" class="button secondary" @click="openRevision(shownFocus)">
            修订记录
          </button>
        </div>
        <FocusTaskProgress
          v-if="shownFocus"
          :focus="shownFocus"
          :pending="pending"
          @progress="saveProgress"
          @complete="completeTask"
        />
        <p v-if="!shownFocus && !current" class="focus-empty focus-main-empty">
          选择任务或输入内容后开始。
        </p>
      </div>
    </main>

    <dialog :open="manualOpen" class="focus-dialog" aria-labelledby="manual-focus-heading">
      <form method="dialog" @submit.prevent="saveManual">
        <header>
          <div>
            <span class="muted">{{ revisionTarget ? '修订事实' : '历史补记' }}</span>
            <h2 id="manual-focus-heading">{{ revisionTarget ? '修订专注记录' : '补记专注' }}</h2>
          </div>
          <button
            type="button"
            class="icon-button"
            title="关闭"
            aria-label="关闭"
            @click="manualOpen = false"
          >
            <X :size="19" aria-hidden="true" />
          </button>
        </header>
        <div class="field">
          <label for="manual-focus-title">专注内容</label>
          <input id="manual-focus-title" v-model="manualTitle" maxlength="200" />
        </div>
        <div class="focus-dialog-grid">
          <div class="field">
            <label for="manual-focus-start">开始时间</label>
            <input id="manual-focus-start" v-model="manualStart" type="datetime-local" />
          </div>
          <div class="field">
            <label for="manual-focus-end">结束时间</label>
            <input id="manual-focus-end" v-model="manualEnd" type="datetime-local" />
          </div>
        </div>
        <div class="field">
          <label for="manual-focus-duration">有效时长（分钟）</label>
          <input id="manual-focus-duration" v-model.number="manualMinutes" type="number" min="0" max="1440" />
        </div>
        <div v-if="revisionTarget" class="field">
          <label for="manual-focus-reason">修订原因</label>
          <textarea id="manual-focus-reason" v-model="manualReason" maxlength="500" />
        </div>
        <footer>
          <button type="button" class="button secondary" @click="manualOpen = false">取消</button>
          <button type="submit" class="button" :disabled="pending">
            {{ revisionTarget ? '保存修订' : '保存补记' }}
          </button>
        </footer>
      </form>
    </dialog>
  </div>
</template>
