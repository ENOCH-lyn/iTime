import { api } from './api'
import type { AIJob } from './ai-jobs'

export interface ReviewSession {
  id: string
  activity_date: string
  time_zone: string
  supplemental_facts: string
  input_revision: number
  question_count: number
  version: number
  created_at: string
  updated_at: string
}

export interface ReviewSummary extends ReviewSession {
  report_count: number
  latest_report_version: number | null
}

export interface ReviewFactGroup<T> {
  items: T[]
  truncated: boolean
}

export interface PlannedFact {
  source_key: string
  plan_version: number
  task_id: string
  occurrence_key: string | null
  title: string
  local_date: string
  starts_at: string
  ends_at: string
  time_zone: string
  step_titles: string[]
  estimated_minutes: number
}

export interface CompletionFact {
  source_key: string
  task_id: string
  occurrence_key: string | null
  title: string
  completed_at: string
}

export interface FocusFact {
  source_key: string
  title: string
  task_id: string | null
  task_title: string | null
  status: 'running' | 'paused' | 'finished' | 'abandoned'
  started_at: string
  ended_at: string | null
  activity_seconds: number
  total_seconds: number | null
  revised: boolean
}

export interface DifficultyFact {
  source_key: string
  task_id: string | null
  task_title: string | null
  content: string
  category: string | null
  emotion: string | null
  occurred_at: string
}

export interface ReviewFacts {
  activity_date: string
  time_zone: string
  activity_window: { start: string; end: string }
  planned_blocks: ReviewFactGroup<PlannedFact>
  task_completions: ReviewFactGroup<CompletionFact>
  focus: ReviewFactGroup<FocusFact> & { total_seconds: number }
  difficulties: ReviewFactGroup<DifficultyFact>
}

export interface ReviewMessage {
  id: string
  sequence: number
  role: 'user' | 'assistant'
  kind: 'answer' | 'question'
  content: string
  source_job_id: string | null
  created_at: string
}

export interface ReviewReportSummary {
  id: string
  version: number
  summary: string
  input_snapshot_id: string
  created_at: string
}

export type ReportFreshness =
  | 'none'
  | 'current'
  | 'stale_user_input'
  | 'stale_business_facts'
  | 'stale_both'

export interface ReviewDetail {
  review: ReviewSession
  facts: ReviewFacts
  facts_sha256: string
  messages: ReviewMessage[]
  reports: ReviewReportSummary[]
  active_job: AIJob | null
  report_freshness: ReportFreshness
  server_now: string
}

export interface ReportFact {
  source_key: string
  text: string
}

export interface ReportInsight {
  text: string
  basis: 'ai_inference'
}

export interface ReviewReport extends ReviewReportSummary {
  source_job_id: string
  facts: ReportFact[]
  user_statements: string[]
  insights: ReportInsight[]
  next_actions: string[]
  uncertainties: string[]
}

export interface ReviewAnnotation {
  id: string
  content: string
  deleted_at: string | null
  version: number
  created_at: string
  updated_at: string
}

export interface MemoryProposal {
  id: string
  report_version_id?: string
  position: number
  kind: 'preference' | 'constraint' | 'context' | 'goal'
  title: string
  content: string
  rationale: string
  status: 'pending' | 'confirmed' | 'rejected'
  confirmed_at: string | null
  rejected_at: string | null
  version: number
  created_at: string
  updated_at: string
}

export interface ReviewReportDetail {
  report: ReviewReport
  annotations: ReviewAnnotation[]
  memory_proposals: MemoryProposal[]
  snapshot: {
    activity_date: string
    time_zone: string
    session_input_revision: number
    facts_sha256: string
    created_at: string
  } | null
}

export async function listReviews() {
  return api<{
    items: ReviewSummary[]
    next_cursor: { before_date: string; before_id: string } | null
  }>('/reviews?limit=50')
}

export async function createReview(activityDate: string, operationId: string) {
  return api<{
    operation_id: string
    created: boolean
    review: ReviewSession
  }>('/reviews', {
    operation_id: operationId,
    activity_date: activityDate,
  })
}

export async function getReview(id: string) {
  return api<ReviewDetail>(`/reviews/${id}`)
}

export async function saveReviewFacts(
  review: ReviewSession,
  supplementalFacts: string,
  operationId: string,
) {
  return api<{ operation_id: string; review: ReviewSession }>(
    `/reviews/${review.id}/facts`,
    {
      operation_id: operationId,
      expected_version: review.version,
      supplemental_facts: supplementalFacts,
    },
    'PUT',
  )
}

export async function requestReviewQuestion(
  review: ReviewSession,
  answer: string | null,
  operationId: string,
) {
  return api<{
    operation_id: string
    review: ReviewSession
    job: AIJob
  }>(`/reviews/${review.id}/questions`, {
    operation_id: operationId,
    expected_version: review.version,
    answer,
  })
}

export async function requestReviewReport(
  review: ReviewSession,
  instruction: string,
  operationId: string,
) {
  return api<{
    operation_id: string
    review: ReviewSession
    job: AIJob
  }>(`/reviews/${review.id}/reports`, {
    operation_id: operationId,
    expected_version: review.version,
    instruction,
  })
}

export async function getReviewReport(reviewId: string, version: number) {
  return api<ReviewReportDetail>(`/reviews/${reviewId}/reports/${version}`)
}

export async function createReviewAnnotation(
  reportId: string,
  content: string,
  operationId: string,
) {
  return api<{ operation_id: string; annotation: ReviewAnnotation }>(
    `/review-reports/${reportId}/annotations`,
    { operation_id: operationId, content },
  )
}

export async function updateReviewAnnotation(
  annotation: ReviewAnnotation,
  content: string,
  operationId: string,
) {
  return api<{ operation_id: string; annotation: ReviewAnnotation }>(
    `/review-annotations/${annotation.id}`,
    {
      operation_id: operationId,
      expected_version: annotation.version,
      content,
    },
    'PUT',
  )
}

export async function deleteReviewAnnotation(
  annotation: ReviewAnnotation,
  operationId: string,
) {
  return api<{ operation_id: string; annotation: ReviewAnnotation }>(
    `/review-annotations/${annotation.id}`,
    {
      operation_id: operationId,
      expected_version: annotation.version,
    },
    'DELETE',
  )
}

export async function confirmMemoryProposal(
  proposal: MemoryProposal,
  operationId: string,
) {
  return api<{
    operation_id: string
    proposal: MemoryProposal
    memory: import('./memories').Memory
  }>(`/memory-proposals/${proposal.id}/confirm`, {
    operation_id: operationId,
    expected_version: proposal.version,
  })
}

export async function rejectMemoryProposal(
  proposal: MemoryProposal,
  operationId: string,
) {
  return api<{ operation_id: string; proposal: MemoryProposal }>(
    `/memory-proposals/${proposal.id}/reject`,
    {
      operation_id: operationId,
      expected_version: proposal.version,
    },
  )
}
