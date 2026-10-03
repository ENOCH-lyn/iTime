import { api } from './api'

export type AIConfigStatus = 'draft' | 'tested' | 'published' | 'retired'
export type AIConfigCapability = 'calendar_screenshot' | 'conversation'

export interface AIConfigVersion {
  id: string
  capability: AIConfigCapability
  version: number
  endpoint_url: string
  model: string
  timeout_seconds: number
  max_output_tokens: number
  key_configured: true
  status: AIConfigStatus
  current: boolean
  test_in_progress: boolean
  last_test_started_at: string | null
  last_test_completed_at: string | null
  last_test_error_code: string | null
  last_test_error_stage: string | null
  created_at: string
  updated_at: string
  published_at: string | null
  retired_at: string | null
}

export async function listAIConfigs(capability: AIConfigCapability) {
  return (await api<{
    items: AIConfigVersion[]
    next_cursor: { before_time: string; before_id: string } | null
  }>(`/admin/ai-configs?capability=${capability}&limit=100`)).items
}

export async function createAIConfig(capability: AIConfigCapability, input: {
  endpoint_url: string
  model: string
  api_key: string
  timeout_seconds: number
  max_output_tokens: number
}) {
  return api<AIConfigVersion>('/admin/ai-configs', {
    capability,
    ...input,
  })
}

export async function testAIConfig(id: string, timeoutSeconds: number) {
  return api<AIConfigVersion>(
    `/admin/ai-configs/${id}/test`,
    {},
    'POST',
    (timeoutSeconds + 5) * 1000,
  )
}

export async function publishAIConfig(id: string) {
  return api<AIConfigVersion>(`/admin/ai-configs/${id}/publish`, {})
}
