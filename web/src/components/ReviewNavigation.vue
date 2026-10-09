<script setup lang="ts">
import { BrainCircuit, CalendarPlus, FileClock } from 'lucide-vue-next'
import type { ReviewSummary } from '../lib/reviews'

defineProps<{
  view: 'review' | 'memories'
  selectedDate: string
  maximumDate: string
  selectedReviewId: string | null
  reviews: ReviewSummary[]
  loading: boolean
}>()

const emit = defineEmits<{
  view: [value: 'review' | 'memories']
  'update:selectedDate': [value: string]
  open: []
  select: [review: ReviewSummary]
}>()
</script>

<template>
  <nav class="secondary-nav review-sidebar" aria-label="复盘">
    <div class="review-view-switch" role="tablist" aria-label="复盘视图">
      <button
        type="button"
        role="tab"
        :aria-selected="view === 'review'"
        :class="{ selected: view === 'review' }"
        @click="emit('view', 'review')"
      >
        <FileClock :size="17" aria-hidden="true" />
        每日复盘
      </button>
      <button
        type="button"
        role="tab"
        :aria-selected="view === 'memories'"
        :class="{ selected: view === 'memories' }"
        @click="emit('view', 'memories')"
      >
        <BrainCircuit :size="17" aria-hidden="true" />
        长期记忆
      </button>
    </div>

    <section v-if="view === 'review'" class="review-date-navigation" aria-labelledby="review-date-title">
      <h2 id="review-date-title">选择活动日</h2>
      <div class="review-date-picker">
        <input
          :value="selectedDate"
          type="date"
          :max="maximumDate"
          aria-label="复盘活动日"
          @input="emit('update:selectedDate', ($event.target as HTMLInputElement).value)"
        />
        <button
          type="button"
          class="icon-button"
          title="打开这一天"
          aria-label="打开这一天"
          :disabled="loading || !selectedDate"
          @click="emit('open')"
        >
          <CalendarPlus :size="18" aria-hidden="true" />
        </button>
      </div>

      <h2>最近复盘</h2>
      <p v-if="loading && !reviews.length" class="review-sidebar-empty" role="status">
        正在读取...
      </p>
      <p v-else-if="!reviews.length" class="review-sidebar-empty">还没有复盘记录。</p>
      <ol v-else class="review-date-list">
        <li v-for="item in reviews" :key="item.id">
          <button
            type="button"
            :class="{ selected: selectedReviewId === item.id }"
            @click="emit('select', item)"
          >
            <span>{{ item.activity_date }}</span>
            <small>
              {{ item.latest_report_version
                ? `${item.report_count} 个报告版本`
                : item.question_count ? `已追问 ${item.question_count} 次` : '尚未生成报告' }}
            </small>
          </button>
        </li>
      </ol>
    </section>
  </nav>
</template>
