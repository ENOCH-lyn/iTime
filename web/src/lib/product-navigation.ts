import {
  CalendarDays,
  ChartNoAxesCombined,
  CheckSquare2,
  ListChecks,
  MessageCircleMore,
  NotebookPen,
  TimerReset,
} from 'lucide-vue-next'

export type ProductArea =
  | 'ai'
  | 'tasks'
  | 'plans'
  | 'calendar'
  | 'focus'
  | 'reviews'
  | 'stats'

export const primaryNavigation = [
  { key: 'ai', to: '/ai', label: 'AI', icon: MessageCircleMore },
  { key: 'tasks', to: '/tasks', label: '任务', icon: CheckSquare2 },
  { key: 'calendar', to: '/calendar', label: '日历', icon: CalendarDays },
  { key: 'focus', to: '/focus', label: '专注', icon: TimerReset },
] as const

export const secondaryNavigation = [
  { key: 'plans', to: '/plans', label: '规划', icon: ListChecks },
  { key: 'reviews', to: '/reviews', label: '复盘', icon: NotebookPen },
  { key: 'stats', to: '/stats', label: '统计', icon: ChartNoAxesCombined },
] as const
