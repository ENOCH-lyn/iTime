import { api } from './api'

export type AgentProposalStatus =
  | 'pending'
  | 'applied'
  | 'rejected'
  | 'expired'

export interface AgentProposalChange {
  id: string
  position: number
  action: 'create' | 'update' | 'delete'
  entity_type: 'task' | 'scheduled_block'
  target_id: string | null
  before_data: Record<string, unknown> | null
  after_data: Record<string, unknown> | null
}

export interface AgentTaskPlanPayload {
  assistant_message: string
  task_title: string
  task_description: string
  estimated_minutes: number
  local_date: string
  starts_at: string
  ends_at: string
  assumptions: string[]
}

export interface AgentProposal {
  id: string
  kind: 'task_plan'
  status: 'pending' | 'applied' | 'rejected'
  effective_status: AgentProposalStatus
  title: string
  summary: string
  assumptions: string[]
  warnings: string[]
  payload: AgentTaskPlanPayload
  changes: AgentProposalChange[]
  version: number
  expires_at: string
  applied_at: string | null
  rejected_at: string | null
  applied_plan_id: string | null
  applied_plan_version_id: string | null
}

export async function getAgentProposal(id: string) {
  return (await api<{ proposal: AgentProposal }>(
    `/ai/action-proposals/${id}`,
  )).proposal
}

export async function applyAgentProposal(
  proposal: AgentProposal,
  operationId: string,
) {
  return api<{
    operation_id: string
    proposal: AgentProposal
    application?: {
      task_id: string
      plan_id: string
      plan_version_id: string
    }
  }>(`/ai/action-proposals/${proposal.id}/apply`, {
    operation_id: operationId,
    expected_proposal_version: proposal.version,
  })
}

export async function rejectAgentProposal(
  proposal: AgentProposal,
  operationId: string,
) {
  return api<{ operation_id: string; proposal: AgentProposal }>(
    `/ai/action-proposals/${proposal.id}/reject`,
    {
      operation_id: operationId,
      expected_proposal_version: proposal.version,
    },
  )
}
