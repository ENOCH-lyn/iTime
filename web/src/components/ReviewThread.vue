<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { FileText, MessageCircleQuestion, Square } from 'lucide-vue-next'
import type { AIJob } from '../lib/ai-jobs'
import type { ReviewMessage, ReviewSession } from '../lib/reviews'

const props = defineProps<{
  review: ReviewSession
  messages: ReviewMessage[]
  activeJob: AIJob | null
  pending: boolean
}>()

const emit = defineEmits<{
  question: [answer: string | null]
  report: []
  cancel: []
}>()

const answer = ref('')
const lastMessageId = computed(() => props.messages.at(-1)?.id ?? '')
const awaitingAnswer = computed(() => props.messages.at(-1)?.role === 'assistant')
const canAsk = computed(() => (
  !props.activeJob
  && !props.pending
  && props.review.question_count < 3
  && (!awaitingAnswer.value || !!answer.value.trim())
))

watch(lastMessageId, () => { answer.value = '' })

function ask() {
  emit('question', awaitingAnswer.value ? answer.value.trim() : null)
}
</script>

<template>
  <section class="review-thread" aria-labelledby="review-thread-title">
    <header class="review-section-heading">
      <div>
        <p class="review-eyebrow">可选 · {{ review.question_count }}/3</p>
        <h2 id="review-thread-title">简短追问</h2>
      </div>
    </header>

    <ol v-if="messages.length" class="review-message-list" aria-label="复盘追问">
      <li v-for="message in messages" :key="message.id" :data-role="message.role">
        <span>{{ message.role === 'assistant' ? 'iTime' : '我' }}</span>
        <p>{{ message.content }}</p>
      </li>
    </ol>
    <p v-else class="review-empty">可以先回答一两个问题，也可以直接生成报告。</p>

    <label v-if="awaitingAnswer && review.question_count < 3" for="review-answer">
      回答当前问题
    </label>
    <textarea
      v-if="awaitingAnswer && review.question_count < 3"
      id="review-answer"
      v-model="answer"
      rows="3"
      maxlength="8000"
      :disabled="pending || !!activeJob"
    />

    <div v-if="activeJob" class="review-job-state" role="status">
      <span>
        {{ activeJob.input_summary.purpose === 'question'
          ? '正在准备下一个问题…'
          : '正在生成复盘报告…' }}
      </span>
      <button type="button" class="button secondary" @click="emit('cancel')">
        <Square :size="15" aria-hidden="true" />
        取消
      </button>
    </div>

    <div v-else class="review-thread-actions">
      <button
        v-if="review.question_count < 3"
        type="button"
        class="button secondary"
        :disabled="!canAsk"
        @click="ask"
      >
        <MessageCircleQuestion :size="17" aria-hidden="true" />
        {{ awaitingAnswer ? '回答并继续' : '开始追问' }}
      </button>
      <button
        type="button"
        class="button"
        :disabled="pending"
        @click="emit('report')"
      >
        <FileText :size="17" aria-hidden="true" />
        {{ review.question_count ? '生成报告' : '跳过，生成报告' }}
      </button>
    </div>
  </section>
</template>
