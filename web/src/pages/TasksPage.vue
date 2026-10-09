<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { CirclePause } from 'lucide-vue-next'
import StatusMessage from '../components/StatusMessage.vue'
import ProjectDialog from '../components/ProjectDialog.vue'
import TaskEditor from '../components/TaskEditor.vue'
import TaskListPanel from '../components/TaskListPanel.vue'
import TaskWorkspaceNavigation from '../components/TaskWorkspaceNavigation.vue'
import AppShell from '../shell/AppShell.vue'
import { useAppShell } from '../shell/useAppShell'
import { failure, type Failure } from '../lib/api'
import {
  authError,
  identity,
  refreshSession,
} from '../lib/auth'
import {
  bulkUpdateTasks,
  getTask,
  listLabels,
  listProjects,
  listTasks,
  type BulkAction,
  type Cursor,
  type Project,
  type ProjectKind,
  type TaskDetail,
  type TaskFilters,
  type TaskLifecycle,
  type TaskSummary,
} from '../lib/tasks'
import { useSyncRefresh } from '../lib/use-sync-refresh'
import '../task-workspace.css'

const router = useRouter()
const projects = ref<Project[]>([])
const labels = ref<Awaited<ReturnType<typeof listLabels>>>([])
const tasks = ref<TaskSummary[]>([])
const cursor = ref<Cursor | null>(null)
const filters = ref<TaskFilters>({
  lifecycle: 'active',
  search: '',
  status: 'open',
  project_id: null,
  inbox: false,
  priority: null,
  label: '',
})
const selectedIds = ref<string[]>([])
const selectedTask = ref<TaskDetail | null>(null)
const creating = ref(false)
const editorDirty = ref(false)
const loadingTasks = ref(false)
const loadedTasks = ref(false)
const loadingDetail = ref(false)
const { pendingLogout, logoutError, logout: baseLogout } = useAppShell()
const bulkPending = ref(false)
const pageError = ref<Failure | null>(null)
const pageSuccess = ref('')
const editorSuccess = ref('')
const projectDialogOpen = ref(false)
const projectDialogProject = ref<Project | null>(null)
const projectDialogKind = ref<ProjectKind>('project')
let listRevision = 0
let bulkOperation: { id: string; fingerprint: string } | null = null

const detailOpen = computed(() => creating.value || !!selectedTask.value || loadingDetail.value)

watch(authError, value => {
  if (value?.code === 'unauthenticated') void router.replace('/login')
})

onMounted(async () => {
  document.title = '任务 · iTime'
  await refreshSession()
  if (!identity.value) {
    await router.replace('/login')
    return
  }
  await Promise.all([refreshProjectsAndLabels(), loadTasks()])
})

useSyncRefresh(
  ['project', 'task', 'step', 'label', 'task_occurrence', 'task_link'],
  async (events) => {
    const linkOnly = (
      events.length > 0
      && events.every(event => event.entity_type === 'task_link')
    )
    const selectedLinkChanged = (
      !!selectedTask.value
      && events.some(event => (
        event.entity_type === 'task_link'
        && selectedTask.value?.links.some(link => link.id === event.entity_id)
      ))
    )
    await Promise.all([
      refreshProjectsAndLabels(),
      loadTasks(false, true),
    ])
    if (
      selectedTask.value
      && (!editorDirty.value || (linkOnly && selectedLinkChanged))
    ) {
      try {
        selectedTask.value = await getTask(selectedTask.value.id)
      } catch {
        selectedTask.value = null
      }
    } else if (selectedTask.value && editorDirty.value && !linkOnly) {
      pageSuccess.value = ''
      editorSuccess.value = '其他设备有更新；当前填写已保留，请保存时比较最新版。'
    }
  },
)

function canLeaveEditor() {
  return !editorDirty.value || window.confirm('当前修改尚未保存，确定离开吗？')
}

async function refreshProjectsAndLabels() {
  try {
    const [projectPage, labelItems] = await Promise.all([listProjects(), listLabels()])
    projects.value = projectPage.items
    labels.value = labelItems
  } catch (reason) {
    pageError.value = failure(reason)
  }
}

async function loadTasks(more = false, keepMessage = false) {
  if (loadingTasks.value && more) return
  const revision = ++listRevision
  loadingTasks.value = true
  if (!keepMessage) {
    pageError.value = null
    pageSuccess.value = ''
  }
  try {
    const page = await listTasks(filters.value, more ? cursor.value : null)
    if (revision !== listRevision) return
    tasks.value = more ? [...tasks.value, ...page.items] : page.items
    cursor.value = page.next_cursor
    loadedTasks.value = true
    selectedIds.value = selectedIds.value.filter(id => tasks.value.some(task => task.id === id))
  } catch (reason) {
    if (revision !== listRevision) return
    pageError.value = failure(reason)
  } finally {
    if (revision === listRevision) loadingTasks.value = false
  }
}

