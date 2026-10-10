<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import {
  AlertTriangle,
  CalendarSearch,
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CirclePause,
  LoaderCircle,
  Pencil,
  RefreshCw,
  Sparkles,
  XCircle,
} from 'lucide-vue-next'
import PlanNavigation from '../components/PlanNavigation.vue'
import PlanPreview from '../components/PlanPreview.vue'
import StatusMessage from '../components/StatusMessage.vue'
import { failure, type Failure } from '../lib/api'
import {
  authError,
  identity,
  refreshSession,
} from '../lib/auth'
import AppShell from '../shell/AppShell.vue'
import { useAppShell } from '../shell/useAppShell'
import { cancelAIJob, getAIJob, type AIJob } from '../lib/ai-jobs'
import {
  applyPlanProposal,
  contextBlock,
  contextUnscheduledItem,
  getPlanHistory,
  getPlanProposal,
  getPlanningContext,
  keepPlanAfterSourceChanges,
  listPlanProposals,
  listPlanningJobs,
  rejectPlanProposal,
  saveManualPlanVersion,
  type PlanBlock,
  type PlanProposal,
  type PlanProposalSummary,
  type PlanScope,
  type PlanSourceConflict,
  type PlanSourceOccurrence,
  type PlanUnscheduledItem,
  type PlanVersion,
  type PlanningContext,
  type PlanningFields,
  type ProposalChange,
} from '../lib/plans'
import { useSyncRefresh } from '../lib/use-sync-refresh'
import '../planning.css'

const route = useRoute()
const router = useRouter()
const browserTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
const form = reactive<PlanningFields>({
  scope: route.query.scope === 'day' ? 'day' : 'week',
  target_date: validDate(String(route.query.date ?? ''))
    ? String(route.query.date)
    : dateInZone(new Date(), browserTimeZone),
  density: 'balanced',
  buffer_minutes: 15,
  availability_start: '08:00',
  availability_end: '22:00',
  task_ids: [],
  instruction: '',
})
const context = ref<PlanningContext | null>(null)
const proposals = ref<PlanProposalSummary[]>([])
const selectedProposal = ref<PlanProposal | null>(null)
const activeJob = ref<AIJob | null>(null)
const history = ref<PlanVersion[]>([])
const viewedVersion = ref<PlanVersion | null>(null)
const draftBlocks = ref<PlanBlock[]>([])
const draftUnscheduled = ref<PlanUnscheduledItem[]>([])
const editing = ref(false)
const dirty = ref(false)
const loaded = ref(false)
const loadingContext = ref(false)
const loadingProposals = ref(false)
const loadingProposal = ref(false)
const cancelling = ref(false)
const applying = ref(false)
const rejecting = ref(false)
const saving = ref(false)
const keepingSourceConflicts = ref(false)
const { pendingLogout, logoutError, logout } = useAppShell()
const pageError = ref<Failure | null>(null)
const pageSuccess = ref('')
let contextRevision = 0
let pollTimer = 0
let proposalOperation: { id: string; fingerprint: string } | null = null
let saveOperation: { id: string; fingerprint: string } | null = null
let keepConflictsOperation: { id: string; fingerprint: string } | null = null
let cancelOperation = ''
let disposed = false
const activeJobStorageKey = 'itime-active-planning-job'

const currentPlan = computed(() => context.value?.current_plan ?? null)
const sourceConflicts = computed(() => (
  currentPlan.value?.source_conflicts.items ?? []
))
const visibleScope = computed<PlanScope>(() => (
  selectedProposal.value?.input_snapshot.scope
  ?? viewedVersion.value?.scope
  ?? (editing.value ? 'week' : form.scope)
))
const visibleTargetDate = computed(() => (
  selectedProposal.value?.input_snapshot.target_date
  ?? viewedVersion.value?.target_date
  ?? form.target_date
))
const visibleDays = computed(() => (
  visibleScope.value === 'day'
    ? [visibleTargetDate.value]
    : Array.from({ length: 7 }, (_, index) => addDays(
      startOfWeek(visibleTargetDate.value),
      index,
    ))
))
const displayBlocks = computed<PlanBlock[]>(() => {
  if (selectedProposal.value) return proposalBlocks(selectedProposal.value)
  if (editing.value) return draftBlocks.value
  if (viewedVersion.value) return viewedVersion.value.blocks
  return currentPlan.value?.blocks.map(contextBlock) ?? []
})
const displayUnscheduled = computed<PlanUnscheduledItem[]>(() => {
  if (selectedProposal.value) return proposalUnscheduled(selectedProposal.value)
  if (editing.value) return draftUnscheduled.value
  if (viewedVersion.value) return viewedVersion.value.unscheduled_items
  return currentPlan.value?.unscheduled_items.map(contextUnscheduledItem) ?? []
})
const previewSummary = computed(() => (
  selectedProposal.value?.summary
  ?? viewedVersion.value?.summary
  ?? currentPlan.value?.summary
  ?? ''
))
const previewAssumptions = computed(() => (
  selectedProposal.value?.assumptions
  ?? viewedVersion.value?.assumptions
  ?? currentPlan.value?.assumptions
  ?? []
))
const jobActive = computed(() => (
  activeJob.value?.status === 'queued' || activeJob.value?.status === 'running'
))
const actionPending = computed(() => (
  applying.value || rejecting.value || saving.value || keepingSourceConflicts.value
))
const planTitle = computed(() => (
  form.scope === 'day'
    ? readableDate(form.target_date)
    : `${readableShort(startOfWeek(form.target_date))} - ${readableShort(addDays(startOfWeek(form.target_date), 6))}`
))

