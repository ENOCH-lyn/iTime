<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import {
  Archive,
  ArchiveRestore,
  ArrowDown,
  ArrowLeft,
  ArrowUp,
  Check,
  CheckCircle2,
  CircleCheck,
  ExternalLink,
  Link2,
  LoaderCircle,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Timer,
  Trash2,
  TriangleAlert,
  X,
} from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import TaskScheduleEditor from './TaskScheduleEditor.vue'
import VoiceTranscriptionControl from './VoiceTranscriptionControl.vue'
import { ApiError, failure, localFailure, type Failure } from '../lib/api'
import { identity } from '../lib/auth'
import { appendTaskOperation } from '../lib/sync-queue'
import {
  createTask,
  createTaskStep,
  deleteTask as deleteTaskRequest,
  deleteTaskStep,
  getTask,
  refreshTaskLink,
  reorderTaskSteps,
  restoreTask,
  setTaskCompletion,
  setTaskArchived,
  syncTaskStepCompletion,
  updateTask,
  updateTaskStep,
  type Project,
  type TaskDetail,
  type TaskDraft,
  type TaskLinkFetch,
  type TaskStep,
} from '../lib/tasks'

const props = defineProps<{
  task: TaskDetail | null
  creating: boolean
  projects: Project[]
  initialProjectId: string | null
  externalSuccess: string
}>()

const emit = defineEmits<{
  close: []
  created: [task: TaskDetail]
  updated: [task: TaskDetail]
  lifecycle: [message: string]
  dirty: [dirty: boolean]
}>()

interface LinkDraft {
  id: string | null
  title: string
  url: string
  savedUrl: string | null
  fetch: TaskLinkFetch | null
}

const working = ref<TaskDetail | null>(null)
const values = reactive({
  title: '',
  description: '',
  project_id: '',
  estimated_minutes: '',
  priority: 0,
  labels: '',
})
const links = ref<LinkDraft[]>([])
const initialSteps = ref<string[]>([])
const stepTitles = reactive<Record<string, string>>({})
const newStep = ref('')
const pendingAction = ref('')
const error = ref<Failure | null>(null)
const success = ref('')
const latest = ref<TaskDetail | null>(null)
const fieldErrors = reactive<Record<string, string>>({})
const scheduleDirty = ref(false)
const operations = new Map<string, { id: string; fingerprint: string }>()
const priorityNames = ['未设置', '低', '中', '高']
const editable = computed(() => props.creating || working.value?.lifecycle === 'active')
const headerState = computed(() => {
  if (props.creating) return '新任务'
  if (working.value?.lifecycle === 'archived') return '已归档'
  if (working.value?.lifecycle === 'trashed') return '回收站'
  return working.value?.status === 'completed' ? '已完成' : '未完成'
})

const dirty = computed(() => {
  if (props.creating) {
    return Boolean(
      values.title
      || values.description
      || String(values.estimated_minutes)
      || values.labels
      || links.value.some(link => link.title || link.url)
      || initialSteps.value.some(Boolean),
    )
  }
  if (!working.value) return false
  return scheduleDirty.value
    || values.title !== working.value.title
    || values.description !== working.value.description
    || values.project_id !== (working.value.project?.id ?? '')
    || String(values.estimated_minutes) !== (working.value.estimated_minutes?.toString() ?? '')
    || values.priority !== working.value.priority
    || values.labels !== working.value.labels.map(label => label.name).join(', ')
    || JSON.stringify(links.value.map(link => ({
      title: link.title,
      url: link.url,
    }))) !== JSON.stringify(
      working.value.links.map(link => ({ title: link.title, url: link.url })),
    )
    || !!newStep.value
    || working.value.steps.some(step => stepTitles[step.id] !== step.title)
})

watch(dirty, value => emit('dirty', value), { immediate: true })
watch([() => props.task?.id, () => props.creating, () => props.initialProjectId], () => {
  working.value = props.task
  resetFields(props.task)
  error.value = null
  success.value = ''
  latest.value = null
  operations.clear()
}, { immediate: true })
watch(() => props.task, task => {
  if (
    !task
    || task.id !== working.value?.id
    || task.version <= working.value.version
    || dirty.value
  ) return
  working.value = task
  resetFields(task)
  latest.value = null
  operations.clear()
})
watch(
  () => props.task?.links.map(link => [
    link.id,
    link.fetch.status,
    link.fetch.fetched_at,
    link.fetch.last_attempt_at,
    link.fetch.error?.code ?? '',
  ].join(':')).join('|'),
  () => {
    if (!props.task || props.task.id !== working.value?.id) return
    working.value = props.task
    mergeLinkStates(props.task)
  },
)

