<script setup lang="ts">
import { CheckCircle2 } from 'lucide-vue-next'
import { computed } from 'vue'
import type { FocusSession } from '../lib/execution'

const props = defineProps<{
  focus: FocusSession
  pending: boolean
}>()

const emit = defineEmits<{
  progress: [completedStepIds: string[]]
  complete: []
}>()

const completedIds = computed(() => (
  props.focus.steps.filter(step => step.completed).map(step => step.id)
))

function toggle(stepId: string, completed: boolean) {
  const next = new Set(completedIds.value)
  if (completed) next.add(stepId)
  else next.delete(stepId)
  emit('progress', props.focus.steps.filter(step => next.has(step.id)).map(step => step.id))
}
</script>

<template>
  <section v-if="focus.task_id" class="focus-progress" aria-labelledby="focus-progress-title">
    <header class="focus-section-heading">
      <div>
        <h2 id="focus-progress-title">任务步骤</h2>
        <p>{{ completedIds.length }} / {{ focus.steps.length }} 步</p>
      </div>
      <span class="focus-task-state">
        {{ focus.task_status === 'completed' ? '任务已完成' : '任务未完成' }}
      </span>
    </header>

    <ul v-if="focus.steps.length" class="focus-step-list">
      <li v-for="step in focus.steps" :key="step.id">
        <label class="checkbox-label">
          <input
            type="checkbox"
            :checked="step.completed"
            :disabled="pending || !['running', 'paused'].includes(focus.status)"
            @change="toggle(step.id, ($event.target as HTMLInputElement).checked)"
          />
          <span>{{ step.title }}</span>
        </label>
      </li>
    </ul>
    <p v-else class="focus-empty">此任务没有步骤。</p>

    <button
      v-if="['finished', 'abandoned'].includes(focus.status) && focus.task_status === 'open'"
      type="button"
      class="button secondary focus-complete-task"
      :disabled="pending"
      @click="emit('complete')"
    >
      <CheckCircle2 :size="18" aria-hidden="true" />
      完成任务
    </button>
  </section>
</template>