watch(authError, value => {
  if (value?.code === 'unauthenticated') void router.replace('/login')
})

onBeforeRouteLeave(() => {
  disposed = true
  window.clearTimeout(pollTimer)
  return true
})

onMounted(async () => {
  document.title = '规划 · iTime'
  await refreshSession()
  if (!identity.value) {
    await router.replace('/login')
    return
  }
  await Promise.all([loadContext(), loadProposals(), recoverJob()])
  const proposalId = typeof route.query.proposal === 'string'
    ? route.query.proposal
    : null
  if (proposalId) await openProposal(proposalId, false)
  window.addEventListener('focus', recoverOnFocus)
  document.addEventListener('visibilitychange', recoverOnFocus)
})

onBeforeUnmount(() => {
  disposed = true
  window.clearTimeout(pollTimer)
  window.removeEventListener('focus', recoverOnFocus)
  document.removeEventListener('visibilitychange', recoverOnFocus)
})

useSyncRefresh(
  [
    'task',
    'step',
    'task_occurrence',
    'time_settings',
    'term',
    'calendar_series',
    'calendar_exception',
    'calendar_addition',
    'calendar_source',
    'memory',
    'memory_settings',
    'plan',
  ],
  async () => {
    if (editing.value && dirty.value) {
      pageSuccess.value = '其他设备有更新；当前计划草稿已保留，请保存时比较最新版。'
      return
    }
    await refreshWorkspace()
  },
)

function pad(value: number) {
  return String(value).padStart(2, '0')
}

