<script setup lang="ts">
import { Clock3, Pencil } from 'lucide-vue-next'
import type { FocusSession } from '../lib/execution'

defineProps<{
  items: FocusSession[]
  selectedId: string | null
  loading: boolean
}>()

const emit = defineEmits<{
  select: [focus: FocusSession]
  revise: [focus: FocusSession]
}>()

function durationLabel(seconds: number | null) {
  if (seconds === null) return '仍在计时'
  if (seconds < 60) return `${seconds} 秒`
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return hours ? `${hours} 小时 ${minutes} 分钟` : `${minutes} 分钟`
}

function dateLabel(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}
</script>

<template>
  <section class="focus-history" aria-labelledby="focus-history-title">
    <header class="focus-section-heading">
      <div>
        <h2 id="focus-history-title">最近记录</h2>
        <p>按实际开始时间排列</p>
      </div>
    </header>
    <p v-if="loading" class="focus-empty" role="status">正在读取记录...</p>
    <p v-else-if="!items.length" class="focus-empty">还没有专注记录。</p>
    <ul v-else class="focus-history-list">
      <li
        v-for="item in items"
        :key="item.id"
        :class="{ selected: selectedId === item.id }"
      >
        <button type="button" class="focus-history-select" @click="emit('select', item)">
          <Clock3 :size="17" aria-hidden="true" />
          <span>
            <strong>{{ item.title }}</strong>
            <small>
              {{ dateLabel(item.effective_started_at ?? item.started_at) }}
              · {{ durationLabel(item.effective_duration_seconds) }}
              · {{ item.status === 'abandoned' ? '已放弃' : item.status === 'finished' ? '已结束' : item.status === 'paused' ? '已暂停' : '进行中' }}
            </small>
          </span>
        </button>
        <button
          v-if="['finished', 'abandoned'].includes(item.status)"
          type="button"
          class="icon-button"
          title="修订记录"
          aria-label="修订记录"
          @click="emit('revise', item)"
        >
          <Pencil :size="17" aria-hidden="true" />
        </button>
        <span v-if="item.revised" class="focus-revised">已修订</span>
      </li>
    </ul>
  </section>
</template>
