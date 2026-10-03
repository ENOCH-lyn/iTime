import { api } from './api'
import type { AIConfigCapability } from './admin-ai'
import type { Page } from './admin-management'

export type BodyLogStatus =
  | 'running'
  | 'succeeded'
  | 'retryable_failed'
  | 'failed'
  | 'timed_out'
  | 'cancelled'
  | 'abandoned'
export type QualityStatus = 'pending' | 'reviewed'
export type QualityOutcome = 'acceptable' | 'needs_improvement'
export type QualityPurpose =
  | 'manual_quality_review'
  | 'prompt_optimization'
  | 'effect_evaluation'

export interface GovernanceOwner {
  id: string
  email: string
}

export interface AIBodyLog {
  id: string
  job_id: string
  owner: GovernanceOwner
  attempt_number: number
  capability: AIConfigCapability
  status: BodyLogStatus
  request_prepared: boolean
  error_code: string | null
  error_stage: string | null
  provider_request_id: string | null
  http_status: number | null
  started_at: string
  finished_at: string | null
  expires_at: string
}

export interface AIBodyLogDetail extends AIBodyLog {
  request_body: Record<string, unknown>
  response_body: string
}

export interface AIQualitySample {
  id: string
  source_job_id: string
  source_body_log_id: string
  owner: GovernanceOwner
  capability: AIConfigCapability
  status: QualityStatus
  outcome: QualityOutcome | null
  reviewed_by_user_id: string | null
  reviewed_at: string | null
  version: number
  sampled_at: string
  expires_at: string
}

export interface AIQualitySampleDetail extends AIQualitySample {
  request_body: Record<string, unknown>
  response_body: string
  review_notes: string | null
}

export interface AuditEvent {
  id: string
  actor: GovernanceOwner | null
  action: string
  request_id: string
  target_id: string | null
  details: Record<string, unknown>
  created_at: string
}

export function listBodyLogs(query: URLSearchParams) {
  return api<Page<AIBodyLog>>(`/admin/ai-body-logs?${query}`)
}

export function accessBodyLog(id: string) {
  return api<AIBodyLogDetail>(
    `/admin/ai-body-logs/${id}/access`,
    { purpose: 'troubleshooting' },
  )
}

export function listQualitySamples(query: URLSearchParams) {
  return api<Page<AIQualitySample>>(`/admin/ai-quality-samples?${query}`)
}

export function accessQualitySample(id: string, purpose: QualityPurpose) {
  return api<AIQualitySampleDetail>(
    `/admin/ai-quality-samples/${id}/access`,
    { purpose },
  )
}

export function reviewQualitySample(id: string, input: {
  expected_version: number
  outcome: QualityOutcome
  review_notes: string | null
}) {
  return api<AIQualitySampleDetail>(
    `/admin/ai-quality-samples/${id}/review`,
    input,
    'PUT',
  )
}

export function listAuditEvents(query: URLSearchParams) {
  return api<Page<AuditEvent>>(`/admin/audit-events?${query}`)
}