function beforeUnload(event: BeforeUnloadEvent) {
  if (!dirty.value) return
  event.preventDefault()
}
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onUnmounted(() => {
  window.removeEventListener('beforeunload', beforeUnload)
  emit('dirty', false)
})

function resetFields(task: TaskDetail | null) {
  scheduleDirty.value = false
  values.title = task?.title ?? ''
  values.description = task?.description ?? ''
  values.project_id = task?.project?.id ?? props.initialProjectId ?? ''
  values.estimated_minutes = task?.estimated_minutes?.toString() ?? ''
  values.priority = task?.priority ?? 0
  values.labels = task?.labels.map(label => label.name).join(', ') ?? ''
  links.value = task?.links.map(link => ({
    id: link.id,
    title: link.title,
    url: link.url,
    savedUrl: link.url,
    fetch: link.fetch,
  })) ?? []
  initialSteps.value = []
  for (const key of Object.keys(stepTitles)) delete stepTitles[key]
  for (const step of task?.steps ?? []) stepTitles[step.id] = step.title
  newStep.value = ''
  for (const key of Object.keys(fieldErrors)) delete fieldErrors[key]
}

function mergeLinkStates(task: TaskDetail) {
  const serverLinks = new Map(task.links.map(link => [link.id, link]))
  for (const draft of links.value) {
    if (!draft.id || draft.url !== draft.savedUrl) continue
    const saved = serverLinks.get(draft.id)
    if (!saved || saved.url !== draft.savedUrl) continue
    draft.fetch = saved.fetch
  }
}

function operationId(scope: string, body: unknown) {
  const fingerprint = JSON.stringify(body)
  const existing = operations.get(scope)
  if (existing?.fingerprint === fingerprint) return existing.id
  const operation = { id: crypto.randomUUID(), fingerprint }
  operations.set(scope, operation)
  return operation.id
}

function clearMessages() {
  error.value = null
  success.value = ''
  latest.value = null
}

function replaceVoiceTitle(text: string) {
  values.title = text
  delete fieldErrors.title
}

function appendVoiceTitle(text: string) {
  values.title = [values.title.trim(), text].filter(Boolean).join('\n')
  delete fieldErrors.title
}

function parsedLabels() {
  const seen = new Set<string>()
  return values.labels
    .split(/[,，\n]/)
    .map(label => label.trim().replace(/\s+/g, ' '))
    .filter(label => {
      const normalized = label.toLocaleLowerCase()
      if (!label || seen.has(normalized)) return false
      seen.add(normalized)
      return true
    })
}

function draft(): TaskDraft | null {
  for (const key of Object.keys(fieldErrors)) delete fieldErrors[key]
  const title = values.title.trim()
  if (!title) fieldErrors.title = '请输入任务名称。'
  let estimated: number | null = null
  const estimatedValue = String(values.estimated_minutes).trim()
  if (estimatedValue) {
    estimated = Number(estimatedValue)
    if (!Number.isInteger(estimated) || estimated < 1 || estimated > 525_600) {
      fieldErrors.estimated_minutes = '请输入 1—525600 之间的整数分钟数。'
    }
  }
  const labels = parsedLabels()
  if (labels.length > 10) fieldErrors.labels = '每个任务最多 10 个标签。'
  const cleanedLinks: TaskDraft['links'] = []
  for (const [index, link] of links.value.entries()) {
    const titleValue = link.title.trim()
    const urlValue = link.url.trim()
    if (!titleValue && !urlValue) continue
    if (!urlValue) {
      fieldErrors[`link-${index}`] = '请输入链接地址。'
      continue
    }
    try {
      const url = new URL(urlValue)
      if (!['http:', 'https:'].includes(url.protocol)) throw new Error('protocol')
      cleanedLinks.push({ title: titleValue, url: url.toString() })
    } catch {
      fieldErrors[`link-${index}`] = '请输入有效的 HTTP 或 HTTPS 地址。'
    }
  }
  if (cleanedLinks.length > 20) fieldErrors.links = '每个任务最多 20 个链接。'
  if (Object.keys(fieldErrors).length) return null
  return {
    title,
    description: values.description,
    project_id: values.project_id || null,
    estimated_minutes: estimated,
    priority: values.priority,
    labels,
    links: cleanedLinks,
  }
}

async function focusFirstError() {
  await nextTick()
  const key = Object.keys(fieldErrors)[0]
  if (key) document.getElementById(key)?.focus()
}

function applyTask(task: TaskDetail, reset = false) {
  const drafts = { ...stepTitles }
  working.value = task
  for (const key of Object.keys(stepTitles)) delete stepTitles[key]
  for (const step of task.steps) {
    stepTitles[step.id] = !reset && drafts[step.id] !== undefined
      ? drafts[step.id]!
      : step.title
  }
  if (reset) resetFields(task)
  else mergeLinkStates(task)
  latest.value = null
  emit('updated', task)
}

