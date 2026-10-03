import { api } from './api'
import type { AIJob } from './ai-jobs'

export type PlanScope = 'day' | 'week'
export type PlanDensity = 'loose' | 'balanced' | 'tight'
export type ProposalEffectiveStatus =
  | 'pending'
  | 'applied'
  | 'rejected'
  | 'expired'
  | 'conflicted'
export type UnscheduledReason =
  | 'no_capacity'
  | 'deadline_conflict'
  | 'fixed_conflict'
  | 'missing_estimate'
  | 'outside_scope'

export interface PlanningStep {
  id: string
  title: string
  position: number
  completed: boolean
}

export interface PlanningTaskCandidate {
  candidate_key: string
  task_id: string
  task_version: number
  occurrence_key: string | null
  occurrence_version: number
  title: string
  description: string
  project_id: string | null
  priority: number
  status: 'open'
  estimated_minutes: number | null
  estimate_source: 'user' | 'ai' | null
  splittable: boolean
  minimum_block_minutes: number
  due_kind: 'none' | 'date' | 'datetime'
  due_date: string | null
  due_at: string | null
  time_zone: string | null
  step_progress: { total: number; completed: number }
  completed_step_ids: string[]
  steps: PlanningStep[]
}

export interface PlanningFixedOccurrence {
  series_id: string | null
  subscription_event_id: string | null
  occurrence_key: string
  occurrence_type: 'base' | 'addition' | 'subscription'
  kind: 'course' | 'fixed'
  source_type: 'manual' | 'import' | 'screenshot' | 'subscription'
  name: string
  busy: true
  status: 'scheduled'
  time_zone: string
  local_start_date: string
  local_start_time: string | null
  local_end_date: string
  local_end_time: string | null
  start_at: string | null
  end_at: string | null
  warning_codes: string[]
}

export interface PlanBlock {
  id?: string
  position: number
  task_id: string
  occurrence_key: string | null
  task_title_snapshot: string
  step_ids: string[]
  step_titles_snapshot: string[]
  local_date: string
  local_starts_at: string
  local_ends_at: string
  time_zone: string
  starts_at: string
  ends_at: string
  estimate_source: 'user' | 'ai'
  estimated_minutes: number
  created_at?: string
}

export interface PlanUnscheduledItem {
  id?: string
  position: number
  task_id: string
  occurrence_key: string | null
  task_title_snapshot: string
  remaining_minutes: number
  reason: UnscheduledReason
  explanation: string
  created_at?: string
}

export interface PlanVersion {
  id: string
  plan_id: string
  version: number
  source: 'ai' | 'manual'
  proposal_id: string | null
  scope: PlanScope
  target_date: string
  density: PlanDensity
  buffer_minutes: number
  availability_start: string
  availability_end: string
  summary: string
  assumptions: string[]
  input_revision: number
  blocks: PlanBlock[]
  unscheduled_items: PlanUnscheduledItem[]
  created_at: string
}

export interface Plan {
  id: string
  week_start_date: string
  week_end_date: string
  time_zone: string
  current_version_id: string
  version: number
  current_version: PlanVersion
  created_at: string
  updated_at: string
}

export interface PlanningBlockProjection extends PlanBlock {
  source: 'plan'
  plan_id: string
  plan_version_id: string
  plan_version: number
  plan_source: 'ai' | 'manual'
}

interface ContextBlock {
  id: string
  position: number
  task_id: string
  occurrence_key: string | null
  task_title: string
  step_ids: string[]
  step_titles: string[]
  local_date: string
  local_starts_at: string
  local_ends_at: string
  time_zone: string
  starts_at: string
  ends_at: string
  estimate_source: 'user' | 'ai'
  estimated_minutes: number
}

interface ContextUnscheduledItem {
  id: string
  position: number
  task_id: string
  occurrence_key: string | null
  task_title: string
  remaining_minutes: number
  reason: UnscheduledReason
  explanation: string
}

export interface PlanningCurrentPlan {
  id: string
  version_id: string
  version: number
  week_start_date: string
  time_zone: string
  source: 'ai' | 'manual'
  scope: PlanScope
  target_date: string
  density: PlanDensity
  buffer_minutes: number
  availability_start: string
  availability_end: string
  summary: string
  assumptions: string[]
  input_revision: number
  blocks: ContextBlock[]
  unscheduled_items: ContextUnscheduledItem[]
}

export interface PlanningContext {
  revision: number
  scope: PlanScope
  target_date: string
  range_start_date: string
  range_end_date: string
  week_start_date: string
  week_end_date: string
  time_zone: string
  density: PlanDensity
  buffer_minutes: number
  availability_start: string
  availability_end: string
  instruction: string
  selected_task_ids: string[]
  task_candidates: PlanningTaskCandidate[]
  fixed_occurrences: PlanningFixedOccurrence[]
  current_plan: PlanningCurrentPlan | null
  preserved_dates: string[]
  content_sha256: string
  defaults: {
    density: PlanDensity
    buffer_minutes: number
    availability_start: string
    availability_end: string
  }
}

export interface ProposalSnapshot {
  id: string
  revision: number
  scope: PlanScope
  target_date: string
  week_start_date: string
  week_end_date: string
  time_zone: string
  density: PlanDensity
  buffer_minutes: number
  availability_start: string
  availability_end: string
  current_plan_id: string | null
  current_plan_version: number | null
}

