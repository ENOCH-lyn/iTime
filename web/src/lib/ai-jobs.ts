import { api } from './api'

export type AIJobStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'timed_out'
  | 'cancelled'

export interface AIJob {
  id: string
  capability:
    | 'calendar_screenshot'
    | 'conversation'
    | 'planning'
    | 'review'
    | 'transcription'
  conversation_id: string | null
  review_session_id: string | null
  status: AIJobStatus
  input_summary: Record<string, unknown>
  output: {
    resource_type: string
    batch_id?: string
    candidate_count?: number
    message_id?: string
    proposal_id?: string
    proposal_kind?: string
    report_id?: string
    review_session_id?: string
    transcription_id?: string
    import_batch_id?: string
    import_job_id?: string
    import_error?: { code: string; message: string }
    version?: number
  } | null
  error: { code: string; message: string } | null
  attempt_count: number
  max_attempts: number
  last_event_sequence: number
  deadline_at: string
  version: number
  created_at: string
  updated_at: string
  finished_at: string | null
}

export async function getAIJob(id: string) {
  return api<AIJob>(`/ai/jobs/${id}`)
}

export async function cancelAIJob<T extends AIJob = AIJob>(
  id: string,
  operationId: string,
) {
  return api<{ operation_id: string; job: T }>(
    `/ai/jobs/${id}/cancel`,
    { operation_id: operationId },
  )
}