async function setLocation(projectId: string | null, inbox: boolean) {
  if (!canLeaveEditor()) return
  filters.value = { ...filters.value, project_id: projectId, inbox }
  selectedTask.value = null
  creating.value = false
  editorDirty.value = false
  editorSuccess.value = ''
  selectedIds.value = []
  await loadTasks()
}

async function setLifecycle(lifecycle: TaskLifecycle) {
  if (filters.value.lifecycle === lifecycle || !canLeaveEditor()) return
  filters.value = {
    ...filters.value,
    lifecycle,
    status: lifecycle === 'active' ? 'open' : 'all',
  }
  selectedTask.value = null
  creating.value = false
  editorDirty.value = false
  editorSuccess.value = ''
  selectedIds.value = []
  await loadTasks()
}

function beginProject(kind: ProjectKind) {
  projectDialogProject.value = null
  projectDialogKind.value = kind
  projectDialogOpen.value = true
}

function editProject(project: Project) {
  projectDialogProject.value = project
  projectDialogKind.value = project.kind
  projectDialogOpen.value = true
}

async function projectSaved(project: Project) {
  const created = !projectDialogProject.value
  projectDialogOpen.value = false
  projectDialogProject.value = null
  if (created) {
    filters.value = { ...filters.value, project_id: project.id, inbox: false }
    selectedTask.value = null
    creating.value = false
  }
  pageSuccess.value = `${project.kind === 'goal' ? '目标' : '项目'}已${created ? '创建' : '保存'}。`
  await Promise.all([refreshProjectsAndLabels(), loadTasks(false, true)])
}

function startTask() {
  if (!canLeaveEditor()) return
  selectedTask.value = null
  creating.value = true
  editorDirty.value = false
  pageError.value = null
  pageSuccess.value = ''
  editorSuccess.value = ''
}

async function openTask(summary: TaskSummary) {
  if (selectedTask.value?.id === summary.id && !creating.value) return
  if (!canLeaveEditor()) return
  creating.value = false
  selectedTask.value = null
  editorDirty.value = false
  editorSuccess.value = ''
  loadingDetail.value = true
  pageError.value = null
  try {
    selectedTask.value = await getTask(summary.id)
  } catch (reason) {
    pageError.value = failure(reason)
  } finally {
    loadingDetail.value = false
  }
}

function closeEditor() {
  if (!canLeaveEditor()) return
  creating.value = false
  selectedTask.value = null
  editorDirty.value = false
  editorSuccess.value = ''
}

async function taskCreated(task: TaskDetail) {
  creating.value = false
  selectedTask.value = task
  editorDirty.value = false
  pageSuccess.value = ''
  editorSuccess.value = '任务已创建并保存到服务端。'
  await Promise.all([refreshProjectsAndLabels(), loadTasks(false, true)])
}

function taskUpdated(task: TaskDetail) {
  selectedTask.value = task
  const others = tasks.value.filter(item => item.id !== task.id)
  tasks.value = task.lifecycle === filters.value.lifecycle
    ? [task as TaskSummary, ...others].sort((left, right) =>
        right.updated_at.localeCompare(left.updated_at))
    : others
  selectedIds.value = selectedIds.value.filter(id => id !== task.id)
  void refreshProjectsAndLabels()
}

async function taskLifecycleChanged(message: string) {
  selectedTask.value = null
  creating.value = false
  editorDirty.value = false
  editorSuccess.value = ''
  selectedIds.value = []
  pageSuccess.value = message
  await Promise.all([refreshProjectsAndLabels(), loadTasks(false, true)])
}

function patchFilters(changes: Partial<TaskFilters>) {
  filters.value = { ...filters.value, ...changes }
  selectedIds.value = []
  void loadTasks()
}

function search(value: string) {
  filters.value = { ...filters.value, search: value }
  selectedIds.value = []
  void loadTasks()
}

function clearFilters() {
  filters.value = {
    ...filters.value,
    search: '',
    status: filters.value.lifecycle === 'active' ? 'open' : 'all',
    priority: null,
    label: '',
  }
  selectedIds.value = []
  void loadTasks()
}

function setSelection(id: string, selected: boolean) {
  selectedIds.value = selected
    ? [...new Set([...selectedIds.value, id])]
    : selectedIds.value.filter(value => value !== id)
}

function selectVisible(selected: boolean) {
  selectedIds.value = selected
    ? [...new Set([...selectedIds.value, ...tasks.value.map(task => task.id)])]
    : selectedIds.value.filter(id => !tasks.value.some(task => task.id === id))
}