function localDate(value: Date) {
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`
}

function parseDate(value: string) {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day, 12)
}

function validDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false
  return localDate(parseDate(value)) === value
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

function addDays(value: string, amount: number) {
  const result = parseDate(value)
  result.setDate(result.getDate() + amount)
  return localDate(result)
}

function startOfWeek(value: string) {
  const result = parseDate(value)
  const weekday = result.getDay() || 7
  result.setDate(result.getDate() - weekday + 1)
  return localDate(result)
}

function readableDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  }).format(parseDate(value))
}

function readableShort(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
  }).format(parseDate(value))
}

function readableTime(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function sourceOccurrenceLabel(value: PlanSourceOccurrence | null) {
  if (!value) return ''
  const start = value.local_start_time?.slice(0, 5) ?? '全天'
  const end = value.local_end_time?.slice(0, 5)
  return `${readableShort(value.local_start_date)} ${start}${end ? `–${end}` : ''}`
}

function sourceChangeLabel(change: PlanSourceConflict['changes'][number]) {
  if (change.kind === 'added') {
    return `新增 ${sourceOccurrenceLabel(change.after)}`
  }
  if (change.kind === 'removed') {
    return `${sourceOccurrenceLabel(change.before)} → 已取消`
  }
  return `${sourceOccurrenceLabel(change.before)} → ${sourceOccurrenceLabel(change.after)}`
}

function sourceConflictDate(conflict: PlanSourceConflict) {
  const change = conflict.changes[0]
  return change?.after?.local_start_date
    ?? change?.before?.local_start_date
    ?? currentPlan.value?.week_start_date
    ?? form.target_date
}

function sourceConflictEvent(conflict: PlanSourceConflict) {
  return conflict.changes[0]?.event_id
}

function minutes(value: string) {
  const [hour, minute] = value.split(':').map(Number)
  return hour * 60 + minute
}

function minuteText(value: number) {
  return `${pad(Math.floor(value / 60))}:${pad(value % 60)}:00`
}

function proposalBlocks(proposal: PlanProposal): PlanBlock[] {
  return proposal.changes
    .filter(change => change.entity_type === 'scheduled_block' && change.after_data)
    .map((change, position) => {
      const data = change.after_data as Record<string, unknown>
      return {
        position,
        task_id: String(data.task_id),
        occurrence_key: data.occurrence_key ? String(data.occurrence_key) : null,
        task_title_snapshot: String(data.task_title ?? '未命名任务'),
        step_ids: Array.isArray(data.step_ids) ? data.step_ids.map(String) : [],
        step_titles_snapshot: Array.isArray(data.step_titles)
          ? data.step_titles.map(String)
          : [],
        local_date: String(data.local_date),
        local_starts_at: String(data.local_starts_at),
        local_ends_at: String(data.local_ends_at),
        time_zone: String(data.time_zone),
        starts_at: String(data.starts_at),
        ends_at: String(data.ends_at),
        estimate_source: data.estimate_source === 'ai' ? 'ai' : 'user',
        estimated_minutes: Number(data.estimated_minutes),
      }
    })
}

function planChange(proposal: PlanProposal): ProposalChange | undefined {
  return proposal.changes.find(change => change.entity_type === 'plan')
}

function proposalUnscheduled(proposal: PlanProposal): PlanUnscheduledItem[] {
  const raw = planChange(proposal)?.after_data?.unscheduled_items
  if (!Array.isArray(raw)) return []
  return raw.map((value, position) => {
    const item = value as Record<string, unknown>
    return {
      position,
      task_id: String(item.task_id),
      occurrence_key: item.occurrence_key ? String(item.occurrence_key) : null,
      task_title_snapshot: String(item.task_title ?? '未命名任务'),
      remaining_minutes: Number(item.remaining_minutes),
      reason: String(item.reason) as PlanUnscheduledItem['reason'],
      explanation: String(item.explanation),
    }
  })
}

async function replaceQuery(proposalId?: string | null) {
  if (disposed || route.path !== '/plans') return
  await router.replace({
    path: '/plans',
    query: {
      scope: form.scope,
      date: form.target_date,
      ...(proposalId ? { proposal: proposalId } : {}),
    },
  })
}

async function loadContext(keepSelection = false) {
  const revision = ++contextRevision
  loadingContext.value = true
  pageError.value = null
  try {
    const result = await getPlanningContext({
      ...form,
      task_ids: [],
    })
    if (revision !== contextRevision) return
    context.value = result
    form.density = result.density
    form.buffer_minutes = result.buffer_minutes
    form.availability_start = result.availability_start.slice(0, 5)
    form.availability_end = result.availability_end.slice(0, 5)
    const available = new Set(result.task_candidates.map(candidate => candidate.task_id))
    const retained = keepSelection
      ? form.task_ids.filter(id => available.has(id))
      : []
    form.task_ids = retained.length
      ? retained
      : [...available]
    loaded.value = true
    if (result.current_plan) await loadHistory(result.current_plan.id)
    else {
      history.value = []
      viewedVersion.value = null
    }
  } catch (reason) {
    if (revision === contextRevision) pageError.value = failure(reason)
  } finally {
    if (revision === contextRevision) loadingContext.value = false
  }
}

async function loadProposals() {
  loadingProposals.value = true
  try {
    proposals.value = await listPlanProposals()
  } catch (reason) {
    pageError.value = failure(reason)
  } finally {
    loadingProposals.value = false
  }
}

async function loadHistory(planId: string) {
  try {
    history.value = await getPlanHistory(planId)
    if (viewedVersion.value) {
      viewedVersion.value = history.value.find(
        item => item.version === viewedVersion.value?.version,
      ) ?? null
    }
  } catch (reason) {
    pageError.value = failure(reason)
  }
}

async function changeScope(scope: PlanScope) {
  if (form.scope === scope && !selectedProposal.value && !viewedVersion.value) return
  editing.value = false
  dirty.value = false
  selectedProposal.value = null
  viewedVersion.value = null
  form.scope = scope
  await replaceQuery()
  await loadContext()
}

async function changeDate(value: string) {
  if (!validDate(value)) return
  editing.value = false
  dirty.value = false
  selectedProposal.value = null
  viewedVersion.value = null
  form.target_date = value
  await replaceQuery()
  await loadContext()
}

function moveDate(direction: -1 | 1) {
  void changeDate(addDays(
    form.target_date,
    direction * (form.scope === 'week' ? 7 : 1),
  ))
}

async function refreshWorkspace() {
  await Promise.all([loadContext(true), loadProposals(), recoverJob()])
  if (selectedProposal.value) {
    await openProposal(selectedProposal.value.id)
  }
}

async function recoverJob() {
  try {
    const storedId = sessionStorage.getItem(activeJobStorageKey)
    if (storedId) {
      const stored = await getAIJob(storedId)
      if (disposed || route.path !== '/plans') return
      if (stored.capability === 'planning' && !stored.conversation_id) {
        activeJob.value = stored
        if (['queued', 'running'].includes(stored.status)) {
          schedulePoll()
          return
        }
        sessionStorage.removeItem(activeJobStorageKey)
        if (stored.status === 'succeeded' && stored.output?.proposal_id) {
          await loadProposals()
          await openProposal(stored.output.proposal_id)
          return
        }
      }
    }
    const jobs = await listPlanningJobs()
    const active = jobs.find(job => (
      !job.conversation_id && ['queued', 'running'].includes(job.status)
    ))
    if (active) {
      activeJob.value = active
      sessionStorage.setItem(activeJobStorageKey, active.id)
      schedulePoll()
    }
  } catch (reason) {
    pageError.value = failure(reason)
  }
}

function schedulePoll() {
  window.clearTimeout(pollTimer)
  if (!jobActive.value || document.hidden) return
  pollTimer = window.setTimeout(() => void pollJob(), 900)
}

async function pollJob() {
  if (!activeJob.value) return
  try {
    const job = await getAIJob(activeJob.value.id)
    if (disposed || route.path !== '/plans') return
    activeJob.value = job
    if (['queued', 'running'].includes(job.status)) {
      schedulePoll()
      return
    }
    if (job.status === 'succeeded' && job.output?.proposal_id) {
      sessionStorage.removeItem(activeJobStorageKey)
      pageSuccess.value = '规划提案已生成，请检查后确认。'
      await loadProposals()
      if (disposed || route.path !== '/plans') return
      await openProposal(job.output.proposal_id)
      return
    }
    sessionStorage.removeItem(activeJobStorageKey)
    pageError.value = {
      code: job.error?.code ?? 'planning_generation_failed',
      message: job.error?.message ?? '规划生成未能完成，请调整条件后重试。',
      request_id: null,
      saved: 'not_saved',
      retryable: job.status !== 'cancelled',
    }
  } catch (reason) {
    pageError.value = failure(reason)
    schedulePoll()
  }
}

async function keepCurrentPlanAfterSourceChanges() {
  if (!currentPlan.value || !sourceConflicts.value.length || keepingSourceConflicts.value) {
    return
  }
  const conflictIds = sourceConflicts.value.map(item => item.id).sort()
  const fingerprint = JSON.stringify({
    planId: currentPlan.value.id,
    planVersion: currentPlan.value.version,
    conflictIds,
  })
  keepConflictsOperation = keepConflictsOperation?.fingerprint === fingerprint
    ? keepConflictsOperation
    : { id: crypto.randomUUID(), fingerprint }
  keepingSourceConflicts.value = true
  pageError.value = null
  try {
    const keptVersion = currentPlan.value.version
    await keepPlanAfterSourceChanges(
      currentPlan.value.id,
      keptVersion,
      conflictIds,
      keepConflictsOperation.id,
    )
    keepConflictsOperation = null
    await loadContext(true)
    pageSuccess.value = `已保留第 ${keptVersion} 版计划，来源冲突已处理。`
  } catch (reason) {
    const currentError = failure(reason)
    if (currentError.code.includes('conflict')) {
      await loadContext(true)
    }
    pageError.value = currentError
  } finally {
    keepingSourceConflicts.value = false
  }
}

async function cancelGeneration() {
  if (!activeJob.value || !jobActive.value || cancelling.value) return
  cancelling.value = true
  pageError.value = null
  cancelOperation ||= crypto.randomUUID()
  try {
    const result = await cancelAIJob(activeJob.value.id, cancelOperation)
    cancelOperation = ''
    activeJob.value = result.job
    sessionStorage.removeItem(activeJobStorageKey)
    pageSuccess.value = '规划生成已取消，未扣除本次额度。'
  } catch (reason) {
    pageError.value = failure(reason)
  } finally {
    cancelling.value = false
  }
}

async function openProposal(
  proposal: PlanProposalSummary | string,
  updateQuery = true,
) {
  const id = typeof proposal === 'string' ? proposal : proposal.id
  if (loadingProposal.value || disposed || route.path !== '/plans') return
  loadingProposal.value = true
  editing.value = false
  dirty.value = false
  viewedVersion.value = null
  pageError.value = null
  try {
    const detail = await getPlanProposal(id)
    if (disposed || route.path !== '/plans') return
    selectedProposal.value = detail
    form.scope = detail.input_snapshot.scope
    form.target_date = detail.input_snapshot.target_date
    form.density = detail.input_snapshot.density
    form.buffer_minutes = detail.input_snapshot.buffer_minutes
    form.availability_start = detail.input_snapshot.availability_start.slice(0, 5)
    form.availability_end = detail.input_snapshot.availability_end.slice(0, 5)
    if (updateQuery) await replaceQuery(detail.id)
    await loadContext(true)
    await nextTick()
    document.querySelector<HTMLElement>('.plan-preview-heading h1')?.focus()
  } catch (reason) {
    pageError.value = failure(reason)
  } finally {
    loadingProposal.value = false
  }
}

function selectProposalFromControl(value: string) {
  if (!value) {
    selectedProposal.value = null
    void replaceQuery()
    return
  }
  void openProposal(value)
}

function proposalOperationId(action: 'apply' | 'reject', proposal: PlanProposal) {
  const fingerprint = `${action}:${proposal.id}:${proposal.version}`
  proposalOperation = proposalOperation?.fingerprint === fingerprint
    ? proposalOperation
    : { id: crypto.randomUUID(), fingerprint }
  return proposalOperation.id
}

async function applyProposal() {
  if (
    !selectedProposal.value
    || selectedProposal.value.effective_status !== 'pending'
    || applying.value
  ) return
  applying.value = true
  pageError.value = null
  try {
    const result = await applyPlanProposal(
      selectedProposal.value,
      proposalOperationId('apply', selectedProposal.value),
    )
    proposalOperation = null
    pageSuccess.value = `提案已写入计划，当前为第 ${result.application.plan_version} 版。`
    await Promise.all([loadContext(), loadProposals()])
    selectedProposal.value = await getPlanProposal(result.proposal.id)
  } catch (reason) {
    pageError.value = failure(reason)
    if (pageError.value.code.includes('conflict') || pageError.value.code.includes('expired')) {
      selectedProposal.value = await getPlanProposal(selectedProposal.value.id)
      await loadProposals()
    }
  } finally {
    applying.value = false
  }
}

async function rejectProposal() {
  if (
    !selectedProposal.value
    || selectedProposal.value.effective_status !== 'pending'
    || rejecting.value
  ) return
  rejecting.value = true
  pageError.value = null
  try {
    const result = await rejectPlanProposal(
      selectedProposal.value,
      proposalOperationId('reject', selectedProposal.value),
    )
    proposalOperation = null
    pageSuccess.value = '提案已拒绝，现有计划没有变化。'
    await loadProposals()
    selectedProposal.value = await getPlanProposal(result.proposal.id)
  } catch (reason) {
    pageError.value = failure(reason)
  } finally {
    rejecting.value = false
  }
}

async function showCurrentPlan() {
  selectedProposal.value = null
  viewedVersion.value = null
  form.scope = 'week'
  await replaceQuery()
  await loadContext()
}

async function beginEdit() {
  if (!currentPlan.value || editing.value) return
  if (form.scope !== 'week') {
    form.scope = 'week'
    await replaceQuery()
    await loadContext()
  }
  if (!currentPlan.value) return
  selectedProposal.value = null
  viewedVersion.value = null
  draftBlocks.value = currentPlan.value.blocks.map(contextBlock)
  draftUnscheduled.value = currentPlan.value.unscheduled_items.map(contextUnscheduledItem)
  editing.value = true
  dirty.value = false
  pageError.value = null
}

function cancelEdit() {
  editing.value = false
  dirty.value = false
  draftBlocks.value = []
  draftUnscheduled.value = []
  saveOperation = null
}

function updateBlockTime(
  index: number,
  field: 'local_starts_at' | 'local_ends_at',
  value: string,
) {
  if (!value || !draftBlocks.value[index]) return
  draftBlocks.value[index] = {
    ...draftBlocks.value[index],
    [field]: `${value}:00`,
  }
  dirty.value = true
}

function updateBlockStep(index: number, stepId: string, selected: boolean) {
  const block = draftBlocks.value[index]
  if (!block) return
  const candidate = context.value?.task_candidates.find(item => (
    item.task_id === block.task_id && item.occurrence_key === block.occurrence_key
  ))
  if (!candidate) return
  const stepIds = selected
    ? [...new Set([...block.step_ids, stepId])]
    : block.step_ids.filter(id => id !== stepId)
  const stepTitles = stepIds.map(id => (
    candidate.steps.find(step => step.id === id)?.title ?? ''
  )).filter(Boolean)
  draftBlocks.value[index] = {
    ...block,
    step_ids: stepIds,
    step_titles_snapshot: stepTitles,
  }
  dirty.value = true
}

function moveBlock(index: number, direction: -1 | 1) {
  const block = draftBlocks.value[index]
  if (!block || !currentPlan.value) return
  const dayIndices = draftBlocks.value
    .map((candidate, candidateIndex) => ({ candidate, candidateIndex }))
    .filter(item => item.candidate.local_date === block.local_date)
    .sort((left, right) => (
      left.candidate.local_starts_at.localeCompare(right.candidate.local_starts_at)
    ))
  const dayIndex = dayIndices.findIndex(item => item.candidateIndex === index)
  const neighbor = dayIndices[dayIndex + direction]
  if (!neighbor) return
  const firstIndex = direction === -1 ? neighbor.candidateIndex : index
  const secondIndex = direction === -1 ? index : neighbor.candidateIndex
  const first = draftBlocks.value[firstIndex]
  const second = draftBlocks.value[secondIndex]
  const base = Math.min(
    minutes(first.local_starts_at),
    minutes(second.local_starts_at),
  )
  const firstDuration = minutes(first.local_ends_at) - minutes(first.local_starts_at)
  const secondDuration = minutes(second.local_ends_at) - minutes(second.local_starts_at)
  const buffer = currentPlan.value.buffer_minutes
  const movedFirst = second
  const movedSecond = first
  const movedFirstDuration = secondDuration
  const movedSecondDuration = firstDuration
  draftBlocks.value[firstIndex] = {
    ...movedFirst,
    local_starts_at: minuteText(base),
    local_ends_at: minuteText(base + movedFirstDuration),
  }
  draftBlocks.value[secondIndex] = {
    ...movedSecond,
    local_starts_at: minuteText(base + movedFirstDuration + buffer),
    local_ends_at: minuteText(
      base + movedFirstDuration + buffer + movedSecondDuration,
    ),
  }
  dirty.value = true
}

async function savePlan() {
  if (!currentPlan.value || !editing.value || saving.value) return
  const body = {
    week_start_date: currentPlan.value.week_start_date,
    expected_plan_version: currentPlan.value.version,
    density: currentPlan.value.density,
    buffer_minutes: currentPlan.value.buffer_minutes,
    availability_start: currentPlan.value.availability_start,
    availability_end: currentPlan.value.availability_end,
    blocks: draftBlocks.value.map(block => ({
      task_id: block.task_id,
      occurrence_key: block.occurrence_key,
      local_date: block.local_date,
      starts_at: block.local_starts_at,
      ends_at: block.local_ends_at,
      step_ids: block.step_ids,
    })),
    unscheduled_items: draftUnscheduled.value.map(item => ({
      task_id: item.task_id,
      occurrence_key: item.occurrence_key,
      remaining_minutes: item.remaining_minutes,
      reason: item.reason,
      explanation: item.explanation,
    })),
  }
  const fingerprint = JSON.stringify(body)
  saveOperation = saveOperation?.fingerprint === fingerprint
    ? saveOperation
    : { id: crypto.randomUUID(), fingerprint }
  saving.value = true
  pageError.value = null
  try {
    const result = await saveManualPlanVersion({
      operation_id: saveOperation.id,
      ...body,
    })
    saveOperation = null
    editing.value = false
    dirty.value = false
    pageSuccess.value = `手工调整已保存为第 ${result.plan.version} 版。`
    await loadContext()
  } catch (reason) {
    pageError.value = failure(reason)
  } finally {
    saving.value = false
  }
}

function selectHistory(value: string) {
  editing.value = false
  dirty.value = false
  selectedProposal.value = null
  viewedVersion.value = value === 'current'
    ? null
    : history.value.find(item => item.version === Number(value)) ?? null
  void replaceQuery()
}

function recoverOnFocus() {
  if (document.hidden) {
    window.clearTimeout(pollTimer)
    return
  }
  if (jobActive.value) schedulePoll()
  else void recoverJob()
}

watch(pageError, async value => {
  if (!value) return
  await nextTick()
  document.querySelector<HTMLElement>('.plan-main .error-status')?.focus()
})
</script>

<template>
  <AppShell
    current="plans"
    has-secondary
    secondary-label="规划"
    title="规划"
    :pending-logout="pendingLogout"
    @logout="logout"
  >
    <template #secondary>
      <PlanNavigation
        :scope="form.scope"
        :proposals="proposals"
        :selected-proposal-id="selectedProposal?.id ?? null"
        :loading="loadingProposals"
        @scope="changeScope"
        @proposal="openProposal"
      />
    </template>
    <template #drawer-secondary="{ close }">
      <PlanNavigation
        :scope="form.scope"
        :proposals="proposals"
        :selected-proposal-id="selectedProposal?.id ?? null"
        :loading="loadingProposals"
        @scope="(v) => { changeScope(v); close() }"
        @proposal="(v) => { openProposal(v); close() }"
      />
    </template>

    <div class="plan-main">
      <header class="plan-toolbar">
        <div class="plan-toolbar-title">
          <span>{{ context?.time_zone ?? browserTimeZone }}</span>
          <h1 tabindex="-1">{{ planTitle }}</h1>
        </div>
        <div class="plan-date-controls">
          <button type="button" class="small-icon-button" aria-label="上一段日期" title="上一段日期" @click="moveDate(-1)">
            <ChevronLeft :size="19" aria-hidden="true" />
          </button>
          <label>
            <span class="sr-only">规划日期</span>
            <input
              :value="form.target_date"
              type="date"
              aria-label="规划日期"
              @change="changeDate(($event.target as HTMLInputElement).value)"
            />
          </label>
          <button type="button" class="small-icon-button" aria-label="下一段日期" title="下一段日期" @click="moveDate(1)">
            <ChevronRight :size="19" aria-hidden="true" />
          </button>
          <button type="button" class="small-icon-button" aria-label="刷新规划" title="刷新规划" :disabled="loadingContext" @click="refreshWorkspace">
            <RefreshCw :size="18" aria-hidden="true" />
          </button>
        </div>
      </header>

      <div
        v-if="pageError || pageSuccess || logoutError || identity?.user.ai_access === 'frozen'"
        class="plan-status"
      >
        <StatusMessage :error="logoutError" />
        <div v-if="identity?.user.ai_access === 'frozen'" class="status restriction-status" role="status">
          <CirclePause :size="20" aria-hidden="true" />
          <p>此账号的 AI 功能已暂停，已有计划仍可查看和手工调整。</p>
        </div>
        <StatusMessage
          :error="pageError"
          :success="pageSuccess"
          :retry-label="pageError?.retryable || pageError?.code.includes('conflict') ? '重新读取' : undefined"
          @retry="refreshWorkspace"
        />
      </div>

      <div class="plan-content">
        <aside class="plan-controls plan-controls-ai" aria-label="规划说明">
          <section class="plan-controls-hero">
            <Sparkles :size="24" aria-hidden="true" />
            <h2>规划现在交给 AI 对话</h2>
            <p>生成条件、任务挑选和重新规划，都在 AI 对话里完成。本页只展示当前计划、历史版本，并支持手工调整。</p>
            <RouterLink
              :to="{ path: '/ai', query: { prompt: '帮我生成本学期的本周规划' } }"
              class="button plan-generate"
            >
              <Sparkles :size="18" aria-hidden="true" />去 AI 对话让它生成
            </RouterLink>
            <RouterLink
              v-if="!context?.task_candidates?.length && loaded"
              to="/tasks"
              class="button secondary plan-task-link"
            >
              前往任务
            </RouterLink>
          </section>
          <section v-if="proposals.length" class="field plan-compact-proposals">
            <label for="plan-recent-proposal">最近 AI 提案</label>
            <select
              id="plan-recent-proposal"
              :value="selectedProposal?.id ?? ''"
              @change="selectProposalFromControl(($event.target as HTMLSelectElement).value)"
            >
              <option value="">当前计划</option>
              <option v-for="proposal in proposals" :key="proposal.id" :value="proposal.id">
                {{ proposal.title }} · {{ proposal.effective_status === 'pending' ? '待确认' : proposal.effective_status === 'applied' ? '已应用' : proposal.effective_status === 'conflicted' ? '需重生成' : proposal.effective_status === 'expired' ? '已过期' : '已拒绝' }}
              </option>
            </select>
          </section>
          <div v-if="jobActive" class="plan-job-state" role="status" aria-live="polite">
            <LoaderCircle :size="18" class="spin" aria-hidden="true" />
            <div>
              <strong>{{ activeJob?.status === 'queued' ? '规划正在排队' : '正在生成并校验' }}</strong>
              <p>离开本页不会中断，返回后会继续同步。</p>
            </div>
            <button type="button" class="text-button" :disabled="cancelling" @click="cancelGeneration">
              {{ cancelling ? '正在取消…' : '取消' }}
            </button>
          </div>
        </aside>

        <section
          class="plan-preview-pane"
          aria-labelledby="plan-preview-title"
          tabindex="0"
        >
          <header class="plan-preview-heading">
            <div>
              <span v-if="selectedProposal">AI 提案 · 尚未写入计划</span>
              <span v-else-if="viewedVersion">历史版本 · 只读</span>
              <span v-else-if="currentPlan">当前计划</span>
              <span v-else>计划预览</span>
              <h1 id="plan-preview-title" tabindex="-1">
                {{ selectedProposal?.title
                  ?? (viewedVersion ? `第 ${viewedVersion.version} 版`
                    : currentPlan ? `第 ${currentPlan.version} 版`
                      : '安排你的这一周') }}
              </h1>
            </div>

            <div v-if="currentPlan && !selectedProposal && !editing" class="plan-version-tools">
              <label>
                <span class="sr-only">查看计划版本</span>
                <select
                  aria-label="查看计划版本"
                  :value="viewedVersion?.version ?? 'current'"
                  @change="selectHistory(($event.target as HTMLSelectElement).value)"
                >
                  <option value="current">当前第 {{ currentPlan.version }} 版</option>
                  <option
                    v-for="version in history.filter(item => item.version !== currentPlan?.version)"
                    :key="version.id"
                    :value="version.version"
                  >
                    第 {{ version.version }} 版 · {{ version.source === 'ai' ? 'AI' : '手工' }}
                  </option>
                </select>
              </label>
              <button v-if="!viewedVersion" type="button" class="button secondary compact-button" @click="beginEdit">
                <Pencil :size="16" aria-hidden="true" />手工调整
              </button>
            </div>
          </header>

          <div v-if="loadingProposal" class="plan-preview-loading" role="status">
            正在读取提案…
          </div>

          <template v-else>
            <section
              v-if="currentPlan && !selectedProposal && !viewedVersion && !editing && sourceConflicts.length"
              class="plan-source-conflicts"
              role="alert"
              aria-labelledby="plan-source-conflicts-title"
            >
              <header>
                <AlertTriangle :size="21" aria-hidden="true" />
                <div>
                  <h2 id="plan-source-conflicts-title">订阅来源更新后，当前计划需复核</h2>
                  <p>第 {{ currentPlan.version }} 版计划没有被自动改动。</p>
                </div>
              </header>
              <div class="plan-source-conflict-list">
                <article
                  v-for="conflict in sourceConflicts"
                  :key="conflict.id"
                  class="plan-source-conflict"
                  role="region"
                  :aria-label="`${conflict.source_name}的计划变化`"
                >
                  <div class="plan-source-conflict-heading">
                    <div>
                      <strong>{{ conflict.source_name }}</strong>
                      <small>来源第 {{ conflict.source_generation }} 版 · {{ readableTime(conflict.detected_at) }}</small>
                    </div>
                    <span>
                      {{ conflict.details_limited
                        ? '变化较多 · 需查看日历'
                        : `${conflict.changes.length} 处变化 · ${conflict.affected_block_ids.length
                          ? `与 ${conflict.affected_block_ids.length} 个计划块重叠`
                          : '未与计划块重叠'}` }}
                    </span>
                  </div>
                  <p v-if="conflict.details_limited" class="plan-source-conflict-limited">
                    此来源在该周的事件超过安全展示上限，请在日历中缩小日期范围查看。
                  </p>
                  <details v-else>
                    <summary>查看变化时段</summary>
                    <ul>
                      <li v-for="change in conflict.changes" :key="`${change.occurrence_key}:${change.kind}`">
                        <strong>{{ change.name }}</strong>
                        <span>{{ sourceChangeLabel(change) }}</span>
                      </li>
                    </ul>
                  </details>
                  <RouterLink
                    :to="{
                      path: '/calendar',
                      query: {
                        date: sourceConflictDate(conflict),
                        source: conflict.source_id,
                        ...(sourceConflictEvent(conflict)
                          ? { event: sourceConflictEvent(conflict) }
                          : {}),
                      },
                    }"
                    class="text-button plan-source-calendar-link"
                  >
                    <CalendarSearch :size="16" aria-hidden="true" />在日历查看
                  </RouterLink>
                </article>
              </div>
              <div class="plan-source-conflict-actions">
                <RouterLink
                  :to="{ path: '/ai', query: { prompt: '订阅来源有新变化，帮我重新生成本周规划' } }"
                  class="button secondary"
                >
                  <Sparkles :size="17" aria-hidden="true" />去 AI 让它重新生成
                </RouterLink>
                <button
                  type="button"
                  class="button"
                  :disabled="actionPending"
                  @click="keepCurrentPlanAfterSourceChanges"
                >
                  <CircleCheck :size="17" aria-hidden="true" />
                  {{ keepingSourceConflicts ? '正在处理…' : '保留当前计划' }}
                </button>
              </div>
            </section>

            <div v-if="selectedProposal" class="proposal-state" :data-state="selectedProposal.effective_status">
              <CircleAlert v-if="selectedProposal.effective_status === 'conflicted'" :size="19" aria-hidden="true" />
              <XCircle v-else-if="['rejected', 'expired'].includes(selectedProposal.effective_status)" :size="19" aria-hidden="true" />
              <CircleCheck v-else-if="selectedProposal.effective_status === 'applied'" :size="19" aria-hidden="true" />
              <Sparkles v-else :size="19" aria-hidden="true" />
              <div>
                <strong>
                  {{ selectedProposal.effective_status === 'pending'
                    ? '这是预览，确认后才会写入计划'
                    : selectedProposal.effective_status === 'applied'
                      ? '此提案已写入计划'
                      : selectedProposal.effective_status === 'rejected'
                        ? '此提案已拒绝'
                        : selectedProposal.effective_status === 'expired'
                          ? '此提案已过期'
                          : '规划输入已变化' }}
                </strong>
                <p v-if="selectedProposal.effective_status === 'pending'">
                  有效至 {{ readableTime(selectedProposal.expires_at) }}，额度已在提案生成成功时结算。
                </p>
                <p v-else-if="selectedProposal.effective_status === 'conflicted'">
                  任务、固定安排或基础计划已变化，请按当前数据重新生成。
                </p>
              </div>
            </div>

            <div v-if="previewSummary || previewAssumptions.length" class="plan-summary">
              <p v-if="previewSummary">{{ previewSummary }}</p>
              <ul v-if="previewAssumptions.length">
                <li v-for="assumption in previewAssumptions" :key="assumption">{{ assumption }}</li>
              </ul>
            </div>

            <PlanPreview
              :days="visibleDays"
              :blocks="displayBlocks"
              :fixed-occurrences="context?.fixed_occurrences ?? []"
              :unscheduled-items="displayUnscheduled"
              :candidates="context?.task_candidates ?? []"
              :editable="editing"
              :pending="actionPending"
              @block-time="updateBlockTime"
              @block-step="updateBlockStep"
              @block-move="moveBlock"
            />
          </template>

          <div v-if="selectedProposal" class="proposal-actions">
            <button
              v-if="selectedProposal.effective_status === 'pending'"
              type="button"
              class="button secondary"
              :disabled="actionPending"
              @click="rejectProposal"
            >
              <XCircle :size="17" aria-hidden="true" />
              {{ rejecting ? '正在拒绝…' : '拒绝提案' }}
            </button>
            <button
              v-if="selectedProposal.effective_status === 'pending'"
              type="button"
              class="button"
              :disabled="actionPending"
              @click="applyProposal"
            >
              <CircleCheck :size="17" aria-hidden="true" />
              {{ applying ? '正在确认…' : '确认并写入计划' }}
            </button>
            <RouterLink
              v-else-if="['expired', 'conflicted'].includes(selectedProposal.effective_status)"
              :to="{ path: '/ai', query: { prompt: '原来的规划提案已过期，帮我按最新数据重新生成' } }"
              class="button"
            >
              <Sparkles :size="17" aria-hidden="true" />去 AI 让它重新生成
            </RouterLink>
            <button
              v-else-if="selectedProposal.effective_status === 'applied'"
              type="button"
              class="button secondary"
              @click="showCurrentPlan"
            >
              <CalendarClock :size="17" aria-hidden="true" />查看当前计划
            </button>
          </div>

          <div v-if="editing" class="plan-edit-actions">
            <span>{{ dirty ? '有未保存调整' : '尚未修改' }}</span>
            <button type="button" class="button secondary" :disabled="saving" @click="cancelEdit">取消</button>
            <button type="button" class="button" :disabled="saving || !dirty" @click="savePlan">
              {{ saving ? '正在保存…' : '保存新版本' }}
            </button>
          </div>
        </section>
      </div>
    </div>
  </AppShell>
</template>
