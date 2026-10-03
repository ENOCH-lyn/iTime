import { api } from './api'

export type MemoryKind = 'preference' | 'constraint' | 'context' | 'goal'
export type MemoryLifecycle = 'active' | 'trashed' | 'purged'

export interface Memory {
  id: string
  kind: MemoryKind
  title: string | null
  content: string | null
  source_type: 'review_proposal' | 'manual'
  source_proposal_id: string | null
  lifecycle: MemoryLifecycle
  deleted_at: string | null
  purge_after: string | null
  purged_at: string | null
  version: number
  created_at: string
  updated_at: string
}

export interface MemorySettings {
  enabled: boolean
  version: number
  created_at: string
  updated_at: string
}

export interface MemoryDraft {
  kind: MemoryKind
  title: string
  content: string
}

export async function getMemorySettings() {
  return (await api<{ settings: MemorySettings }>('/memory-settings')).settings
}

export async function saveMemorySettings(
  settings: MemorySettings,
  enabled: boolean,
  operationId: string,
) {
  return (await api<{
    operation_id: string
    settings: MemorySettings
  }>('/memory-settings', {
    operation_id: operationId,
    expected_version: settings.version,
    enabled,
  }, 'PUT')).settings
}

export async function listMemories(lifecycle: 'active' | 'trashed' = 'active') {
  return api<{
    items: Memory[]
    next_cursor: { before_time: string; before_id: string } | null
  }>(`/memories?lifecycle=${lifecycle}&limit=100`)
}

export async function createMemory(draft: MemoryDraft, operationId: string) {
  return (await api<{ operation_id: string; memory: Memory }>(
    '/memories',
    { operation_id: operationId, ...draft },
  )).memory
}

export async function updateMemory(
  memory: Memory,
  draft: MemoryDraft,
  operationId: string,
) {
  return (await api<{ operation_id: string; memory: Memory }>(
    `/memories/${memory.id}`,
    {
      operation_id: operationId,
      expected_version: memory.version,
      ...draft,
    },
    'PUT',
  )).memory
}

export async function deleteMemory(memory: Memory, operationId: string) {
  return (await api<{ operation_id: string; memory: Memory }>(
    `/memories/${memory.id}`,
    {
      operation_id: operationId,
      expected_version: memory.version,
    },
    'DELETE',
  )).memory
}

export async function restoreMemory(memory: Memory, operationId: string) {
  return (await api<{ operation_id: string; memory: Memory }>(
    `/memories/${memory.id}/restore`,
    {
      operation_id: operationId,
      expected_version: memory.version,
    },
  )).memory
}
