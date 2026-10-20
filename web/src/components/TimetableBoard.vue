<script setup lang="ts">
import { computed } from 'vue'
import {
  EVENT_COLORS,
  eventTimeLabel,
  minutesToLabel,
  type BoardEvent,
  type TimetableDay,
} from '../lib/timetable'

const HOUR_HEIGHT = 68

const props = defineProps<{
  days: TimetableDay[]
  items: BoardEvent[]
  rangeStart: number
  rangeEnd: number
  nowMinutes: number | null
  loading: boolean
}>()

const emit = defineEmits<{
  select: [event: BoardEvent]
  create: [dayIndex: number, startMinutes: number]
}>()

interface PositionedEvent extends BoardEvent {
  top: number
  height: number
  left: string
  width: string
  timeLabel: string
}

const totalMinutes = computed(() => Math.max(60, props.rangeEnd - props.rangeStart))
const boardHeight = computed(() => (totalMinutes.value / 60) * HOUR_HEIGHT)

const hourMarks = computed(() => {
  const marks: number[] = []
  const first = Math.ceil(props.rangeStart / 60) * 60
  for (let minute = first; minute <= props.rangeEnd; minute += 60) {
    marks.push(minute)
  }
  return marks
})

const positioned = computed<PositionedEvent[]>(() => {
  const output: PositionedEvent[] = []
  for (let dayIndex = 0; dayIndex < props.days.length; dayIndex += 1) {
    const dayItems = props.items
      .filter(item => item.dayIndex === dayIndex)
      .sort((left, right) => (
        left.startMin - right.startMin || left.endMin - right.endMin
      ))
    const laneEnds: number[] = []
    const lanes = new Map<string, number>()
    for (const item of dayItems) {
      const end = Math.max(item.endMin, item.startMin + 15)
      let lane = laneEnds.findIndex(laneEnd => laneEnd <= item.startMin)
      if (lane === -1) {
        lane = laneEnds.length
        laneEnds.push(end)
      } else {
        laneEnds[lane] = end
      }
      lanes.set(item.id, lane)
    }
    const laneCount = Math.max(1, laneEnds.length)
    for (const item of dayItems) {
      const lane = lanes.get(item.id) ?? 0
      const start = Math.min(Math.max(item.startMin, props.rangeStart), props.rangeEnd)
      const end = Math.min(Math.max(item.endMin, start + 15), props.rangeEnd)
      output.push({
        ...item,
        top: ((start - props.rangeStart) / 60) * HOUR_HEIGHT,
        height: Math.max(30, ((end - start) / 60) * HOUR_HEIGHT - 4),
        left: `calc(${(lane * 100) / laneCount}% + 2px)`,
        width: `calc(${100 / laneCount}% - 4px)`,
        timeLabel: eventTimeLabel(item),
      })
    }
  }
  return output
})

const nowTop = computed(() => {
  if (props.nowMinutes === null) return null
  if (props.nowMinutes < props.rangeStart || props.nowMinutes > props.rangeEnd) return null
  return ((props.nowMinutes - props.rangeStart) / 60) * HOUR_HEIGHT
})

function eventsForDay(dayIndex: number) {
  return positioned.value.filter(item => item.dayIndex === dayIndex)
}

function createAt(dayIndex: number, event: MouseEvent) {
  const target = event.currentTarget as HTMLElement | null
  if (!target) return
  const rect = target.getBoundingClientRect()
  const offset = Math.max(0, Math.min(rect.height, event.clientY - rect.top))
  const raw = props.rangeStart + (offset / rect.height) * totalMinutes.value
  const snapped = Math.min(
    props.rangeEnd - 30,
    Math.max(props.rangeStart, Math.round(raw / 15) * 15),
  )
  emit('create', dayIndex, snapped)
}

function colorFor(event: BoardEvent) {
  return EVENT_COLORS[event.colorIndex] ?? EVENT_COLORS[0]
}

function hourTop(mark: number) {
  return Math.max(9, ((mark - props.rangeStart) / 60) * HOUR_HEIGHT)
}
</script>

