import { api, uploadApi, uploadFormApi } from './api'
import {
  cancelAIJob,
  getAIJob,
  type AIJob,
} from './ai-jobs'
import type { CalendarWriteResult, SeriesDraft } from './calendar'

export type ImportSourceType =
  | 'xlsx_file'
  | 'ics_file'
  | 'image'
  | 'public_url'
  | 'text'
export type ImportState =
  | 'queued'
  | 'processing'
  | 'needs_review'
  | 'confirmed'
  | 'cancelled'
  | 'failed'
  | 'expired'
export type CandidateState = 'valid' | 'invalid' | 'duplicate' | 'confirmed' | 'skipped'

export interface CalendarSource {
  id: string
  kind: 'excel_file' | 'ics_file' | 'image' | 'public_url' | 'text'
  name: string
  version: number
  created_at: string
  updated_at: string
}

export interface ImportCounts {
  valid: number
  invalid: number
  duplicate: number
  confirmed: number
  skipped: number
}

export interface CalendarImportBatch {
  id: string
  source: CalendarSource
  format: ImportSourceType
  source_type: ImportSourceType
  source_display: string | null
  filename: string
  state: ImportState
  candidate_count: number
  counts: ImportCounts
  input_summary: Record<string, unknown>
  conversation_id: string | null
  message_id: string | null
  job_id: string | null
  error: { code: string; message: string } | null
  version: number
  expires_at: string
  created_at: string
  updated_at: string
}

export interface ImportWarning {
  code: string
  message: string
  occurrence_key?: string
  position?: number
  series_id?: string
  series_ids?: string[]
  fields?: string[]
}

export interface CalendarImportCandidate {
  id: string
  position: number
  state: CandidateState
  raw_data: Record<string, unknown>
  draft: SeriesDraft | null
  external_uid: string | null
  external_recurrence_id: string | null
  warnings: ImportWarning[]
  error: {
    code: string
    message: string
    details: Record<string, unknown>
  } | null
  duplicate_series_id: string | null
  confirmed_series_id: string | null
  version: number
  created_at: string
  updated_at: string
}

export interface CalendarImportDetail {
  batch: CalendarImportBatch
  candidates: CalendarImportCandidate[]
}

export interface ImportOutcome {
  candidate_id: string
  outcome: 'created' | 'merged' | 'skipped'
  series?: CalendarWriteResult['series']
  warnings?: CalendarWriteResult['warnings']
  conflicts?: CalendarWriteResult['conflicts']
}

export interface CalendarAIImportJob extends AIJob {
  capability: 'calendar_screenshot'
  input_summary: Record<string, unknown> & {
    source_type: 'image' | 'public_url' | 'text'
    batch_id: string
  }
  output: {
    resource_type: 'calendar_import_batch'
    batch_id: string
    candidate_count: number
  } | null
}

export type CalendarAIImportInput =
  | {
      sourceType: 'image'
      file: File
      conversationId?: string
      messageId?: string
    }
  | {
      sourceType: 'public_url' | 'text'
      content: string
      conversationId?: string
      messageId?: string
    }

export async function listCalendarImports() {
  return (await api<{
    items: CalendarImportBatch[]
    next_cursor: { before_time: string; before_id: string } | null
  }>('/calendar/imports?limit=5')).items
}

export async function getCalendarImport(batchId: string) {
  return api<CalendarImportDetail>(`/calendar/imports/${batchId}`)
}

export async function uploadCalendarFile(
  file: File,
  operationId: string = crypto.randomUUID(),
) {
  const params = new URLSearchParams({ filename: file.name })
  return uploadApi<{
    operation_id: string
    batch: CalendarImportBatch
  }>(`/calendar/imports/files?${params}`, file, operationId)
}

export async function createAIImport(
  input: CalendarAIImportInput,
  operationId: string = crypto.randomUUID(),
) {
  if (input.sourceType === 'image') {
    const form = new FormData()
    form.set('source_type', 'image')
    form.set('file', input.file, input.file.name)
    if (input.conversationId) form.set('conversation_id', input.conversationId)
    if (input.messageId) form.set('message_id', input.messageId)
    return uploadFormApi<{
      operation_id: string
      batch: CalendarImportBatch
      job: CalendarAIImportJob
    }>('/calendar/imports/ai', form, operationId, {
      timeoutMs: 30_000,
      timeoutMessage: '图片上传超时，请检查导入记录后重试。',
      networkMessage: '无法上传图片。请检查网络后重试。',
    })
  }
  return api<{
    operation_id: string
    batch: CalendarImportBatch
    job: CalendarAIImportJob
  }>(
    '/calendar/imports/ai',
    {
      source_type: input.sourceType,
      content: input.content,
      ...(input.conversationId
        ? { conversation_id: input.conversationId }
        : {}),
      ...(input.messageId ? { message_id: input.messageId } : {}),
    },
    'POST',
    30_000,
    { 'X-Operation-ID': operationId },
  )
}

export async function getCalendarImportJob(id: string) {
  return getAIJob(id) as Promise<CalendarAIImportJob>
}

export async function cancelCalendarImportJob(
  id: string,
  operationId: string = crypto.randomUUID(),
) {
  return cancelAIJob<CalendarAIImportJob>(id, operationId)
}

export async function updateImportCandidate(
  batch: CalendarImportBatch,
  candidate: CalendarImportCandidate,
  draft: SeriesDraft,
  operationId: string = crypto.randomUUID(),
) {
  return api<{
    operation_id: string
    batch: CalendarImportBatch
    candidate: CalendarImportCandidate
  }>(
    `/calendar/imports/${batch.id}/candidates/${candidate.id}`,
    {
      operation_id: operationId,
      expected_batch_version: batch.version,
      expected_version: candidate.version,
      ...draft,
    },
    'PUT',
  )
}

export async function confirmCalendarImport(
  batch: CalendarImportBatch,
  decisions: {
    candidate_id: string
    expected_version: number
    action: 'confirm' | 'skip'
  }[],
  operationId: string = crypto.randomUUID(),
) {
  return api<{
    operation_id: string
    batch: CalendarImportBatch
    outcomes: ImportOutcome[]
  }>(`/calendar/imports/${batch.id}/confirm`, {
    operation_id: operationId,
    expected_batch_version: batch.version,
    decisions,
  })
}
