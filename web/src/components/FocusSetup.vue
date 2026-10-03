<script setup lang="ts">
import { Play } from 'lucide-vue-next'
import type { FocusMode } from '../lib/execution'
import type { TaskSummary } from '../lib/tasks'

defineProps<{
  tasks: TaskSummary[]
  selectedTaskId: string
  title: string
  mode: FocusMode
  targetMinutes: number
  pending: boolean
  scheduledBlockId: string | null
}>()

const emit = defineEmits<{
  'update:selectedTaskId': [value: string]
  'update:title': [value: string]
  'update:mode': [value: FocusMode]
  'update:targetMinutes': [value: number]
  start: []
}>()
</script>

<template>
  <section class="focus-setup" aria-labelledby="focus-setup-title">
    <header>
      <h1 id="focus-setup-title">专注</h1>
      <p>{{ scheduledBlockId ? '当前计划块' : '选择此刻要推进的内容' }}</p>
    </header>

    <div v-if="!scheduledBlockId" class="field">
      <label for="focus-task">关联任务 <span class="muted">可选</span></label>
      <select
        id="focus-task"
        :value="selectedTaskId"
        @change="emit('update:selectedTaskId', ($event.target as HTMLSelectElement).value)"
      >
        <option value="">不关联任务</option>
        <option v-for="task in tasks" :key="task.id" :value="task.id">
          {{ task.title }}
        </option>
      </select>
    </div>

    <div v-if="!selectedTaskId && !scheduledBlockId" class="field">
      <label for="focus-title">专注内容</label>
      <input
        id="focus-title"
        :value="title"
        maxlength="200"
        autocomplete="off"
        @input="emit('update:title', ($event.target as HTMLInputElement).value)"
      />
    </div>

    <fieldset class="segmented-field focus-mode">
      <legend>计时方式</legend>
      <label :class="{ selected: mode === 'stopwatch' }">
        <input
          :checked="mode === 'stopwatch'"
          type="radio"
          name="focus-mode"
          value="stopwatch"
          @change="emit('update:mode', 'stopwatch')"
        />
        正计时
      </label>
      <label :class="{ selected: mode === 'countdown' }">
        <input
          :checked="mode === 'countdown'"
          type="radio"
          name="focus-mode"
          value="countdown"
          @change="emit('update:mode', 'countdown')"
        />
        倒计时
      </label>
    </fieldset>

    <div v-if="mode === 'countdown'" class="field">
      <label for="focus-target-minutes">目标分钟</label>
      <input
        id="focus-target-minutes"
        :value="targetMinutes"
        type="number"
        min="1"
        max="1440"
        step="5"
        inputmode="numeric"
        @input="emit('update:targetMinutes', Number(($event.target as HTMLInputElement).value))"
      />
    </div>

    <button
      type="button"
      class="button focus-start-button"
      :disabled="pending"
      @click="emit('start')"
    >
      <Play :size="18" aria-hidden="true" />
      {{ pending ? '正在开始' : '开始专注' }}
    </button>
  </section>
</template>
