import type { CalendarOccurrence } from './calendar'
import type { PlanningBlockProjection } from './plans'
import type { TaskOccurrence } from './task-schedules'

export type TimetableKind = 'course' | 'fixed' | 'task' | 'plan'

export interface BoardEvent {
  id: string
  title: string
  subtitle: string
  dayIndex: number
  startMin: number
  endMin: number
  kind: TimetableKind
  colorIndex: number
  cancelled: boolean
  occurrence?: CalendarOccurrence
  task?: TaskOccurrence
  plan?: PlanningBlockProjection
}

export interface TimetableDay {
  date: string
  weekday: string
  dayNumber: number
  month: number
  isToday: boolean
}

export const EVENT_COLORS = [
  '#3f7a5d',
  '#b06a44',
  '#4b6f9e',
  '#8a6d33',
  '#775b8f',
  '#397b77',
] as const

export function parseLocalDate(value: string): Date {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day, 12)
}

export function localDate(value: Date): string {
  const pad = (part: number) => String(part).padStart(2, '0')
  return `${value.getFullYear()}-${pad(value.getMonth() + 1)}-${pad(value.getDate())}`
}

export function addDays(value: string, amount: number): string {
  const date = parseLocalDate(value)
  date.setDate(date.getDate() + amount)
  return localDate(date)
}

export function startOfWeek(value: string): string {
  const date = parseLocalDate(value)
  const weekday = date.getDay() || 7
  date.setDate(date.getDate() - weekday + 1)
  return localDate(date)
}

export function isoWeekday(value: string): number {
  const weekday = parseLocalDate(value).getDay()
  return weekday === 0 ? 7 : weekday
}

export function daysBetween(from: string, to: string): number {
  const start = parseLocalDate(from).getTime()
  const end = parseLocalDate(to).getTime()
  return Math.round((end - start) / 86_400_000)
}

export function dateInZone(value: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(value)
  const part = (type: Intl.DateTimeFormatPartTypes) => (
    parts.find(candidate => candidate.type === type)?.value ?? ''
  )
  return `${part('year')}-${part('month')}-${part('day')}`
}

export function minutesInZone(value: Date, timeZone: string): number {
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).formatToParts(value)
  const part = (type: Intl.DateTimeFormatPartTypes) => (
    Number(parts.find(candidate => candidate.type === type)?.value ?? '0')
  )
  return part('hour') * 60 + part('minute')
}

export function timeToMinutes(value: string | null | undefined): number | null {
  if (!value) return null
  const [hour, minute] = value.split(':').map(Number)
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return null
  return hour * 60 + minute
}

export function minutesToLabel(value: number): string {
  const normalized = ((value % 1440) + 1440) % 1440
  const hour = Math.floor(normalized / 60)
  const minute = normalized % 60
  return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`
}

export function minutesToTime(value: number): string {
  return `${minutesToLabel(value)}:00`
}

export function formatRange(start: string): string {
  const first = parseLocalDate(start)
  const last = parseLocalDate(addDays(start, 6))
  const firstLabel = new Intl.DateTimeFormat('zh-CN', {
    month: 'long',
    day: 'numeric',
  }).format(first)
  const lastLabel = new Intl.DateTimeFormat('zh-CN', {
    month: 'long',
    day: 'numeric',
  }).format(last)
  return `${firstLabel} — ${lastLabel}`
}

export function weekdayLabels(): string[] {
  return ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
}

export function weekdayShortLabels(): string[] {
  return ['一', '二', '三', '四', '五', '六', '日']
}

export function makeWeek(start: string, today: string): TimetableDay[] {
  const labels = weekdayLabels()
  return Array.from({ length: 7 }, (_, index) => {
    const date = addDays(start, index)
    const parsed = parseLocalDate(date)
    return {
      date,
      weekday: labels[index],
      dayNumber: parsed.getDate(),
      month: parsed.getMonth() + 1,
      isToday: date === today,
    }
  })
}

export function colorIndexFor(value: string): number {
  let hash = 0
  for (const char of value) {
    hash = (hash * 31 + char.codePointAt(0)!) % 360
  }
  return hash % EVENT_COLORS.length
}

export function eventTimeLabel(event: BoardEvent): string {
  const start = minutesToLabel(event.startMin)
  const end = minutesToLabel(event.endMin)
  return event.endMin > event.startMin ? `${start}–${end}` : start
}
