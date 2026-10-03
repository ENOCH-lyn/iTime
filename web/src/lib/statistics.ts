import { api } from './api'

export type StatisticsScope = 'day' | 'week' | 'month'

export interface PlanningMetrics {
  block_count: number
  task_count: number
  planned_seconds: number
}

export interface TaskMetrics {
  completed_count: number
  completed_task_count: number
  completed_occurrence_count: number
  skipped_occurrence_count: number
}

export interface FocusMetrics {
  session_count: number
  total_seconds: number
  finished_count: number
  finished_seconds: number
  abandoned_count: number
  abandoned_seconds: number
  manual_count: number
  manual_seconds: number
  revised_count: number
  ongoing_count: number
  ongoing_seconds: number
  proportional_focus_count: number
}

export interface StatisticsDay {
  activity_date: string
  planning: PlanningMetrics
  tasks: TaskMetrics
  focus: FocusMetrics
}

export interface StatisticsResult {
  scope: StatisticsScope
  anchor_date: string
  period_start_date: string
  period_end_date: string
  data_end_date: string
  current_activity_date: string
  time_zone: string
  activity_day_start_minute: 240
  calculated_at: string
  methodology_version: 'statistics_v1'
  summary: {
    planning: PlanningMetrics
    tasks: TaskMetrics
    focus: FocusMetrics
  }
  days: StatisticsDay[]
}

export async function getStatistics(
  scope: StatisticsScope,
  anchorDate: string,
) {
  const params = new URLSearchParams({
    scope,
    anchor_date: anchorDate,
  })
  return api<StatisticsResult>(`/statistics?${params}`)
}

export function formatDuration(seconds: number) {
  if (seconds <= 0) return '0 分钟'
  if (seconds < 60) return '< 1 分钟'
  const minutes = Math.round(seconds / 60)
  const hours = Math.floor(minutes / 60)
  const remainder = minutes % 60
  if (!hours) return `${minutes} 分钟`
  return remainder ? `${hours} 小时 ${remainder} 分钟` : `${hours} 小时`
}
