import { api, uploadApi } from './api'

export type AIJobStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'timed_out'
  | 'cancelled'

export interface AIJob {
  id: string
  capability: 'calendar_screenshot'
  status: AIJobStatus
  input_summary: {
    filename: string
    content_type: string
    byte_size: number
    width: number
    height: number
    content_sha256: string
  }
  output: {
    resource_type: 'calendar_import_batch'
    batch_id: string
    candidate_count: number
  } | null
  error: { code: string; message: string } | null
  attempt_count: number
  max_attempts: number
  deadline_at: string
  version: number
  created_at: string
  updated_at: string
  finished_at: string | null
}

export async function uploadCalendarScreenshot(
  file: File,
  operationId: string,
) {
  const params = new URLSearchParams({ filename: file.name })
  return uploadApi<{ operation_id: string; job: AIJob }>(
    `/calendar/imports/screenshots?${params}`,
    file,
    operationId,
  )
}

export async function listScreenshotJobs() {
  return (await api<{
    items: AIJob[]
    next_cursor: { before_time: string; before_id: string } | null
  }>('/ai/jobs?capability=calendar_screenshot&limit=20')).items
}

export async function getAIJob(id: string) {
  return api<AIJob>(`/ai/jobs/${id}`)
}

export async function cancelAIJob(id: string, operationId: string) {
  return api<{ operation_id: string; job: AIJob }>(
    `/ai/jobs/${id}/cancel`,
    { operation_id: operationId },
  )
}
