<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Save } from 'lucide-vue-next'
import type { ReviewDetail } from '../lib/reviews'

const props = defineProps<{
  detail: ReviewDetail
  pending: boolean
}>()

const emit = defineEmits<{ save: [content: string] }>()
const draft = ref(props.detail.review.supplemental_facts)

watch(
  () => props.detail.review.supplemental_facts,
  value => { draft.value = value },
)

const focusTime = computed(() => {
  const minutes = Math.round(props.detail.facts.focus.total_seconds / 60)
  if (minutes < 60) return `${minutes} 分钟`
  const hours = Math.floor(minutes / 60)
  const remainder = minutes % 60
  return remainder ? `${hours} 小时 ${remainder} 分钟` : `${hours} 小时`
})

function localTime(value: string) {
  return new Date(value).toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
}
</script>

<template>
  <section class="review-facts" aria-labelledby="review-facts-title">
    <header class="review-section-heading">
      <div>
        <p class="review-eyebrow">当日记录</p>
        <h2 id="review-facts-title">事实概览</h2>
      </div>
      <span class="review-zone">{{ detail.facts.time_zone }} · 04:00 切换</span>
    </header>

    <dl class="review-fact-metrics">
      <div>
        <dt>计划块</dt>
        <dd>{{ detail.facts.planned_blocks.items.length }}</dd>
      </div>
      <div>
        <dt>完成任务</dt>
        <dd>{{ detail.facts.task_completions.items.length }}</dd>
      </div>
      <div>
        <dt>专注</dt>
        <dd>{{ focusTime }}</dd>
      </div>
      <div>
        <dt>困难</dt>
        <dd>{{ detail.facts.difficulties.items.length }}</dd>
      </div>
    </dl>

    <details class="review-fact-detail">
      <summary>查看记录明细</summary>
      <div class="review-fact-columns">
        <section>
          <h3>计划与完成</h3>
          <ul v-if="detail.facts.planned_blocks.items.length">
            <li v-for="item in detail.facts.planned_blocks.items" :key="item.source_key">
              <strong>{{ item.title }}</strong>
              <span>{{ item.starts_at.slice(0, 5) }}—{{ item.ends_at.slice(0, 5) }}</span>
            </li>
          </ul>
          <p v-else class="review-empty">这一天没有已确认计划。</p>
          <ul v-if="detail.facts.task_completions.items.length">
            <li v-for="item in detail.facts.task_completions.items" :key="item.source_key">
              <strong>{{ item.title }}</strong>
              <span>{{ localTime(item.completed_at) }} 完成</span>
            </li>
          </ul>
        </section>
        <section>
          <h3>专注与困难</h3>
          <ul v-if="detail.facts.focus.items.length">
            <li v-for="item in detail.facts.focus.items" :key="item.source_key">
              <strong>{{ item.title }}</strong>
              <span>{{ Math.round(item.activity_seconds / 60) }} 分钟</span>
            </li>
          </ul>
          <ul v-if="detail.facts.difficulties.items.length">
            <li v-for="item in detail.facts.difficulties.items" :key="item.source_key">
              <strong>{{ item.category || '困难记录' }}</strong>
              <span>{{ item.content }}</span>
            </li>
          </ul>
          <p
            v-if="!detail.facts.focus.items.length && !detail.facts.difficulties.items.length"
            class="review-empty"
          >
            这一天没有专注或困难记录。
          </p>
        </section>
      </div>
    </details>

    <form class="review-facts-form" @submit.prevent="emit('save', draft)">
      <label for="review-supplemental-facts">补充事实</label>
      <textarea
        id="review-supplemental-facts"
        v-model="draft"
        maxlength="8000"
        rows="4"
        :disabled="pending"
        placeholder="写下未记录在计划、任务或专注中的实际情况"
      />
      <div class="review-form-footer">
        <span>{{ draft.length }}/8000</span>
        <button
          type="submit"
          class="button secondary"
          :disabled="pending || draft.trim() === detail.review.supplemental_facts"
        >
          <Save :size="17" aria-hidden="true" />
          保存事实
        </button>
      </div>
    </form>
  </section>
</template>