async function handleError(reason: unknown) {
  error.value = failure(reason)
  success.value = ''
  if (working.value && error.value.code === 'task_version_conflict') {
    try { latest.value = await getTask(working.value.id) }
    catch { error.value = localFailure('无法读取服务器最新版。当前填写仍保留，请稍后重试。') }
  }
  await nextTick()
  document.querySelector<HTMLElement>('.task-editor .error-status')?.focus()
}

function keepDraftOnLatest() {
  if (!latest.value) return
  applyTask(latest.value)
  error.value = null
  success.value = '已载入最新版本号；当前填写仍保留，请检查后再次保存。'
  operations.clear()
}

function useServerVersion() {
  if (!latest.value) return
  applyTask(latest.value, true)
  error.value = null
  success.value = '已改用服务器上的最新内容。'
  operations.clear()
}

async function save() {
  if (pendingAction.value || !editable.value) return
  clearMessages()
  const fields = draft()
  if (!fields) {
    await focusFirstError()
    return
  }
  pendingAction.value = 'save'
  try {
    if (props.creating) {
      const steps = initialSteps.value
        .map(title => ({ title: title.trim() }))
        .filter(step => step.title)
      if (steps.length > 100) throw localFailure('每个任务最多 100 个步骤。')
      const body = { ...fields, steps }
      const task = await createTask(operationId('create', body), body)
      operations.clear()
      success.value = '任务已创建。'
      emit('created', task)
    } else if (working.value) {
      const body = { ...fields, expected_version: working.value.version }
      const task = await updateTask(
        working.value.id,
        operationId('update', body),
        working.value.version,
        fields,
      )
      operations.delete('update')
      applyTask(task, true)
      success.value = '修改已保存。'
    }
  } catch (reason) {
    await handleError(reason)
  } finally {
    pendingAction.value = ''
  }
}

async function toggleCompletion() {
  if (!working.value || pendingAction.value || !editable.value) return
  clearMessages()
  pendingAction.value = 'completion'
  const completed = working.value.status !== 'completed'
  try {
    const body = { expected_version: working.value.version, completed }
    const task = await setTaskCompletion(
      working.value,
      operationId('completion', body),
      completed,
    )
    operations.delete('completion')
    applyTask(task)
    success.value = completed ? '任务已明确标为完成。' : '任务已重新打开。'
  } catch (reason) {
    await handleError(reason)
  } finally {
    pendingAction.value = ''
  }
}

async function addStep() {
  if (!working.value || pendingAction.value || !editable.value) return
  const title = newStep.value.trim()
  if (!title) {
    fieldErrors.newStep = '请输入步骤内容。'
    await focusFirstError()
    return
  }
  clearMessages()
  delete fieldErrors.newStep
  pendingAction.value = 'step-create'
  try {
    const body = { expected_task_version: working.value.version, title }
    const task = await createTaskStep(
      working.value,
      operationId('step-create', body),
      title,
    )
    operations.delete('step-create')
    newStep.value = ''
    applyTask(task)
    success.value = '步骤已添加；任务仍需单独确认完成。'
  } catch (reason) {
    await handleError(reason)
  } finally {
    pendingAction.value = ''
  }
}

async function saveStep(step: TaskStep, completed = step.completed) {
  if (!working.value || pendingAction.value || !editable.value) return
  const title = stepTitles[step.id]?.trim() ?? ''
  if (!title) {
    fieldErrors[`step-${step.id}`] = '步骤内容不能为空。'
    await focusFirstError()
    return
  }
  clearMessages()
  delete fieldErrors[`step-${step.id}`]
  pendingAction.value = `step-${step.id}`
  const completionOnly = (
    completed !== step.completed
    && title === step.title
    && working.value.schedule.recurrence.frequency === 'none'
  )
  const body = {
    expected_task_version: working.value.version,
    title,
    completed,
  }
  const operation = operationId(`step-${step.id}`, body)
  try {
    const task = completionOnly
      ? (
          await syncTaskStepCompletion(
            working.value,
            step,
            operation,
            completed,
          )
        ).task
      : await updateTaskStep(
          working.value,
          step.id,
          operation,
          title,
          completed,
        )
    operations.delete(`step-${step.id}`)
    applyTask(task)
    success.value = completed
      ? '步骤已勾选；任务不会自动完成。'
      : (step.completed ? '步骤已重新打开。' : '步骤已保存。')
  } catch (reason) {
    if (
      completionOnly
      && identity.value
      && (
        !navigator.onLine
        || (
          reason instanceof ApiError
          && ['network_error', 'request_timeout'].includes(reason.info.code)
        )
      )
    ) {
      try {
        appendTaskOperation(identity.value.user.id, {
          schema_version: 1,
          kind: 'task_step_completion',
          operation_id: operation,
          task_id: working.value.id,
          step_id: step.id,
          expected_task_version: working.value.version,
          base_completed: step.completed,
          completed,
          created_at: new Date().toISOString(),
        })
        const projected: TaskDetail = {
          ...working.value,
          version: working.value.version + 1,
          steps: working.value.steps.map(item => item.id === step.id
            ? {
                ...item,
                completed,
                completed_at: completed ? new Date().toISOString() : null,
              }
            : item),
          step_progress: {
            total: working.value.step_progress.total,
            completed: working.value.steps.filter(item =>
              item.id === step.id ? completed : item.completed).length,
          },
        }
        applyTask(projected)
        success.value = '步骤操作已保存在本设备，联网后同步。'
      } catch (queueError) {
        await handleError(queueError)
      }
    } else {
      await handleError(reason)
    }
  } finally {
    pendingAction.value = ''
  }
}