export interface PlanProposalSummary {
  id: string
  kind: 'plan'
  source_job_id: string
  conversation_id: string | null
  source_message_id: string | null
  input_snapshot_id: string
  schema_version: number
  status: 'pending' | 'applied' | 'rejected'
  effective_status: ProposalEffectiveStatus
  title: string
  summary: string
  assumptions: string[]
  warnings: string[]
  expires_at: string
  applied_at: string | null
  rejected_at: string | null
  applied_plan_id: string | null
  applied_plan_version_id: string | null
  version: number
  created_at: string
  updated_at: string
  input_snapshot: ProposalSnapshot
  current_state: {
    revision: number
    plan: { id: string; version: number } | null
  }
  conflict: {
    categories: string[]
    snapshot_revision: number
    current_revision: number
  } | null
}

export interface ProposalBlock extends Omit<PlanBlock, 'id' | 'position' | 'created_at' | 'task_title_snapshot' | 'step_titles_snapshot'> {
  candidate_key: string
}

export interface ProposalPayload {
  summary: string
  assumptions: string[]
  blocks: ProposalBlock[]
  unscheduled_items: Array<{
    candidate_key: string
    task_id: string
    occurrence_key: string | null
    remaining_minutes: number
    reason: UnscheduledReason
    explanation: string
    estimate_source: 'user' | 'ai'
    estimated_minutes: number
  }>
}

export interface ProposalChange {
  id: string
  position: number
  action: 'create' | 'update' | 'delete'
  entity_type: 'plan' | 'scheduled_block'
  target_id: string | null
  before_data: Record<string, unknown> | null
  after_data: Record<string, unknown> | null
  created_at: string
}

export interface PlanProposal extends PlanProposalSummary {
  payload: ProposalPayload
  changes: ProposalChange[]
  job: AIJob
}

export interface PlanningFields {
  scope: PlanScope
  target_date: string
  density: PlanDensity
  buffer_minutes: number
  availability_start: string
  availability_end: string
  task_ids: string[]
  instruction: string
}

export interface ManualPlanVersionInput {
  operation_id: string
  week_start_date: string
  expected_plan_version: number
  density: PlanDensity
  buffer_minutes: number
  availability_start: string
  availability_end: string
  blocks: Array<{
    task_id: string
    occurrence_key: string | null
    local_date: string
    starts_at: string
    ends_at: string
    step_ids: string[]
  }>
  unscheduled_items: Array<{
    task_id: string
    occurrence_key: string | null
    remaining_minutes: number
    reason: UnscheduledReason
    explanation: string
  }>
}

export function contextBlock(block: ContextBlock): PlanBlock {
  return {
    ...block,
    task_title_snapshot: block.task_title,
    step_titles_snapshot: block.step_titles,
  }
}

export function contextUnscheduledItem(
  item: ContextUnscheduledItem,
): PlanUnscheduledItem {
  return {
    ...item,
    task_title_snapshot: item.task_title,
  }
}

export async function getPlanningContext(fields: Omit<PlanningFields, 'instruction'>) {
  const params = new URLSearchParams({
    scope: fields.scope,
    date: fields.target_date,
    density: fields.density,
    buffer: String(fields.buffer_minutes),
    availability_start: fields.availability_start,
    availability_end: fields.availability_end,
  })
  fields.task_ids.forEach(id => params.append('task_id', id))
  return (await api<{ context: PlanningContext }>(
    `/planning/context?${params}`,
  )).context
}

export async function createPlanningGeneration(
  fields: PlanningFields,
  operationId: string,
) {
  return api<{ operation_id: string; job: AIJob }>(
    '/planning/generations',
    { operation_id: operationId, ...fields },
  )
}

export async function listPlanningJobs() {
  return (await api<{
    items: AIJob[]
    next_cursor: { before_time: string; before_id: string } | null
  }>('/ai/jobs?capability=planning&limit=20')).items
}

export async function listPlanProposals() {
  return (await api<{
    items: PlanProposalSummary[]
    next_cursor: { before_time: string; before_id: string } | null
  }>('/proposals?limit=30')).items
}

export async function getPlanProposal(id: string) {
  return (await api<{ proposal: PlanProposal }>(`/proposals/${id}`)).proposal
}

export async function applyPlanProposal(
  proposal: PlanProposal,
  operationId: string,
) {
  return api<{
    operation_id: string
    proposal: PlanProposalSummary
    application: {
      plan_id: string
      plan_version_id: string
      plan_version: number
      version: PlanVersion
    }
  }>(`/proposals/${proposal.id}/apply`, {
    operation_id: operationId,
    expected_proposal_version: proposal.version,
  })
}

export async function rejectPlanProposal(
  proposal: PlanProposal,
  operationId: string,
) {
  return api<{
    operation_id: string
    proposal: PlanProposalSummary
  }>(`/proposals/${proposal.id}/reject`, {
    operation_id: operationId,
    expected_proposal_version: proposal.version,
  })
}

export async function listPlans(start: string, end: string) {
  const params = new URLSearchParams({ start, end })
  return (await api<{ start: string; end: string; items: Plan[] }>(
    `/plans?${params}`,
  )).items
}

export async function getPlan(id: string, version?: number) {
  const suffix = version ? `?version=${version}` : ''
  return (await api<{ plan: Plan }>(`/plans/${id}${suffix}`)).plan
}

export async function getPlanHistory(id: string) {
  return (await api<{ items: PlanVersion[] }>(`/plans/${id}/history`)).items
}

export async function saveManualPlanVersion(input: ManualPlanVersionInput) {
  return api<{ operation_id: string; plan: Plan }>('/plans/versions', input)
}

export async function listPlanningBlocks(start: string, end: string) {
  const params = new URLSearchParams({ start, end })
  return api<{
    start: string
    end: string
    time_zone: string
    items: PlanningBlockProjection[]
  }>(`/planning/blocks?${params}`)
}
