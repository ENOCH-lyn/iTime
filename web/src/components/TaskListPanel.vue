<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  Check,
  CheckCircle2,
  ChevronDown,
  Circle,
  Clock3,
  Link2,
  Plus,
  Search,
  SlidersHorizontal,
  Tags,
  X,
} from 'lucide-vue-next'
import type {
  BulkAction,
  Label,
  Project,
  TaskFilters,
  TaskSummary,
} from '../lib/tasks'

const props = defineProps<{
  tasks: TaskSummary[]
  projects: Project[]
  labels: Label[]
  filters: TaskFilters
  selectedIds: string[]
  activeTaskId: string | null
  loading: boolean
  loaded: boolean
  hasMore: boolean
  bulkPending: boolean
}>()

const emit = defineEmits<{
  newTask: []
  openTask: [task: TaskSummary]
  search: [value: string]
  filters: [changes: Partial<TaskFilters>]
  clearFilters: []
  loadMore: []
  selection: [id: string, selected: boolean]
  selectVisible: [selected: boolean]
  bulk: [input: {
    action: BulkAction
    priority?: number
    project_id?: string | null
    labels?: string[]
  }]
}>()

const bulkAction = ref<BulkAction>('complete')
const bulkPriority = ref(2)
const bulkProject = ref('')
const bulkLabels = ref('')
const filtersExpanded = ref(false)
const allVisibleSelected = computed(() =>
  props.tasks.length > 0 && props.tasks.every(task => props.selectedIds.includes(task.id)))
const hasFilters = computed(() =>
  props.filters.status !== 'open'
  || props.filters.priority !== null
  || !!props.filters.label
  || !!props.filters.search.trim())

const priorityNames = ['未设置', '低', '中', '高']

function submitSearch(event: Event) {
  const form = event.currentTarget as HTMLFormElement
  const data = new FormData(form)
  emit('search', String(data.get('task-search') ?? ''))
}

function applyBulk() {
  const input: {
    action: BulkAction
    priority?: number
    project_id?: string | null
    labels?: string[]
  } = { action: bulkAction.value }
  if (bulkAction.value === 'set_priority') input.priority = bulkPriority.value
  if (bulkAction.value === 'set_project') input.project_id = bulkProject.value || null
  if (['add_labels', 'remove_labels'].includes(bulkAction.value)) {
    input.labels = bulkLabels.value
      .split(/[,，\n]/)
      .map(value => value.trim())
      .filter(Boolean)
  }
  emit('bulk', input)
}

function projectName() {
  if (props.filters.inbox) return '收集箱'
  return props.projects.find(project => project.id === props.filters.project_id)?.name ?? '所有任务'
}
</script>