async function removeStep(step: TaskStep) {
  if (!working.value || pendingAction.value || !editable.value) return
  clearMessages()
  pendingAction.value = `delete-${step.id}`
  try {
    const body = { expected_task_version: working.value.version }
    const task = await deleteTaskStep(
      working.value,
      step.id,
      operationId(`delete-${step.id}`, body),
    )
    operations.delete(`delete-${step.id}`)
    applyTask(task)
    success.value = '步骤已移除。'
  } catch (reason) {
    await handleError(reason)
  } finally {
    pendingAction.value = ''
  }
}

async function moveStep(step: TaskStep, direction: -1 | 1) {
  if (!working.value || pendingAction.value || !editable.value) return
  const index = working.value.steps.findIndex(item => item.id === step.id)
  const target = index + direction
  if (target < 0 || target >= working.value.steps.length) return
  clearMessages()
  pendingAction.value = `move-${step.id}`
  const ids = working.value.steps.map(item => item.id)
  ;[ids[index], ids[target]] = [ids[target]!, ids[index]!]
  try {
    const body = { expected_task_version: working.value.version, step_ids: ids }
    const task = await reorderTaskSteps(
      working.value,
      operationId(`move-${step.id}`, body),
      ids,
    )
    operations.delete(`move-${step.id}`)
    applyTask(task)
    success.value = '步骤顺序已更新。'
  } catch (reason) {
    await handleError(reason)
  } finally {
    pendingAction.value = ''
  }
}

function addLink() {
  if (links.value.length < 20) {
    links.value.push({
      id: null,
      title: '',
      url: '',
      savedUrl: null,
      fetch: null,
    })
  }
}

function addInitialStep() {
  if (initialSteps.value.length < 100) initialSteps.value.push('')
}

function isHttpUrl(value: string) {
  return value.startsWith('http://') || value.startsWith('https://')
}

function linkStatusLabel(fetch: TaskLinkFetch) {
  if (fetch.status === 'queued') return '等待读取'
  if (fetch.status === 'running') return '正在读取'
  if (fetch.status === 'ready') return '已读取'
  return '读取失败'
}

async function refreshLink(link: LinkDraft) {
  if (
    !working.value
    || !link.id
    || link.url !== link.savedUrl
    || pendingAction.value
    || !editable.value
  ) return
  clearMessages()
  const scope = `link-refresh-${link.id}`
  pendingAction.value = scope
  try {
    const task = await refreshTaskLink(
      working.value.id,
      link.id,
      operationId(scope, { link_id: link.id }),
    )
    operations.delete(scope)
    applyTask(task)
    const refreshed = task.links.find(item => item.id === link.id)
    success.value = refreshed?.fetch.using_previous_snapshot
      ? '已重新排队，读取期间继续使用上次摘要。'
      : '链接已排队读取。'
  } catch (reason) {
    await handleError(reason)
  } finally {
    pendingAction.value = ''
  }
}

function formatDateTime(value: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN') : ''
}

async function changeLifecycle(action: 'archive' | 'unarchive' | 'delete' | 'restore') {
  if (!working.value || pendingAction.value) return
  if (
    action === 'delete'
    && !window.confirm('删除后任务会进入回收站并保留 30 天，期间可以恢复。确定移到回收站吗？')
  ) return
  clearMessages()
  pendingAction.value = action
  const body = {
    expected_version: working.value.version,
    ...(action === 'archive' || action === 'unarchive'
      ? { archived: action === 'archive' }
      : {}),
  }
  const scope = `lifecycle-${action}`
  try {
    const id = operationId(scope, body)
    const task = action === 'archive' || action === 'unarchive'
      ? await setTaskArchived(working.value, id, action === 'archive')
      : action === 'delete'
        ? await deleteTaskRequest(working.value, id)
        : await restoreTask(working.value, id)
    operations.delete(scope)
    emit('lifecycle', {
      archive: '任务已归档。',
      unarchive: '任务已取消归档。',
      delete: '任务已移到回收站，将保留 30 天。',
      restore: task.lifecycle === 'archived'
        ? '任务已恢复到已归档。'
        : '任务已恢复到活动任务。',
    }[action])
  } catch (reason) {
    await handleError(reason)
  } finally {
    pendingAction.value = ''
  }
}
</script>

