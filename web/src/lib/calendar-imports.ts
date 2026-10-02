import { api, uploadApi } from './api'
import type { CalendarWriteResult, SeriesDraft } from './calendar'

export type ImportFormat = 'xlsx' | 'ics' | 'screenshot'
export type ImportState = 'ready' | 'partially_confirmed' | 'completed' | 'expired'
export type CandidateState = 'valid' | 'invalid' | 'duplicate' | 'confirmed' | 'skipped'

export interface CalendarSource {
  id: string
  kind: 'excel_file' | 'ics_file' | 'screenshot'
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
  format: ImportFormat
  filename: string
  state: ImportState
  candidate_count: number
  counts: ImportCounts
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
