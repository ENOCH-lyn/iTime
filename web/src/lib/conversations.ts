import { api } from './api'
import type { AIJob } from './ai-jobs'
import type { CalendarImportBatch } from './calendar-imports'
import type { PlanningFields } from './plans'

export type ConversationResponseMode = 'auto' | 'text' | 'planning'

export interface Conversation {
  id: string
  title: string
  title_source: 'auto' | 'user'
  version: number
  archived: boolean
  archived_at: string | null
  created_at: string
  updated_at: string
  active_job: AIJob | null
}

export interface ConversationMessage {
  id: string
  conversation_id: string
  sequence: number
  role: 'user' | 'assistant'
  status: 'complete' | 'queued' | 'running' | 'failed' | 'timed_out' | 'cancelled'
  content: string
  error: { code: string; message: string } | null
  job: AIJob | null
  calendar_imports?: CalendarImportBatch[]
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface ConversationPage {
  items: Conversation[]
  next_cursor: { before_time: string; before_id: string } | null
}

export interface MessagePage {
  conversation: Conversation
  items: ConversationMessage[]
  next_before_sequence: number | null
}

export interface AIJobEvent {
  sequence: number
  generation: number
  kind: 'job_state' | 'content_reset' | 'content_delta'
  payload: {
    status?: AIJob['status']
    message_id?: string
    text?: string
    attempt?: number
    retrying?: boolean
    proposal_id?: string
    error_code?: string
    error_message?: string
  }
  created_at: string
}

export async function listConversations(
  archived = false,
  cursor: ConversationPage['next_cursor'] = null,
) {
  const params = new URLSearchParams({
    archived: String(archived),
    limit: '50',
  })
  if (cursor) {
    params.set('before_time', cursor.before_time)
    params.set('before_id', cursor.before_id)
  }
  return api<ConversationPage>(`/ai/conversations?${params}`)
}

export async function createConversation(operationId: string, title?: string) {
  return api<{ operation_id: string; conversation: Conversation }>(
    '/ai/conversations',
    {
      operation_id: operationId,
      ...(title ? { title } : {}),
    },
  )
}

export async function getConversation(id: string) {
  return api<Conversation>(`/ai/conversations/${id}`)
}

export async function updateConversation(
  conversation: Conversation,
  operationId: string,
  changes: { title?: string; archived?: boolean },
) {
  return api<{ operation_id: string; conversation: Conversation }>(
    `/ai/conversations/${conversation.id}`,
    {
      operation_id: operationId,
      expected_version: conversation.version,
      ...changes,
    },
    'PATCH',
  )
}

export async function listConversationMessages(
  id: string,
  beforeSequence?: number,
) {
  const params = new URLSearchParams({ limit: '50' })
  if (beforeSequence !== undefined) {
    params.set('before_sequence', String(beforeSequence))
  }
  return api<MessagePage>(`/ai/conversations/${id}/messages?${params}`)
}

export async function sendConversationMessage(
  conversation: Conversation,
  operationId: string,
  content: string,
  responseMode: ConversationResponseMode = 'auto',
  planning?: PlanningFields,
  importBatchId?: string,
  imageMediaId?: string,
) {
  return api<{
    operation_id: string
    conversation: Conversation
    messages: ConversationMessage[]
    job: AIJob
  }>(
    `/ai/conversations/${conversation.id}/messages`,
    {
      operation_id: operationId,
      expected_conversation_version: conversation.version,
      content,
      response_mode: responseMode,
      ...(responseMode === 'planning' ? { planning } : {}),
      ...(importBatchId ? { import_batch_id: importBatchId } : {}),
      ...(imageMediaId ? { image_media_id: imageMediaId } : {}),
    },
  )
}

export function jobEventsUrl(jobId: string, after: number) {
  return `/api/v1/ai/jobs/${jobId}/events?after=${after}`
}
