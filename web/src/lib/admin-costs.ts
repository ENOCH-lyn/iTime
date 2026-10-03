import { api } from './api'
import type { AIConfigCapability } from './admin-ai'
import type { GovernanceOwner } from './admin-governance'
import type { Page } from './admin-management'

export type ProviderCostStatus =
  | 'succeeded'
  | 'retryable_failed'
  | 'failed'
  | 'timed_out'
  | 'cancelled'
  | 'abandoned'
export type ProviderUsageSource = 'provider' | 'estimated' | 'not_sent'

export interface ProviderCost {
  id: string
  job_id: string
  owner: GovernanceOwner
  attempt_number: number
  attempt_status: ProviderCostStatus
  capability: AIConfigCapability
  config: {
    id: string
    version: number
    model: string
  }
  provider_request_id: string | null
  http_status: number | null
  error_code: string | null
  error_stage: string | null
  provider_started_at: string | null
  usage_source: ProviderUsageSource
  estimation_reason: string | null
  estimator_version: string | null
  usage: {
    input_tokens: number
    cached_input_tokens: number
    output_tokens: number
    total_tokens: number
    details: Record<string, unknown>
  }
  pricing: {
    currency: string
    input_per_million_tokens: string
    cached_input_per_million_tokens: string
    output_per_million_tokens: string
  } | null
  amounts: {
    input: string
    cached_input: string
    output: string
    total: string
  } | null
  created_at: string
}

export function listProviderCosts(query: URLSearchParams) {
  return api<Page<ProviderCost>>(`/admin/provider-costs?${query}`)
}
