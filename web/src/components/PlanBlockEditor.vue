<script setup lang="ts">
import { ArrowDown, ArrowUp, Clock3, ListChecks } from 'lucide-vue-next'
import { computed } from 'vue'
import type { PlanBlock, PlanningTaskCandidate } from '../lib/plans'

const props = defineProps<{
  block: PlanBlock
  candidate: PlanningTaskCandidate | null
  editable: boolean
  pending: boolean
  firstInDay: boolean
  lastInDay: boolean
}>()

const emit = defineEmits<{
  time: [field: 'local_starts_at' | 'local_ends_at', value: string]
  step: [stepId: string, selected: boolean]
  move: [direction: -1 | 1]
}>()

const duration = computed(() => {
  const [startHour, startMinute] = props.block.local_starts_at.split(':').map(Number)
  const [endHour, endMinute] = props.block.local_ends_at.split(':').map(Number)
  return endHour * 60 + endMinute - startHour * 60 - startMinute
})

function inputTime(
  field: 'local_starts_at' | 'local_ends_at',
  event: Event,
) {
  emit('time', field, (event.target as HTMLInputElement).value)
}
</script>

<template>
  <article class="plan-block" :class="{ editing: editable }">
    <div class="plan-block-time">
      <Clock3 :size="15" aria-hidden="true" />
      <template v-if="editable">
        <label>
          <span class="sr-only">{{ block.task_title_snapshot }} 开始时间</span>
          <input
            type="time"
            step="900"
            :value="block.local_starts_at.slice(0, 5)"
            :disabled="pending"
            :aria-label="`${block.task_title_snapshot} 开始时间`"
            @input="inputTime('local_starts_at', $event)"
          />
        </label>
        <span aria-hidden="true">至</span>
        <label>
          <span class="sr-only">{{ block.task_title_snapshot }} 结束时间</span>
          <input
            type="time"
            step="900"
            :value="block.local_ends_at.slice(0, 5)"
            :disabled="pending"
            :aria-label="`${block.task_title_snapshot} 结束时间`"
            @input="inputTime('local_ends_at', $event)"
          />
        </label>
      </template>
      <span v-else>
        {{ block.local_starts_at.slice(0, 5) }}–{{ block.local_ends_at.slice(0, 5) }}
      </span>
    </div>

    <div class="plan-block-copy">
      <strong>{{ block.task_title_snapshot }}</strong>
      <p>
        {{ duration }} 分钟
        <span v-if="block.estimate_source === 'ai'">· AI 估时 {{ block.estimated_minutes }} 分钟</span>
      </p>
      <p v-if="block.step_titles_snapshot.length && !editable" class="plan-block-steps">
        <ListChecks :size="14" aria-hidden="true" />
        {{ block.step_titles_snapshot.join('、') }}
      </p>
      <details v-if="editable && candidate?.steps.length" class="plan-step-picker">
        <summary>选择本时段处理的步骤</summary>
        <label
          v-for="step in candidate.steps"
          :key="step.id"
          class="checkbox-label"
        >
          <input
            type="checkbox"
            :checked="block.step_ids.includes(step.id)"
            :disabled="pending || step.completed"
            @change="emit('step', step.id, ($event.target as HTMLInputElement).checked)"
          />
          <span :class="{ completed: step.completed }">{{ step.title }}</span>
        </label>
      </details>
    </div>

    <div v-if="editable" class="plan-block-order" aria-label="调整日内顺序">
      <button
        type="button"
        class="small-icon-button"
        :disabled="pending || firstInDay"
        :aria-label="`上移 ${block.task_title_snapshot}`"
        :title="`上移 ${block.task_title_snapshot}`"
        @click="emit('move', -1)"
      >
        <ArrowUp :size="17" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="small-icon-button"
        :disabled="pending || lastInDay"
        :aria-label="`下移 ${block.task_title_snapshot}`"
        :title="`下移 ${block.task_title_snapshot}`"
        @click="emit('move', 1)"
      >
        <ArrowDown :size="17" aria-hidden="true" />
      </button>
    </div>
  </article>
</template>
