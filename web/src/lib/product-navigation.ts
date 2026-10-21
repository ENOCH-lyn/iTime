import {
  CalendarDays,
  CalendarRange,
  CheckSquare2,
  MessageCircleMore,
} from 'lucide-vue-next'

export type ProductArea =
  | 'timetable'
  | 'ai'
  | 'tasks'
  | 'calendar'
  | 'plans'
  | 'reviews'
  | 'me'

export const primaryNavigation = [
  { key: 'timetable', to: '/timetable', label: '课表', icon: CalendarRange },
  { key: 'tasks', to: '/tasks', label: '任务', icon: CheckSquare2 },
  { key: 'ai', to: '/ai', label: 'AI', icon: MessageCircleMore },
  { key: 'calendar', to: '/calendar', label: '日历', icon: CalendarDays },
] as const