async function applyBulk(input: {
  action: BulkAction
  priority?: number
  project_id?: string | null
  labels?: string[]
}) {
  if (bulkPending.value || !selectedIds.value.length) return
  const items = selectedIds.value.map(id => {
    const task = tasks.value.find(candidate => candidate.id === id)
    return task ? { id, expected_version: task.version } : null
  }).filter((item): item is { id: string; expected_version: number } => !!item)
  const body = { ...input, items }
  const fingerprint = JSON.stringify(body)
  if (!bulkOperation || bulkOperation.fingerprint !== fingerprint) {
    bulkOperation = { id: crypto.randomUUID(), fingerprint }
  }
  bulkPending.value = true
  pageError.value = null
  pageSuccess.value = ''
  editorSuccess.value = ''
  try {
    await bulkUpdateTasks(bulkOperation.id, body)
    bulkOperation = null
    selectedIds.value = []
    pageSuccess.value = `已完成 ${items.length} 项批量操作。`
    if (selectedTask.value && items.some(item => item.id === selectedTask.value?.id)) {
      selectedTask.value = await getTask(selectedTask.value.id)
    }
    await Promise.all([refreshProjectsAndLabels(), loadTasks(false, true)])
  } catch (reason) {
    pageError.value = failure(reason)
    await nextTick()
    document.querySelector<HTMLElement>('.task-list-column .error-status')?.focus()
  } finally {
    bulkPending.value = false
  }
}

async function recoverList() {
  bulkOperation = null
  selectedIds.value = []
  await Promise.all([refreshProjectsAndLabels(), loadTasks()])
}

async function logout() {
  if (!canLeaveEditor()) return
  await baseLogout()
}
</script>

<template>
  <AppShell
    current="tasks"
    has-secondary
    secondary-label="任务视图"
    title="任务"
    :pending-logout="pendingLogout"
    @logout="logout"
  >
    <template #secondary>
      <TaskWorkspaceNavigation
        :projects="projects"
        :selected-project-id="filters.project_id"
        :inbox="filters.inbox"
        @location="setLocation"
        @create-project="beginProject"
        @edit-project="editProject"
      />
    </template>
    <template #drawer-secondary="{ close }">
      <TaskWorkspaceNavigation
        :projects="projects"
        :selected-project-id="filters.project_id"
        :inbox="filters.inbox"
        @location="(id, inbox) => { setLocation(id, inbox); close() }"
        @create-project="(kind) => { beginProject(kind); close() }"
        @edit-project="(project) => { editProject(project); close() }"
      />
    </template>

    <div class="task-main" :class="{ 'detail-open': detailOpen }">
      <div class="task-list-column">
        <div
          v-if="pageError || pageSuccess || logoutError || identity?.user.ai_access === 'frozen'"
          class="workspace-status"
        >
          <StatusMessage :error="logoutError" />
          <div v-if="identity?.user.ai_access === 'frozen'" class="status restriction-status" role="status">
            <CirclePause :size="20" aria-hidden="true" />
            <p>此账号的 AI 功能已暂停，手动查看和编辑仍可使用。</p>
          </div>
          <StatusMessage :error="pageError" :success="pageSuccess"
            :retry-label="pageError?.retryable || pageError?.code === 'task_version_conflict' ? '刷新列表' : undefined"
            @retry="recoverList" />
        </div>
        <TaskListPanel
          :tasks="tasks"
          :projects="projects"
          :labels="labels"
          :filters="filters"
          :selected-ids="selectedIds"
          :active-task-id="selectedTask?.id ?? null"
          :loading="loadingTasks"
          :loaded="loadedTasks"
          :has-more="!!cursor"
          :bulk-pending="bulkPending"
          @new-task="startTask"
          @lifecycle="setLifecycle"
          @open-task="openTask"
          @search="search"
          @filters="patchFilters"
          @clear-filters="clearFilters"
          @load-more="loadTasks(true)"
          @selection="setSelection"
          @select-visible="selectVisible"
          @bulk="applyBulk"
        />
      </div>

      <div v-if="loadingDetail" class="task-detail-loading" role="status">正在读取任务详情…</div>
      <TaskEditor
        v-else
        :key="creating ? 'new' : selectedTask?.id ?? 'empty'"
        :task="selectedTask"
        :creating="creating"
        :projects="projects"
        :initial-project-id="filters.project_id"
        :external-success="editorSuccess"
        @close="closeEditor"
        @created="taskCreated"
        @updated="taskUpdated"
        @lifecycle="taskLifecycleChanged"
        @dirty="editorDirty = $event"
      />
    </div>

    <ProjectDialog
      :open="projectDialogOpen"
      :project="projectDialogProject"
      :initial-kind="projectDialogKind"
      @close="projectDialogOpen = false"
      @saved="projectSaved"
    />
  </AppShell>
</template>
