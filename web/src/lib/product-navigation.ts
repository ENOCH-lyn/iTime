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
    to: '/calendar',
    label: '日历',
    symbol: 'calendar_month',
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