<template>
  <div class="board-scroll">
    <div class="board" :style="{ '--hour-height': `${HOUR_HEIGHT}px` }">
      <div class="board-head">
        <div class="board-corner" aria-hidden="true">时间</div>
        <div
          v-for="day in days"
          :key="day.date"
          class="board-day-head"
          :class="{ today: day.isToday }"
        >
          <span class="board-weekday">{{ day.weekday }}</span>
          <span class="board-date">
            {{ day.dayNumber }}
            <small v-if="day.dayNumber === 1">{{ day.month }}月</small>
          </span>
        </div>
      </div>

      <div
        class="board-body"
        :style="{ height: `${boardHeight}px` }"
        :aria-busy="loading"
      >
        <div class="board-axis" aria-hidden="true">
          <span
            v-for="mark in hourMarks"
            :key="mark"
            class="board-hour"
            :style="{ top: `${hourTop(mark)}px` }"
          >
            {{ minutesToLabel(mark) }}
          </span>
        </div>

        <div
          v-for="(day, dayIndex) in days"
          :key="day.date"
          class="board-day"
          :class="{ today: day.isToday }"
          @click="createAt(dayIndex, $event)"
        >
          <div class="board-lines" aria-hidden="true" />
          <button
            v-for="event in eventsForDay(dayIndex)"
            :key="event.id"
            type="button"
            class="board-event"
            :class="[`kind-${event.kind}`, { cancelled: event.cancelled }]"
            :style="{
              top: `${event.top}px`,
              height: `${event.height}px`,
              left: event.left,
              width: event.width,
              '--event-color': colorFor(event),
            }"
            :aria-label="`${event.timeLabel} ${event.title}`"
            @click.stop="emit('select', event)"
          >
            <span class="board-event-time">{{ event.timeLabel }}</span>
            <strong>{{ event.title }}</strong>
            <small v-if="event.subtitle">{{ event.subtitle }}</small>
          </button>
          <div
            v-if="day.isToday && nowTop !== null"
            class="board-now"
            :style="{ top: `${nowTop}px` }"
            aria-hidden="true"
          >
            <span />
          </div>
        </div>

        <div v-if="loading" class="board-loading" role="status">
          <span class="board-loading-dot" />正在读取课表
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.board-scroll {
  overflow-x: auto;
  overflow-y: hidden;
  border-radius: 20px;
  background: var(--surface);
  border: 1px solid var(--border);
  box-shadow: 0 1px 2px rgb(31 42 36 / 4%), 0 16px 40px -32px rgb(31 42 36 / 30%);
  scrollbar-width: thin;
}
.board {
  min-width: 760px;
  position: relative;
}
.board-head {
  display: grid;
  grid-template-columns: 58px repeat(7, minmax(0, 1fr));
  position: sticky;
  top: 0;
  z-index: 4;
  background: color-mix(in srgb, var(--surface) 94%, transparent);
  backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--border);
}
.board-corner {
  display: flex;
  align-items: flex-end;
  justify-content: center;
  padding: 12px 0 10px;
  font-size: 11px;
  letter-spacing: .08em;
  color: var(--muted);
}
.board-day-head {
  padding: 12px 8px 10px;
  text-align: center;
  border-left: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
}
.board-day-head.today {
  background: color-mix(in srgb, var(--accent) 7%, transparent);
}
.board-weekday {
  display: block;
  font-size: 12px;
  color: var(--muted);
}
.board-date {
  display: inline-flex;
  align-items: baseline;
  gap: 3px;
  margin-top: 2px;
  font-size: 18px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
  letter-spacing: -.02em;
}
.board-date small {
  font-size: 10px;
  font-weight: 500;
  color: var(--muted);
}
.board-day-head.today .board-date {
  color: var(--accent);
}
.board-body {
  position: relative;
  display: grid;
  grid-template-columns: 58px repeat(7, minmax(0, 1fr));
}
.board-axis {
  position: relative;
  height: 100%;
}
.board-hour {
  position: absolute;
  right: 10px;
  transform: translateY(-50%);
  font-size: 11px;
  color: var(--muted);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.board-day {
  position: relative;
  height: 100%;
  border-left: 1px solid color-mix(in srgb, var(--border) 60%, transparent);
  cursor: pointer;
}
.board-day.today {
  background: color-mix(in srgb, var(--accent) 4%, transparent);
}
.board-lines {
  position: absolute;
  inset: 0;
  pointer-events: none;
  background-image: repeating-linear-gradient(
    to bottom,
    color-mix(in srgb, var(--border) 55%, transparent) 0,
    color-mix(in srgb, var(--border) 55%, transparent) 1px,
    transparent 1px,
    transparent var(--hour-height)
  );
}
.board-event {
  position: absolute;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 5px 7px;
  overflow: hidden;
  border: 0;
  border-left: 3px solid var(--event-color);
  border-radius: 9px;
  background: color-mix(in srgb, var(--event-color) 14%, var(--surface));
  color: var(--text);
  text-align: left;
  line-height: 1.25;
  transition: transform .12s ease, box-shadow .12s ease;
}
.board-event:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 18px -10px rgb(31 42 36 / 45%);
  z-index: 3;
}
.board-event:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 1px;
}
.board-event-time {
  font-size: 10px;
  font-weight: 600;
  color: color-mix(in srgb, var(--event-color) 72%, var(--text));
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
:global(:root[data-theme="dark"]) .board-event-time {
  color: color-mix(in srgb, var(--event-color) 40%, #f1f7f2);
}
.board-event strong {
  font-size: 12px;
  font-weight: 600;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.board-event small {
  font-size: 10px;
  color: var(--muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.board-event.cancelled {
  opacity: .45;
  text-decoration: line-through;
}
.board-event.kind-task {
  border-left-style: dashed;
  background: color-mix(in srgb, var(--muted) 10%, var(--surface));
}
.board-event.kind-plan {
  border-left-color: color-mix(in srgb, var(--accent) 70%, #7a5a8f);
  background: color-mix(in srgb, #775b8f 11%, var(--surface));
  border-left-style: double;
}
.board-now {
  position: absolute;
  left: 0;
  right: 0;
  z-index: 3;
  height: 2px;
  background: #cf5b47;
  pointer-events: none;
}
.board-now span {
  position: absolute;
  left: -4px;
  top: -3px;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #cf5b47;
}
.board-loading {
  position: absolute;
  inset: 0;
  z-index: 6;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  gap: 8px;
  padding-top: 32px;
  background: color-mix(in srgb, var(--surface) 70%, transparent);
  font-size: 13px;
  color: var(--muted);
  border-radius: 20px;
}
.board-loading-dot {
  width: 8px;
  height: 8px;
  margin-top: 6px;
  border-radius: 50%;
  background: var(--accent);
  animation: pulse 1.1s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: .3; transform: scale(.8); }
  50% { opacity: 1; transform: scale(1); }
}
@media (max-width: 720px) {
  .board { min-width: 620px; }
  .board-head { grid-template-columns: 44px repeat(7, minmax(0, 1fr)); }
  .board-body { grid-template-columns: 44px repeat(7, minmax(0, 1fr)); }
  .board-hour { right: 6px; font-size: 10px; }
  .board-event { padding: 4px 5px; }
  .board-event strong { font-size: 11px; }
}
</style>
