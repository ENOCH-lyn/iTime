import { api, uploadApi } from './api'
import type { AIJob } from './ai-jobs'

export type VoiceTranscriptionPurpose = 'task_title' | 'difficulty_content'
export type VoiceTranscriptionResolution =
  | 'pending'
  | 'accepted'
  | 'discarded'
  | 'expired'

export interface TranscriptionJob extends AIJob {
  capability: 'transcription'
  input_summary: {
    purpose: VoiceTranscriptionPurpose
    filename: string
    content_type: 'audio/wav'
    byte_size: number
    duration_ms: number
    sample_rate_hz: 16000
    channel_count: 1
    sample_width_bits: 16
    content_sha256: string
  }
  output: {
    resource_type: 'voice_transcription'
    transcription_id: string
  } | null
}

export interface VoiceTranscription {
  id: string
  job_id: string
  purpose: VoiceTranscriptionPurpose
  transcript: string | null
  transcript_sha256: string | null
  language: string | null
  resolution: VoiceTranscriptionResolution
  ready_at: string | null
  resolved_at: string | null
  text_purged_at: string | null
  expires_at: string
  version: number
  created_at: string
  updated_at: string
}

export interface VoiceTranscriptionResult {
  transcription: VoiceTranscription
  job: TranscriptionJob
}

export interface VoiceTranscriptionMutationResult
  extends VoiceTranscriptionResult {
  operation_id: string
}

function filename() {
  return `itime-voice-${Date.now()}.wav`
}

export async function sha256Blob(blob: Blob) {
  const digest = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer())
  return [...new Uint8Array(digest)]
    .map(byte => byte.toString(16).padStart(2, '0'))
    .join('')
}

export async function uploadVoiceTranscription(
  blob: Blob,
  purpose: VoiceTranscriptionPurpose,
  operationId: string,
  signal?: AbortSignal,
) {
  const file = new File([blob], filename(), {
    type: 'audio/wav',
    lastModified: Date.now(),
  })
  const params = new URLSearchParams({
    purpose,
    filename: file.name,
  })
  return uploadApi<VoiceTranscriptionMutationResult>(
    `/voice-transcriptions?${params}`,
    file,
    operationId,
    {
      signal,
      timeoutMs: 30_000,
      timeoutMessage: '录音上传超时。请先检查转写状态，再决定是否重试。',
      networkMessage: '无法连接服务。录音是否已接收尚未确认，请检查状态后重试。',
    },
  )
}

export async function listVoiceTranscriptions(
  purpose: VoiceTranscriptionPurpose,
  limit = 20,
) {
  const params = new URLSearchParams({
    purpose,
    limit: String(limit),
  })
  return api<{
    items: VoiceTranscriptionResult[]
    next_cursor: { before_time: string; before_id: string } | null
  }>(`/voice-transcriptions?${params}`)
}

export async function getVoiceTranscription(id: string) {
  return api<VoiceTranscriptionResult>(`/voice-transcriptions/${id}`)
}

export async function resolveVoiceTranscription(
  item: VoiceTranscription,
  action: 'accept' | 'discard',
  operationId: string,
) {
  return api<VoiceTranscriptionMutationResult>(
    `/voice-transcriptions/${item.id}/resolve`,
    {
      operation_id: operationId,
      expected_version: item.version,
      action,
      transcript_sha256: action === 'accept' ? item.transcript_sha256 : null,
    },
  )
}