<template>
  <section class="task-editor"
    :aria-label="creating ? '新建任务' : task ? `${editable ? '编辑' : '查看'}任务 ${task.title}` : '任务详情'">
    <div v-if="!creating && !working" class="task-editor-placeholder">
      <CheckCircle2 :size="34" aria-hidden="true" />
      <h2>选择一项任务</h2>
      <p>在列表中打开任务，查看内容和步骤。</p>
    </div>

    <form v-else class="task-editor-form" novalidate @submit.prevent="save">
      <header class="task-editor-header">
        <button type="button" class="icon-button mobile-back" title="返回任务列表"
          aria-label="返回任务列表" @click="emit('close')">
          <ArrowLeft :size="20" aria-hidden="true" />
        </button>
        <div>
          <p>{{ headerState }}</p>
          <h1>{{ creating ? '记下一件要做的事' : '任务详情' }}</h1>
        </div>
        <div class="task-editor-actions">
          <RouterLink
            v-if="working && editable && working.status === 'open'"
            :to="{ path: '/focus', query: { task: working.id } }"
            class="button secondary"
          >
            <Timer :size="17" aria-hidden="true" />
            开始专注
          </RouterLink>
          <button v-if="working && editable && working.schedule.recurrence.frequency === 'none'"
            type="button" class="button secondary"
            :disabled="!!pendingAction" @click="toggleCompletion">
            <RotateCcw v-if="working.status === 'completed'" :size="17" aria-hidden="true" />
            <CheckCircle2 v-else :size="17" aria-hidden="true" />
            {{ pendingAction === 'completion' ? '正在保存…' : working.status === 'completed' ? '重新打开' : '标为完成' }}
          </button>
          <button v-if="editable" type="submit" class="button" :disabled="!!pendingAction">
            <Save :size="17" aria-hidden="true" />
            {{ pendingAction === 'save' ? '正在保存…' : creating ? '创建任务' : '保存修改' }}
          </button>
        </div>
      </header>

      <div class="task-editor-messages">
        <StatusMessage :error="error" :success="success || externalSuccess" />
        <div v-if="working" class="task-lifecycle-bar" :data-lifecycle="working.lifecycle">
          <div class="task-lifecycle-copy">
            <Archive v-if="working.lifecycle === 'archived'" :size="20" aria-hidden="true" />
            <Trash2 v-else-if="working.lifecycle === 'trashed'" :size="20" aria-hidden="true" />
            <div>
              <strong v-if="working.lifecycle === 'active'">整理任务</strong>
              <strong v-else-if="working.lifecycle === 'archived'">此任务已归档</strong>
              <strong v-else>此任务在回收站中</strong>
              <p v-if="working.lifecycle === 'active'">归档不会改变任务的完成状态。</p>
              <p v-else-if="working.lifecycle === 'archived'">
                当前内容只读。取消归档后可以继续编辑。
              </p>
              <p v-else>
                当前内容只读，将于 {{ formatDateTime(working.purge_after) }} 清理
                <template v-if="working.trash_remaining_days !== null">
                  （{{ working.trash_remaining_days ? `还剩 ${working.trash_remaining_days} 天` : '即将清理' }}）
                </template>。
              </p>
            </div>
          </div>
          <div class="task-lifecycle-actions">
            <button v-if="working.lifecycle === 'active'" type="button"
              class="button secondary compact-button" :disabled="!!pendingAction"
              @click="changeLifecycle('archive')">
              <Archive :size="16" aria-hidden="true" />
              {{ pendingAction === 'archive' ? '正在归档…' : '归档' }}
            </button>
            <button v-else-if="working.lifecycle === 'archived'" type="button"
              class="button secondary compact-button" :disabled="!!pendingAction"
              @click="changeLifecycle('unarchive')">
              <ArchiveRestore :size="16" aria-hidden="true" />
              {{ pendingAction === 'unarchive' ? '正在恢复…' : '取消归档' }}
            </button>
            <button v-if="working.lifecycle !== 'trashed'" type="button"
              class="button secondary compact-button lifecycle-delete"
              :disabled="!!pendingAction" @click="changeLifecycle('delete')">
              <Trash2 :size="16" aria-hidden="true" />
              {{ pendingAction === 'delete' ? '正在删除…' : '移到回收站' }}
            </button>
            <button v-else type="button" class="button compact-button"
              :disabled="!!pendingAction" @click="changeLifecycle('restore')">
              <RotateCcw :size="16" aria-hidden="true" />
              {{ pendingAction === 'restore' ? '正在恢复…' : '恢复任务' }}
            </button>
          </div>
        </div>
        <div v-if="latest" class="conflict-comparison">
          <strong>服务器最新版</strong>
          <p>{{ latest.title }} · {{ latest.status === 'completed' ? '已完成' : '未完成' }} · 版本 {{ latest.version }}</p>
          <p v-if="latest.description">{{ latest.description }}</p>
          <div class="inline-actions">
            <button type="button" class="text-button" @click="keepDraftOnLatest">保留填写并基于最新版重试</button>
            <button type="button" class="text-button" @click="useServerVersion">改用服务器内容</button>
          </div>
        </div>
      </div>

      <fieldset class="task-editor-body" :disabled="!!pendingAction || !editable">
        <section class="editor-section primary-fields">
          <div class="field">
            <label for="task-title">任务名称</label>
            <textarea id="task-title" v-model="values.title" class="task-title-input"
              rows="1" maxlength="200" :disabled="!!pendingAction"
              :aria-invalid="!!fieldErrors.title" :aria-describedby="fieldErrors.title ? 'task-title-error' : undefined"
              placeholder="例如：完成论文提纲"></textarea>
            <p v-if="fieldErrors.title" id="task-title-error" class="field-error">{{ fieldErrors.title }}</p>
            <VoiceTranscriptionControl
              v-if="editable"
              purpose="task_title"
              target-label="任务名称"
              :field-limit="200"
              :current-text="values.title"
              :disabled="!!pendingAction"
              @replace="replaceVoiceTitle"
              @append="appendVoiceTitle"
            />
          </div>
          <div class="field">
            <label for="task-description">正文 <span class="muted">可选</span></label>
            <textarea id="task-description" v-model="values.description" maxlength="20000"
              rows="7" :disabled="!!pendingAction" placeholder="补充背景、要求或想法"></textarea>
          </div>
        </section>

        <section class="editor-section task-properties" aria-labelledby="task-properties-title">
          <h2 id="task-properties-title">安排信息</h2>
          <div class="property-grid">
            <div class="field">
              <label for="task-project">项目或目标</label>
              <select id="task-project" v-model="values.project_id" :disabled="!!pendingAction">
                <option value="">收集箱</option>
                <optgroup label="目标">
                  <option v-for="project in projects.filter(item => item.kind === 'goal')"
                    :key="project.id" :value="project.id">{{ project.name }}</option>
                </optgroup>
                <optgroup label="项目">
                  <option v-for="project in projects.filter(item => item.kind === 'project')"
                    :key="project.id" :value="project.id">{{ project.name }}</option>
                </optgroup>
              </select>
            </div>
            <div class="field">
              <label for="task-priority">优先级</label>
              <select id="task-priority" v-model="values.priority" :disabled="!!pendingAction">
                <option v-for="(name, index) in priorityNames" :key="name" :value="index">{{ name }}</option>
              </select>
            </div>
            <div class="field">
              <label for="estimated_minutes">预计用时 <span class="muted">分钟</span></label>
              <input id="estimated_minutes" v-model="values.estimated_minutes" type="number"
                inputmode="numeric" min="1" max="525600" :disabled="!!pendingAction"
                :aria-invalid="!!fieldErrors.estimated_minutes"
                :aria-describedby="fieldErrors.estimated_minutes ? 'estimated-error' : undefined"
                placeholder="未设置" />
              <p v-if="fieldErrors.estimated_minutes" id="estimated-error" class="field-error">
                {{ fieldErrors.estimated_minutes }}
              </p>
            </div>
            <div class="field">
              <label for="labels">标签 <span class="muted">逗号分隔</span></label>
              <input id="labels" v-model="values.labels" maxlength="420" :disabled="!!pendingAction"
                :aria-invalid="!!fieldErrors.labels"
                :aria-describedby="fieldErrors.labels ? 'labels-error' : undefined"
                placeholder="学习, 深度工作" />
              <p v-if="fieldErrors.labels" id="labels-error" class="field-error">{{ fieldErrors.labels }}</p>
            </div>
          </div>
        </section>

        <TaskScheduleEditor
          v-if="working"
          :task="working"
          :read-only="!editable"
          @updated="applyTask"
          @dirty="scheduleDirty = $event"
        />

        <section class="editor-section" aria-labelledby="task-steps-title">
          <div class="editor-section-heading">
            <div>
              <h2 id="task-steps-title">步骤</h2>
              <p v-if="working">{{ working.step_progress.completed }}/{{ working.step_progress.total }} 已完成</p>
            </div>
            <button v-if="creating" type="button" class="button secondary compact-button"
              :disabled="!!pendingAction || initialSteps.length >= 100" @click="addInitialStep">
              <Plus :size="16" aria-hidden="true" />添加步骤
            </button>
          </div>

          <div v-if="creating" class="initial-step-list">
            <div v-for="(_, index) in initialSteps" :key="index" class="draft-row">
              <label class="sr-only" :for="`initial-step-${index}`">步骤 {{ index + 1 }}</label>
              <span>{{ index + 1 }}</span>
              <input :id="`initial-step-${index}`" v-model="initialSteps[index]" maxlength="300"
                :disabled="!!pendingAction" placeholder="步骤内容" />
              <button type="button" class="small-icon-button" :aria-label="`移除步骤 ${index + 1}`"
                title="移除步骤" @click="initialSteps.splice(index, 1)">
                <X :size="16" aria-hidden="true" />
              </button>
            </div>
          </div>

          <template v-else-if="working">
            <ul class="step-list">
              <li v-for="(step, index) in working.steps" :key="step.id">
                <label class="step-check">
                  <input type="checkbox" :checked="step.completed" :disabled="!!pendingAction"
                    :aria-label="`${step.completed ? '重新打开' : '完成'}步骤 ${step.title}`"
                    @change="saveStep(step, ($event.target as HTMLInputElement).checked)" />
                </label>
                <div class="step-input">
                  <label class="sr-only" :for="`step-${step.id}`">步骤 {{ index + 1 }}</label>
                  <input :id="`step-${step.id}`" v-model="stepTitles[step.id]" maxlength="300"
                    :disabled="!!pendingAction" :class="{ completed: step.completed }"
                    :aria-invalid="!!fieldErrors[`step-${step.id}`]"
                    :aria-describedby="fieldErrors[`step-${step.id}`] ? `step-error-${step.id}` : undefined" />
                  <p v-if="fieldErrors[`step-${step.id}`]" :id="`step-error-${step.id}`" class="field-error">
                    {{ fieldErrors[`step-${step.id}`] }}
                  </p>
                </div>
                <div v-if="editable" class="step-actions">
                  <button type="button" class="small-icon-button" title="保存步骤"
                    :aria-label="`保存步骤 ${step.title}`" :disabled="!!pendingAction"
                    @click="saveStep(step)">
                    <Check :size="15" aria-hidden="true" />
                  </button>
                  <button type="button" class="small-icon-button" title="上移"
                    :aria-label="`上移步骤 ${step.title}`" :disabled="!!pendingAction || index === 0"
                    @click="moveStep(step, -1)">
                    <ArrowUp :size="15" aria-hidden="true" />
                  </button>
                  <button type="button" class="small-icon-button" title="下移"
                    :aria-label="`下移步骤 ${step.title}`"
                    :disabled="!!pendingAction || index === working.steps.length - 1"
                    @click="moveStep(step, 1)">
                    <ArrowDown :size="15" aria-hidden="true" />
                  </button>
                  <button type="button" class="small-icon-button danger-icon" title="移除步骤"
                    :aria-label="`移除步骤 ${step.title}`" :disabled="!!pendingAction"
                    @click="removeStep(step)">
                    <Trash2 :size="15" aria-hidden="true" />
                  </button>
                </div>
              </li>
            </ul>
            <div v-if="editable" class="new-step-row">
              <label class="sr-only" for="newStep">新步骤</label>
              <input id="newStep" v-model="newStep" maxlength="300" :disabled="!!pendingAction"
                :aria-invalid="!!fieldErrors.newStep"
                :aria-describedby="fieldErrors.newStep ? 'new-step-error' : undefined"
                placeholder="添加下一步" @keydown.enter.prevent="addStep" />
              <button type="button" class="button secondary compact-button" :disabled="!!pendingAction"
                @click="addStep">
                <Plus :size="16" aria-hidden="true" />添加
              </button>
            </div>
            <p v-if="fieldErrors.newStep" id="new-step-error" class="field-error">{{ fieldErrors.newStep }}</p>
          </template>
          <p v-if="(creating && !initialSteps.length) || (!creating && working && !working.steps.length)"
            class="section-empty">暂无步骤。</p>
        </section>

        <section class="editor-section" aria-labelledby="task-links-title">
          <div class="editor-section-heading">
            <div>
              <h2 id="task-links-title">链接</h2>
              <p>{{ links.length }} 条</p>
            </div>
            <button v-if="editable" type="button" class="button secondary compact-button"
              :disabled="!!pendingAction || links.length >= 20" @click="addLink">
              <Plus :size="16" aria-hidden="true" />添加链接
            </button>
          </div>
          <div class="link-list">
            <div v-for="(link, index) in links" :key="index" class="link-row">
              <Link2 :size="17" aria-hidden="true" />
              <div class="link-fields">
                <label class="sr-only" :for="`link-title-${index}`">链接 {{ index + 1 }} 标题</label>
                <input :id="`link-title-${index}`" v-model="link.title" maxlength="120"
                  :disabled="!!pendingAction" placeholder="标题，可选" />
                <label class="sr-only" :for="`link-${index}`">链接 {{ index + 1 }} 地址</label>
                <input :id="`link-${index}`" v-model="link.url" type="url"
                  :disabled="!!pendingAction" placeholder="https://"
                  :aria-invalid="!!fieldErrors[`link-${index}`]"
                  :aria-describedby="fieldErrors[`link-${index}`] ? `link-error-${index}` : undefined" />
                <p v-if="fieldErrors[`link-${index}`]" :id="`link-error-${index}`" class="field-error">
                  {{ fieldErrors[`link-${index}`] }}
                </p>
                <div
                  v-if="link.fetch && link.id && link.url === link.savedUrl"
                  class="link-source-state"
                  :data-state="link.fetch.status"
                  role="status"
                >
                  <div class="link-source-heading">
                    <LoaderCircle
                      v-if="link.fetch.status === 'queued' || link.fetch.status === 'running'"
                      :size="16"
                      class="link-source-spinner"
                      aria-hidden="true"
                    />
                    <CircleCheck
                      v-else-if="link.fetch.status === 'ready'"
                      :size="16"
                      aria-hidden="true"
                    />
                    <TriangleAlert v-else :size="16" aria-hidden="true" />
                    <strong>{{ linkStatusLabel(link.fetch) }}</strong>
                    <span v-if="link.fetch.fetched_at">
                      {{ formatDateTime(link.fetch.fetched_at) }}
                    </span>
                    <button
                      v-if="editable && ['ready', 'failed'].includes(link.fetch.status)"
                      type="button"
                      class="small-icon-button link-refresh-button"
                      title="重新读取"
                      :aria-label="`重新读取链接 ${link.title || index + 1}`"
                      :disabled="!!pendingAction"
                      @click="refreshLink(link)"
                    >
                      <RefreshCw :size="15" aria-hidden="true" />
                    </button>
                  </div>
                  <p v-if="link.fetch.using_previous_snapshot" class="link-source-note">
                    本次读取未完成，仍使用上次成功摘要。
                  </p>
                  <p v-if="link.fetch.page_title" class="link-source-title">
                    {{ link.fetch.page_title }}
                  </p>
                  <p v-if="link.fetch.summary" class="link-source-summary">
                    {{ link.fetch.summary }}
                  </p>
                  <p v-if="link.fetch.error" class="link-source-error">
                    {{ link.fetch.error.message }}
                  </p>
                  <p class="link-source-meta">
                    来源 {{ link.fetch.final_host || link.fetch.source_host }}
                    <template v-if="link.fetch.content_sha256">
                      · {{ link.fetch.content_sha256.slice(0, 12) }}
                    </template>
                  </p>
                </div>
              </div>
              <a v-if="working && link.url && isHttpUrl(link.url)" :href="link.url"
                target="_blank" rel="noopener noreferrer" class="small-icon-button"
                :aria-label="`打开链接 ${link.title || index + 1}`" title="在新窗口打开">
                <ExternalLink :size="15" aria-hidden="true" />
              </a>
              <button v-if="editable" type="button" class="small-icon-button danger-icon" title="移除链接"
                :aria-label="`移除链接 ${link.title || index + 1}`"
                @click="links.splice(index, 1)">
                <Trash2 :size="15" aria-hidden="true" />
              </button>
            </div>
          </div>
          <p v-if="!links.length" class="section-empty">暂无链接。</p>
          <p v-if="fieldErrors.links" class="field-error">{{ fieldErrors.links }}</p>
        </section>
      </fieldset>

      <footer class="task-editor-footer">
        <span v-if="working">版本 {{ working.version }} · 最近更新 {{ new Date(working.updated_at).toLocaleString('zh-CN') }}</span>
        <span v-else>尚未保存</span>
        <button v-if="editable" type="submit" class="button" :disabled="!!pendingAction">
          <Save :size="17" aria-hidden="true" />{{ pendingAction === 'save' ? '正在保存…' : creating ? '创建任务' : '保存修改' }}
        </button>
      </footer>
    </form>
  </section>
</template>
