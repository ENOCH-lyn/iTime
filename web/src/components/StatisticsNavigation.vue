<script setup lang="ts">
import {
  ChevronLeft,
  ChevronRight,
  LocateFixed,
} from 'lucide-vue-next'
import type { StatisticsScope } from '../lib/statistics'

defineProps<{
  scope: StatisticsScope
  rangeLabel: string
  canMoveNext: boolean
  loading: boolean
}>()

const emit = defineEmits<{
  scope: [value: StatisticsScope]
  previous: []
  current: []
  next: []
}>()
</script>

<template>
  <nav class="secondary-nav statistics-sidebar" aria-label="统计">
    <section class="statistics-range-controls" aria-labelledby="statistics-range-title">
      <h2 id="statistics-range-title">统计范围</h2>
      <div class="statistics-scope-switch" role="tablist" aria-label="统计周期">
        <button
          v-for="item in ([
            ['day', '日'],
            ['week', '周'],
            ['month', '月'],
          ] as const)"
          :key="item[0]"
          type="button"
          role="tab"
          :aria-selected="scope === item[0]"
          :class="{ selected: scope === item[0] }"
          :disabled="loading"
          @click="emit('scope', item[0])"
        >
          {{ item[1] }}
        </button>
      </div>
      <p class="statistics-range-label">{{ rangeLabel }}</p>
      <div class="statistics-range-actions">
        <button
          type="button"
          class="icon-button"
          title="上一范围"
          aria-label="上一范围"
          :disabled="loading"
          @click="emit('previous')"
        >
          <ChevronLeft :size="19" aria-hidden="true" />
        </button>
        <button
          type="button"
          class="icon-button"
          title="回到当前"
          aria-label="回到当前"
          :disabled="loading"
          @click="emit('current')"
        >
          <LocateFixed :size="18" aria-hidden="true" />
        </button>
        <button
          type="button"
          class="icon-button"
          title="下一范围"
          aria-label="下一范围"
          :disabled="loading || !canMoveNext"
          @click="emit('next')"
        >
          <ChevronRight :size="19" aria-hidden="true" />
        </button>
      </div>
    </section>
  </nav>
</template>
