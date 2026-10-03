<script setup lang="ts">
import { computed } from 'vue'
import { formatDuration, type StatisticsDay, type StatisticsScope } from '../lib/statistics'

const props = defineProps<{
  days: StatisticsDay[]
  scope: StatisticsScope
}>()

const planningMax = computed(() => Math.max(
  0,
  ...props.days.map(day => day.planning.planned_seconds),
))
const taskMax = computed(() => Math.max(
  0,
  ...props.days.map(
    day => day.tasks.completed_count + day.tasks.skipped_occurrence_count,
  ),
))
const focusMax = computed(() => Math.max(
  0,
  ...props.days.map(day => day.focus.total_seconds),
))

function percentage(value: number, maximum: number) {
  if (!value || !maximum) return '0%'
  return `${Math.max(4, value / maximum * 100)}%`
}

function dayLabel(value: string) {
  const parsed = new Date(`${value}T00:00:00Z`)
  if (props.scope === 'month') return String(parsed.getUTCDate())
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    weekday: props.scope === 'week' ? 'short' : undefined,
    timeZone: 'UTC',
  }).format(parsed)
}

function shortDuration(seconds: number) {
  if (!seconds) return '0'
  if (seconds < 60) return '<1'
  return String(Math.round(seconds / 60))
}
</script>

<template>
  <section class="statistics-trends" aria-labelledby="statistics-trends-title">
    <div class="statistics-section-heading">
      <div>
        <span class="statistics-eyebrow">逐活动日</span>
        <h2 id="statistics-trends-title">变化趋势</h2>
      </div>
      <p>每组使用自己的量纲。</p>
    </div>

    <section class="statistics-chart" data-kind="planning">
      <header>
        <div>
          <h3>计划分钟</h3>
          <p>当前计划版本中与活动日相交的时间。</p>
        </div>
      </header>
      <div class="statistics-chart-scroll">
        <ol
          class="statistics-chart-points"
          :style="{ '--point-count': Math.max(days.length, 1) }"
        >
          <li
            v-for="day in days"
            :key="`planning-${day.activity_date}`"
            :aria-label="`${day.activity_date}，计划 ${formatDuration(day.planning.planned_seconds)}`"
          >
            <span class="statistics-chart-value">
              {{ shortDuration(day.planning.planned_seconds) }}
            </span>
            <span class="statistics-chart-track" aria-hidden="true">
              <span
                class="statistics-chart-bar"
                :style="{ height: percentage(day.planning.planned_seconds, planningMax) }"
              />
            </span>
            <time :datetime="day.activity_date">{{ dayLabel(day.activity_date) }}</time>
          </li>
        </ol>
      </div>
    </section>

    <section class="statistics-chart" data-kind="tasks">
      <header>
        <div>
          <h3>任务结果</h3>
          <p>实色为完成，描边为跳过。</p>
        </div>
      </header>
      <div class="statistics-chart-scroll">
        <ol
          class="statistics-chart-points"
          :style="{ '--point-count': Math.max(days.length, 1) }"
        >
          <li
            v-for="day in days"
            :key="`tasks-${day.activity_date}`"
            :aria-label="`${day.activity_date}，完成 ${day.tasks.completed_count} 项，跳过 ${day.tasks.skipped_occurrence_count} 项`"
          >
            <span class="statistics-chart-value">
              {{ day.tasks.completed_count }} / {{ day.tasks.skipped_occurrence_count }}
            </span>
            <span class="statistics-chart-track task-track" aria-hidden="true">
              <span
                class="statistics-chart-bar task-complete"
                :style="{ height: percentage(day.tasks.completed_count, taskMax) }"
              />
              <span
                class="statistics-chart-bar task-skipped"
                :style="{ height: percentage(day.tasks.skipped_occurrence_count, taskMax) }"
              />
            </span>
            <time :datetime="day.activity_date">{{ dayLabel(day.activity_date) }}</time>
          </li>
        </ol>
      </div>
    </section>

    <section class="statistics-chart" data-kind="focus">
      <header>
        <div>
          <h3>专注分钟</h3>
          <p>完成、放弃与进行中的有效时长合计。</p>
        </div>
      </header>
      <div class="statistics-chart-scroll">
        <ol
          class="statistics-chart-points"
          :style="{ '--point-count': Math.max(days.length, 1) }"
        >
          <li
            v-for="day in days"
            :key="`focus-${day.activity_date}`"
            :aria-label="`${day.activity_date}，专注 ${formatDuration(day.focus.total_seconds)}`"
          >
            <span class="statistics-chart-value">
              {{ shortDuration(day.focus.total_seconds) }}
            </span>
            <span class="statistics-chart-track" aria-hidden="true">
              <span
                class="statistics-chart-bar"
                :style="{ height: percentage(day.focus.total_seconds, focusMax) }"
              />
            </span>
            <time :datetime="day.activity_date">{{ dayLabel(day.activity_date) }}</time>
          </li>
        </ol>
      </div>
    </section>
  </section>
</template>
