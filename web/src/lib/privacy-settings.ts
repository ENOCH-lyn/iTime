import { api } from './api'

export interface PrivacySettings {
  quality_improvement_enabled: boolean
  version: number
  updated_at: string
}

export async function getPrivacySettings() {
  return (await api<{ settings: PrivacySettings }>('/privacy-settings')).settings
}

export async function updatePrivacySettings(input: {
  operation_id: string
  expected_version: number
  quality_improvement_enabled: boolean
}) {
  return api<{
    operation_id: string
    settings: PrivacySettings
  }>('/privacy-settings', input, 'PUT')
}