<template>
  <section class="task-list-panel" aria-label="任务列表">
    <header class="task-list-heading">
      <div>
        <p>{{ projectName() }}</p>
        <h1 tabindex="-1">任务</h1>
      </div>
      <button type="button" class="button task-new-button" @click="emit('newTask')">
        <Plus :size="18" aria-hidden="true" />新建任务
      </button>
    </header>

    <form class="task-search" role="search" @submit.prevent="submitSearch">
      <label class="sr-only" for="task-search">搜索任务</label>
      <Search :size="17" aria-hidden="true" />
      <input id="task-search" name="task-search" type="search" :value="filters.search"
        maxlength="100" placeholder="搜索名称或正文" />
      <button type="submit" class="small-icon-button" title="搜索" aria-label="搜索">
        <Check :size="17" aria-hidden="true" />
      </button>
      <button type="button" class="small-icon-button filter-toggle" title="筛选"
        :aria-expanded="filtersExpanded" aria-controls="task-filters" aria-label="筛选任务"
        @click="filtersExpanded = !filtersExpanded">
        <SlidersHorizontal :size="17" aria-hidden="true" />
      </button>
    </form>

    <div id="task-filters" class="task-filters" :class="{ expanded: filtersExpanded }">
      <label>
        <span>状态</span>
        <select :value="filters.status" @change="emit('filters', { status: ($event.target as HTMLSelectElement).value as TaskFilters['status'] })">
          <option value="open">未完成</option>
          <option value="completed">已完成</option>
          <option value="all">全部</option>
        </select>
      </label>
      <label>
        <span>优先级</span>
        <select :value="filters.priority ?? ''" @change="emit('filters', { priority: ($event.target as HTMLSelectElement).value === '' ? null : Number(($event.target as HTMLSelectElement).value) })">
          <option value="">全部</option>
          <option value="3">高</option>
          <option value="2">中</option>
          <option value="1">低</option>
          <option value="0">未设置</option>
        </select>
      </label>
      <label>
        <span>标签</span>
        <select :value="filters.label" @change="emit('filters', { label: ($event.target as HTMLSelectElement).value })">
          <option value="">全部</option>
          <option v-for="label in labels" :key="label.id" :value="label.name">{{ label.name }}</option>
        </select>
      </label>
      <button v-if="hasFilters" type="button" class="text-button clear-filters" @click="emit('clearFilters')">
        <X :size="15" aria-hidden="true" />清除筛选
      </button>
    </div>

    <div v-if="tasks.length" class="list-selection">
      <label class="checkbox-label">
        <input type="checkbox" :checked="allVisibleSelected"
          @change="emit('selectVisible', ($event.target as HTMLInputElement).checked)" />
        选择当前列表
      </label>
      <span>{{ tasks.length }} 项</span>
    </div>

    <div v-if="selectedIds.length" class="bulk-toolbar" aria-label="批量操作">
      <strong>已选 {{ selectedIds.length }} 项</strong>
      <select v-model="bulkAction" aria-label="批量操作类型" :disabled="bulkPending">
        <option value="complete">标为完成</option>
        <option value="reopen">重新打开</option>
        <option value="set_priority">设置优先级</option>
        <option value="set_project">移动到项目</option>
        <option value="add_labels">添加标签</option>
        <option value="remove_labels">移除标签</option>
      </select>
      <select v-if="bulkAction === 'set_priority'" v-model="bulkPriority"
        aria-label="批量优先级" :disabled="bulkPending">
        <option :value="3">高</option>
        <option :value="2">中</option>
        <option :value="1">低</option>
        <option :value="0">未设置</option>
      </select>
      <select v-if="bulkAction === 'set_project'" v-model="bulkProject"
        aria-label="批量项目" :disabled="bulkPending">
        <option value="">收集箱</option>
        <option v-for="project in projects" :key="project.id" :value="project.id">{{ project.name }}</option>
      </select>
      <label v-if="bulkAction === 'add_labels' || bulkAction === 'remove_labels'" class="bulk-label-input">
        <span class="sr-only">批量标签，使用逗号分隔</span>
        <Tags :size="16" aria-hidden="true" />
        <input v-model="bulkLabels" type="text" maxlength="420" placeholder="标签，逗号分隔"
          :disabled="bulkPending" />
      </label>
      <button type="button" class="button compact-button" :disabled="bulkPending
        || ((bulkAction === 'add_labels' || bulkAction === 'remove_labels') && !bulkLabels.trim())"
        @click="applyBulk">
        {{ bulkPending ? '正在处理…' : '应用' }}
      </button>
    </div>

    <ul class="task-list" aria-live="polite">
      <li v-for="task in tasks" :key="task.id" :class="{ active: activeTaskId === task.id }">
        <label class="task-select">
          <input type="checkbox" :checked="selectedIds.includes(task.id)"
            :aria-label="`选择任务 ${task.title}`"
            @change="emit('selection', task.id, ($event.target as HTMLInputElement).checked)" />
        </label>
        <button type="button" class="task-row" @click="emit('openTask', task)">
          <span class="task-row-main">
            <span class="task-state-icon" :data-state="task.status">
              <CheckCircle2 v-if="task.status === 'completed'" :size="19" aria-hidden="true" />
              <Circle v-else :size="19" aria-hidden="true" />
            </span>
            <span class="task-copy">
              <strong>{{ task.title }}</strong>
              <span v-if="task.description">{{ task.description }}</span>
            </span>
          </span>
          <span class="task-meta">
            <span v-if="task.priority" class="priority-mark" :data-priority="task.priority">
              {{ priorityNames[task.priority] }}
            </span>
            <span v-if="task.estimated_minutes"><Clock3 :size="14" aria-hidden="true" />{{ task.estimated_minutes }} 分钟</span>
            <span v-if="task.step_progress.total">{{ task.step_progress.completed }}/{{ task.step_progress.total }} 步</span>
            <span v-if="task.link_count"><Link2 :size="14" aria-hidden="true" />{{ task.link_count }}</span>
          </span>
          <span v-if="task.labels.length" class="task-labels">
            <span v-for="label in task.labels" :key="label.id">{{ label.name }}</span>
          </span>
        </button>
      </li>
    </ul>

    <p v-if="loading" role="status" class="task-list-state">正在读取任务…</p>
    <div v-else-if="loaded && !tasks.length" class="task-list-empty">
      <CheckCircle2 :size="28" aria-hidden="true" />
      <strong>{{ hasFilters ? '没有符合条件的任务' : '这里还没有任务' }}</strong>
      <p>{{ hasFilters ? '可以调整或清除筛选条件。' : '先记下一件要完成的事。' }}</p>
    </div>
    <button v-if="hasMore" type="button" class="button secondary load-more"
      :disabled="loading" @click="emit('loadMore')">
      <ChevronDown :size="17" aria-hidden="true" />加载更多
    </button>
  </section>
</template>
