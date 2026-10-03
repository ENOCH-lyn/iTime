<script setup lang="ts">
import {
  CalendarClock,
  CircleAlert,
  Clock3,
  ListChecks,
  Sparkles,
} from 'lucide-vue-next'
import type {
  PlanBlock,
  PlanUnscheduledItem,
  PlanningFixedOccurrence,
  PlanningTaskCandidate,
} from '../lib/plans'
import PlanBlockEditor from './PlanBlockEditor.vue'

const props = defineProps<{
  days: string[]
  blocks: PlanBlock[]
  fixedOccurrences: PlanningFixedOccurrence[]
  unscheduledItems: PlanUnscheduledItem[]
  candidates: PlanningTaskCandidate[]
  editable: boolean
  pending: boolean
}>()

const emit = defineEmits<{
  blockTime: [
    index: number,
    field: 'local_starts_at' | 'local_ends_at',
    value: string,
  ]
  blockStep: [index: number, stepId: string, selected: boolean]
  blockMove: [index: number, direction: -1 | 1]
}>()

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

function blocksFor(day: string) {
  return props.blocks
    .map((block, index) => ({ block, index }))
    .filter(item => item.block.local_date === day)
    .sort((left, right) => (
      left.block.local_starts_at.localeCompare(right.block.local_starts_at)
      || left.block.position - right.block.position
    ))
}

function fixedFor(day: string) {
  return props.fixedOccurrences.filter(item => item.local_start_date === day)
}

function candidateFor(block: PlanBlock) {
  return props.candidates.find(candidate => (
    candidate.task_id === block.task_id
    && candidate.occurrence_key === block.occurrence_key
  )) ?? null
}

function fixedTime(item: PlanningFixedOccurrence) {
  if (!item.local_start_time) return '全天'
  const start = item.local_start_time.slice(0, 5)
  const end = item.local_end_time?.slice(0, 5)
  return end ? `${start}–${end}` : start
}
</script>

<template>
  <div class="plan-preview">
    <section
      v-for="day in days"
      :key="day"
      class="plan-day"
      :aria-labelledby="`plan-day-${day}`"
    >
      <header>
        <h2 :id="`plan-day-${day}`">{{ dayTitle(day) }}</h2>
        <span>
          {{ blocksFor(day).reduce((total, item) => total
            + (Number(item.block.local_ends_at.slice(0, 2)) * 60
              + Number(item.block.local_ends_at.slice(3, 5)))
            - (Number(item.block.local_starts_at.slice(0, 2)) * 60
              + Number(item.block.local_starts_at.slice(3, 5))), 0) }} 分钟
        </span>
      </header>

      <div v-if="fixedFor(day).length" class="plan-fixed-list" aria-label="固定占用">
        <div v-for="item in fixedFor(day)" :key="item.occurrence_key" class="plan-fixed-item">
          <CalendarClock :size="15" aria-hidden="true" />
          <span>{{ fixedTime(item) }}</span>
          <strong>{{ item.name }}</strong>
        </div>
      </div>

      <div v-if="blocksFor(day).length" class="plan-day-blocks">
        <PlanBlockEditor
          v-for="(item, dayIndex) in blocksFor(day)"
          :key="item.block.id ?? `${item.block.task_id}-${item.block.occurrence_key}-${item.index}`"
          :block="item.block"
          :candidate="candidateFor(item.block)"
          :editable="editable"
          :pending="pending"
          :first-in-day="dayIndex === 0"
          :last-in-day="dayIndex === blocksFor(day).length - 1"
          @time="(field, value) => emit('blockTime', item.index, field, value)"
          @step="(stepId, selected) => emit('blockStep', item.index, stepId, selected)"
          @move="direction => emit('blockMove', item.index, direction)"
        />
      </div>
      <div v-else class="plan-day-empty">
        <Clock3 :size="18" aria-hidden="true" />
        <p>这一天没有任务时段。</p>
      </div>
    </section>

    <section v-if="unscheduledItems.length" class="plan-unscheduled" aria-labelledby="unscheduled-heading">
      <header>
        <CircleAlert :size="19" aria-hidden="true" />
        <div>
          <h2 id="unscheduled-heading">暂未排入</h2>
          <p>这些任务仍保留，只是当前条件下没有合适时段。</p>
        </div>
      </header>
      <ul>
        <li v-for="item in unscheduledItems" :key="item.id ?? `${item.task_id}-${item.occurrence_key}`">
          <ListChecks :size="16" aria-hidden="true" />
          <div>
            <strong>{{ item.task_title_snapshot }}</strong>
            <p>{{ item.remaining_minutes }} 分钟 · {{ item.explanation }}</p>
          </div>
        </li>
      </ul>
    </section>

    <div v-if="!blocks.length && !unscheduledItems.length" class="plan-preview-empty">
      <Sparkles :size="28" aria-hidden="true" />
      <h2>还没有计划内容</h2>
      <p>选择任务并生成提案，或查看这一周已有的计划。</p>
    </div>
  </div>
</template>
