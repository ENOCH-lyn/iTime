<script setup lang="ts">
import { CircleStop, Pause, Play, X } from 'lucide-vue-next'
import { computed } from 'vue'
import type { FocusSession, FocusTransition } from '../lib/execution'

const props = defineProps<{
  focus: FocusSession
  elapsedSeconds: number
  pending: boolean
}>()

const emit = defineEmits<{
  transition: [action: FocusTransition]
}>()

const displayedSeconds = computed(() => {
  if (props.focus.mode === 'countdown' && props.focus.target_seconds !== null) {
    return Math.abs(props.focus.target_seconds - props.elapsedSeconds)
  }
  return props.elapsedSeconds
})

const timeLabel = computed(() => {
  const total = Math.max(0, Math.floor(displayedSeconds.value))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  return [hours, minutes, seconds].map(value => String(value).padStart(2, '0')).join(':')
})

const stateLabel = computed(() => {
  if (props.focus.status === 'paused') return '已暂停'
  if (props.focus.status === 'finished') return '已结束'
  if (props.focus.status === 'abandoned') return '已放弃'
  if (
    props.focus.mode === 'countdown'
    && props.focus.target_seconds !== null
    && props.elapsedSeconds >= props.focus.target_seconds
  ) return '已超出目标'
  return '进行中'
})
</script>

<template>
  <section class="focus-timer" aria-labelledby="focus-timer-title">
    <header class="focus-timer-heading">
      <div>
        <div class="focus-state-line">
          <p class="focus-state" :data-state="focus.status">{{ stateLabel }}</p>
          <span v-if="focus.revised" class="focus-revised-main">已修订</span>
        </div>
        <h1 id="focus-timer-title">{{ focus.title }}</h1>
      </div>
      <span v-if="focus.attention_required" class="focus-attention">请核对持续时间</span>
    </header>

    <p class="focus-time" aria-live="off">
      <span v-if="focus.mode === 'countdown' && focus.target_seconds !== null && elapsedSeconds >= focus.target_seconds">+</span>{{ timeLabel }}
    </p>
    <p class="focus-time-caption">
      {{ focus.mode === 'countdown'
        ? elapsedSeconds >= (focus.target_seconds ?? 0) ? '超出目标时间' : '剩余时间'
        : '本次专注时间' }}
    </p>

    <div v-if="['running', 'paused'].includes(focus.status)" class="focus-timer-actions">
      <button
        v-if="focus.status === 'running'"
        type="button"
        class="icon-command"
        title="暂停"
        aria-label="暂停"
        :disabled="pending"
        @click="emit('transition', 'pause')"
      >
        <Pause :size="22" aria-hidden="true" />
      </button>
      <button
        v-else
        type="button"
        class="icon-command primary"
        title="继续"
        aria-label="继续"
        :disabled="pending"
        @click="emit('transition', 'resume')"
      >
        <Play :size="22" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="icon-command"
        title="结束"
        aria-label="结束"
        :disabled="pending"
        @click="emit('transition', 'finish')"
      >
        <CircleStop :size="22" aria-hidden="true" />
      </button>
      <button
        type="button"
        class="icon-command danger"
        title="放弃"
        aria-label="放弃"
        :disabled="pending"
        @click="emit('transition', 'abandon')"
      >
        <X :size="22" aria-hidden="true" />
      </button>
    </div>
  </section>
</template>
