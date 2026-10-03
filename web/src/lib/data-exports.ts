import { api, downloadApi } from './api'

export type DataExportStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'expired'

export interface DataExportJob {
  id: string
  status: DataExportStatus
  schema_version: number
  attempt_count: number
  snapshot_at: string | null
  filename: string | null
  byte_size: number | null
  sha256: string | null
  expires_at: string | null
  error: { code: string; message: string } | null
  created_at: string
  updated_at: string
  finished_at: string | null
}

export function listDataExports() {
  return api<{ jobs: DataExportJob[] }>('/data-exports?limit=20')
}

export function getDataExport(id: string) {
  return api<{ job: DataExportJob }>(`/data-exports/${id}`)
}

export function createDataExport(operationId: string) {
  return api<{
    operation_id: string
    reused: boolean
    job: DataExportJob
  }>('/data-exports', { operation_id: operationId })
}

export function downloadDataExport(id: string) {
  return downloadApi(`/data-exports/${id}/download`, 60_000)
}

export function formatExportBytes(value: number | null) {
  if (value === null) return '大小待生成'
  if (value < 1024) return `${value} B`
  const units = ['KB', 'MB', 'GB']
  let size = value / 1024
  let index = 0
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024
    index += 1
  }
  return `${size >= 10 ? size.toFixed(0) : size.toFixed(1)} ${units[index]}`
}
