export type ProductArea =
  | 'timetable'
  | 'ai'
  | 'tasks'
  | 'plans'
  | 'reviews'
  | 'me'

export const primaryNavigation = [
  {
    key: 'timetable',
    to: '/timetable',
    label: '课表',
    symbol: 'calendar_view_week',
  },
  {
    key: 'tasks',
    to: '/tasks',
    label: '任务',
    symbol: 'checklist',
  },
  {
    key: 'ai',
    to: '/ai',
    label: 'AI',
    symbol: 'smart_toy',
  },
] as const
