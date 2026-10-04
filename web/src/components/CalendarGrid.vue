<script setup lang="ts">
import { AlertTriangle, CheckCircle2, Clock3, ListChecks, MapPin } from 'lucide-vue-next'
import { computed } from 'vue'
import type { CalendarDisplayItem } from '../lib/calendar'

const props = defineProps<{
  days: string[]
  items: CalendarDisplayItem[]
  view: 'week' | 'month'
  monthKey: string
  today: string
  selectedId: string | null
  loading: boolean
}>()

const emit = defineEmits<{ select: [item: CalendarDisplayItem] }>()
const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

function parseDate(value: string) {
  const [year, month, day] = value.split('-').map(Number)
  return new Date(year, month - 1, day, 12)
}

function dayTitle(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'long',
    day: 'numeric',
    weekday: 'long',
  }).format(parseDate(value))
}

function shortDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
  }).format(parseDate(value))
}

function itemsFor(day: string) {
  return props.items.filter(item => item.date === day)
}

function timeLabel(item: CalendarDisplayItem) {
  if (!item.time) return '全天'
  const start = item.time.slice(0, 5)
  const end = item.end_time?.slice(0, 5)
  return end ? `${start}–${end}${item.end_date !== item.date ? ' +1' : ''}` : start
}

const visibleDays = computed(() => props.days.filter(day => (
  props.view === 'week' || itemsFor(day).length || day === props.today
)))
</script>

<template>
  <div v-if="loading" class="calendar-loading" role="status" aria-live="polite">
    <span v-for="number in view === 'week' ? 7 : 14" :key="number" aria-hidden="true" />
    <p>正在读取日历…</p>
  </div>

  <template v-else>
    <section class="calendar-grid" :class="`calendar-${view}`" :aria-label="view === 'week' ? '周日历' : '月日历'">
      <div v-for="weekday in weekdays" :key="weekday" class="calendar-weekday" aria-hidden="true">
        {{ weekday }}
      </div>
      <article
        v-for="day in days"
        :key="day"
        class="calendar-day"
        :class="{
          today: day === today,
          outside: view === 'month' && !day.startsWith(monthKey),
        }"
      >
        <header>
          <span class="calendar-day-name">{{ weekdays[parseDate(day).getDay() === 0 ? 6 : parseDate(day).getDay() - 1] }}</span>
          <time :datetime="day" :aria-label="dayTitle(day)">{{ parseDate(day).getDate() }}</time>
        </header>
        <div class="calendar-day-events">
          <button
            v-for="item in itemsFor(day)"
            :key="item.id"
            type="button"
            :data-event-id="item.id"
            class="calendar-event"
            :class="[
              `event-${item.kind}`,
              {
                selected: selectedId === item.id,
                cancelled: ['cancelled', 'source_cancelled', 'skipped'].includes(item.state),
              },
            ]"
            :aria-label="`${timeLabel(item)} ${item.title}${item.conflict ? '，存在时间冲突' : ''}`"
            @click="emit('select', item)"
          >
            <span class="event-time">{{ timeLabel(item) }}</span>
            <strong>{{ item.title }}</strong>
            <span v-if="view === 'week' && item.subtitle" class="event-subtitle">
              <ListChecks v-if="item.kind === 'plan'" :size="12" aria-hidden="true" />
              <MapPin v-else-if="item.kind !== 'task'" :size="12" aria-hidden="true" />
              {{ item.subtitle }}
            </span>
            <AlertTriangle v-if="item.conflict" class="event-alert" :size="14" aria-label="存在时间冲突" />
            <CheckCircle2 v-else-if="item.state === 'completed'" class="event-complete" :size="14" aria-label="已完成" />
          </button>
          <p v-if="view === 'week' && !itemsFor(day).length" class="calendar-day-empty">无安排</p>
        </div>
      </article>
    </section>

    <section class="calendar-agenda" aria-label="日程列表">
      <article v-for="day in visibleDays" :key="day" class="agenda-day">
        <header :class="{ today: day === today }">
          <time :datetime="day">{{ shortDate(day) }}</time>
          <span>{{ day === today ? '今天' : dayTitle(day).split('日')[1] }}</span>
        </header>
        <div class="agenda-events">
          <button
            v-for="item in itemsFor(day)"
            :key="item.id"
            type="button"
            :data-event-id="item.id"
            class="agenda-event"
            :class="[
              `event-${item.kind}`,
              {
                selected: selectedId === item.id,
                cancelled: ['cancelled', 'source_cancelled', 'skipped'].includes(item.state),
              },
            ]"
            :aria-label="`${timeLabel(item)} ${item.title}${item.conflict ? '，存在时间冲突' : ''}`"
            @click="emit('select', item)"
          >
            <span class="agenda-time"><Clock3 :size="15" aria-hidden="true" />{{ timeLabel(item) }}</span>
            <span class="agenda-copy">
              <strong>{{ item.title }}</strong>
              <small>{{ item.subtitle || (item.kind === 'task' ? '任务截止' : '无地点') }}</small>
            </span>
            <AlertTriangle v-if="item.conflict" :size="17" aria-label="存在时间冲突" />
            <CheckCircle2 v-else-if="item.state === 'completed'" :size="17" aria-label="已完成" />
          </button>
          <p v-if="!itemsFor(day).length" class="agenda-empty">没有安排</p>
        </div>
      </article>
    </section>
  </template>
</template>
