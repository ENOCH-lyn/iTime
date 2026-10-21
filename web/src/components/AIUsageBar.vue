<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { getQuota, type QuotaSummary } from '../lib/quota'
import { failure, type Failure } from '../lib/api'

const summary = ref<QuotaSummary | null>(null)
const error = ref<Failure | null>(null)
const loading = ref(true)
const current = computed(() => summary.value?.current ?? null)
const percent = computed(() => {
  if (!current.value) return 0
  const base = Number(current.value.base)
  const charged = Number(current.value.charged)
  if (!Number.isFinite(base) || base <= 0) return 0
  return Math.min(100, Math.max(0, Math.round((charged / base) * 100)))
})
const amount = (value: string) => `${current.value?.currency ?? ''} ${value}`.trim()

onMounted(async () => {
  try {
    summary.value = await getQuota()
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="ai-usage">
    <p v-if="loading" class="muted" role="status">正在读取用量…</p>
    <p v-else-if="error" class="muted">{{ error.message }}</p>
    <p v-else-if="!summary?.configured" class="muted">AI 用量暂未配置。</p>
    <template v-else-if="current">
      <div class="ai-usage-heading">
        <strong>今日 AI 用量</strong>
        <span>{{ amount(current.charged) }} / {{ amount(current.base) }}</span>
      </div>
      <div
        class="ai-usage-track"
        role="progressbar"
        aria-label="今日 AI 用量"
        :aria-valuenow="percent"
        aria-valuemin="0"
        aria-valuemax="100"
      >
        <span :style="{ width: `${percent}%` }"></span>
      </div>
      <p class="field-hint">剩余 {{ amount(current.available) }}</p>
    </template>
  </div>
</template>

<style scoped>
.ai-usage {
  display: grid;
  gap: 8px;
}
.ai-usage-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
}
.ai-usage-heading span {
  color: var(--muted);
  font-size: .86rem;
}
.ai-usage-track {
  height: 8px;
  overflow: hidden;
  border-radius: 999px;
  background: var(--surface-soft);
}
.ai-usage-track span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: var(--primary);
}
</style>
