import { api } from './api'

export interface ToolProposal {
  id: string
  conversation_id: string
  source_message_id: string | null
  tool_name:
    | 'update_task'
    | 'complete_task'
    | 'delete_task'
    | 'delete_calendar_series'
    | 'update_calendar_series'
    | 'create_calendar_exception'
    | 'update_plan_block'
    | 'create_plan_block'
    | 'delete_plan_block'
    | string
  operation_id: string
  status: 'pending' | 'applied' | 'rejected' | 'expired'
  preview: {
    summary?: string
    details?: Record<string, unknown>
  }
  payload: Record<string, unknown>
  expected_version: number | null
  expires_at: string
  applied_at: string | null
  rejected_at: string | null
  applied_resource_id: string | null
  applied_resource_version: number | null
  version: number
  created_at: string
  updated_at: string
}

export async function getToolProposal(id: string) {
  return (await api<{ proposal: ToolProposal }>(`/ai/tool-proposals/${id}`)).proposal
}

export async function applyToolProposal(
  proposal: Pick<ToolProposal, 'id' | 'version'>,
  operationId: string = crypto.randomUUID(),
) {
  return api<{ proposal: ToolProposal; task?: unknown }>(
    `/ai/tool-proposals/${proposal.id}/apply`,
    {
      operation_id: operationId,
      expected_proposal_version: proposal.version,
    },
  )
}

export async function rejectToolProposal(
  proposal: Pick<ToolProposal, 'id' | 'version'>,
  operationId: string = crypto.randomUUID(),
) {
  return api<{ proposal: ToolProposal }>(
    `/ai/tool-proposals/${proposal.id}/reject`,
    {
      operation_id: operationId,
      expected_proposal_version: proposal.version,
    },
  )
}
