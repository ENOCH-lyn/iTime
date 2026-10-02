import { api } from './api'
import type { TaskSchedule } from './task-schedules'

export type ProjectKind = 'project' | 'goal'
export type TaskStatus = 'open' | 'completed'
export type BulkAction =
  | 'complete'
  | 'reopen'
  | 'set_priority'
  | 'set_project'
  | 'add_labels'
  | 'remove_labels'

export interface Cursor {
  before_time: string
  before_id: string
}

export interface Page<T> {
  items: T[]
  next_cursor: Cursor | null
}

export interface Project {
  id: string
  kind: ProjectKind
  name: string
  description: string
  version: number
  task_count?: number
  open_task_count?: number
  created_at: string
  updated_at: string
}

export interface Label {
  id: string
  name: string
  version: number
  task_count?: number
}

export interface TaskProject {
  id: string
  kind: ProjectKind
  name: string
}

export interface TaskStep {
  id: string
  title: string
  position: number
  completed: boolean
  completed_at: string | null
  created_at: string
  updated_at: string
}

export interface TaskLink {
  id: string
  title: string
  url: string
  position: number
}

export interface TaskSummary {
  id: string
  title: string
  description: string
  project: TaskProject | null
  estimated_minutes: number | null
  estimate_source: 'user' | 'ai' | null
  priority: number
  status: TaskStatus
  completed_at: string | null
  version: number
  labels: Label[]
  step_progress: { total: number; completed: number }
  link_count: number
  schedule: TaskSchedule
  created_at: string
  updated_at: string
}

export interface TaskDetail extends TaskSummary {
  steps: TaskStep[]
  links: TaskLink[]
}

export interface TaskDraft {
  title: string
  description: string
  project_id: string | null
  estimated_minutes: number | null
  priority: number
  labels: string[]
  links: { title: string; url: string }[]
}

export interface TaskFilters {
  search: string
  status: 'all' | TaskStatus
  project_id: string | null
  inbox: boolean
  priority: number | null
  label: string
}

export interface BulkInput {
  action: BulkAction
  items: { id: string; expected_version: number }[]
  priority?: number
  project_id?: string | null
  labels?: string[]
}

function mutationBody<T extends object>(operationId: string, fields: T) {
  return { operation_id: operationId, ...fields }
}

export async function listProjects(kind: 'all' | ProjectKind = 'all') {
  const params = new URLSearchParams({ kind, limit: '100' })
  return api<Page<Project>>(`/projects?${params}`)
}

export async function getProject(id: string) {
  return (await api<{ project: Project }>(`/projects/${id}`)).project
}

export async function createProject(
  operationId: string,
  fields: Pick<Project, 'kind' | 'name' | 'description'>,
) {
  return (await api<{ operation_id: string; project: Project }>(
    '/projects',
    mutationBody(operationId, fields),
  )).project
}

export async function updateProject(
  id: string,
  operationId: string,
  expectedVersion: number,
  fields: Pick<Project, 'kind' | 'name' | 'description'>,
) {
  return (await api<{ operation_id: string; project: Project }>(
    `/projects/${id}`,
    mutationBody(operationId, { ...fields, expected_version: expectedVersion }),
    'PUT',
  )).project
}

export async function listLabels() {
  return (await api<{ items: Label[] }>('/labels?limit=100')).items
}

export async function listTasks(filters: TaskFilters, cursor?: Cursor | null) {
  const params = new URLSearchParams({
    status: filters.status,
    location: filters.inbox ? 'inbox' : 'all',
    limit: '50',
  })
  if (filters.search.trim()) params.set('search', filters.search.trim())
  if (filters.project_id) params.set('project_id', filters.project_id)
  if (filters.priority !== null) params.set('priority', String(filters.priority))
  if (filters.label) params.set('label', filters.label)
  if (cursor) {
    params.set('before_time', cursor.before_time)
    params.set('before_id', cursor.before_id)
  }
  return api<Page<TaskSummary>>(`/tasks?${params}`)
}

export async function getTask(id: string) {
  return (await api<{ task: TaskDetail }>(`/tasks/${id}`)).task
}

export async function createTask(
  operationId: string,
  fields: TaskDraft & { steps: { title: string }[] },
) {
  return (await api<{ operation_id: string; task: TaskDetail }>(
    '/tasks',
    mutationBody(operationId, fields),
  )).task
}

export async function updateTask(
  id: string,
  operationId: string,
  expectedVersion: number,
  fields: TaskDraft,
) {
  return (await api<{ operation_id: string; task: TaskDetail }>(
    `/tasks/${id}`,
    mutationBody(operationId, { ...fields, expected_version: expectedVersion }),
    'PUT',
  )).task
}

export async function setTaskCompletion(
  task: Pick<TaskSummary, 'id' | 'version'>,
  operationId: string,
  completed: boolean,
) {
  return (await api<{ operation_id: string; task: TaskDetail }>(
    `/tasks/${task.id}/completion`,
    mutationBody(operationId, { expected_version: task.version, completed }),
    'PUT',
  )).task
}

export async function createTaskStep(
  task: Pick<TaskSummary, 'id' | 'version'>,
  operationId: string,
  title: string,
) {
  return (await api<{ operation_id: string; task: TaskDetail }>(
    `/tasks/${task.id}/steps`,
    mutationBody(operationId, { expected_task_version: task.version, title }),
  )).task
}

export async function updateTaskStep(
  task: Pick<TaskSummary, 'id' | 'version'>,
  stepId: string,
  operationId: string,
  title: string,
  completed: boolean,
) {
  return (await api<{ operation_id: string; task: TaskDetail }>(
    `/tasks/${task.id}/steps/${stepId}`,
    mutationBody(operationId, {
      expected_task_version: task.version,
      title,
      completed,
    }),
    'PUT',
  )).task
}

export async function deleteTaskStep(
  task: Pick<TaskSummary, 'id' | 'version'>,
  stepId: string,
  operationId: string,
) {
  return (await api<{ operation_id: string; task: TaskDetail }>(
    `/tasks/${task.id}/steps/${stepId}`,
    mutationBody(operationId, { expected_task_version: task.version }),
    'DELETE',
  )).task
}

export async function reorderTaskSteps(
  task: Pick<TaskSummary, 'id' | 'version'>,
  operationId: string,
  stepIds: string[],
) {
  return (await api<{ operation_id: string; task: TaskDetail }>(
    `/tasks/${task.id}/steps`,
    mutationBody(operationId, {
      expected_task_version: task.version,
      step_ids: stepIds,
    }),
    'PUT',
  )).task
}

export async function bulkUpdateTasks(operationId: string, input: BulkInput) {
  return (await api<{ operation_id: string; items: TaskSummary[] }>(
    '/tasks/actions/bulk',
    mutationBody(operationId, input),
  )).items
}
