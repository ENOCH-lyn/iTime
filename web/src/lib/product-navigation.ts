import {
  CalendarDays,
  CheckSquare2,
  CircleUser,
  ListChecks,
  MessageCircleMore,
} from 'lucide-vue-next'

export type ProductArea =
  | 'ai'
  | 'tasks'
  | 'calendar'
  | 'plans'
  | 'me'

export const primaryNavigation = [
  { key: 'ai', to: '/ai', label: 'AI', icon: MessageCircleMore },
  { key: 'calendar', to: '/calendar', label: '日历', icon: CalendarDays },
  { key: 'tasks', to: '/tasks', label: '任务', icon: CheckSquare2 },
  { key: 'plans', to: '/plans', label: '规划', icon: ListChecks },
  { key: 'me', to: '/me', label: '我的', icon: CircleUser },
] as const
