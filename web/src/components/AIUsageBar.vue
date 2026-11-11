<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { Gauge } from 'lucide-vue-next'
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
const amount = (value: string) => {
  const currency = current.value?.currency ?? ''
  const numeric = Number(value)
  const text = Number.isFinite(numeric) ? numeric.toFixed(2) : value
  return `${currency} ${text}`.trim()
}
const resetTime = computed(() => {
  if (!current.value) return ''
  const value = new Date(current.value.window_end_at)
  if (Number.isNaN(value.getTime())) return ''
  return value.toLocaleString('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
})

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
  <div class="usage-meter">
    <p v-if="loading" class="muted" role="status">正在读取用量…</p>
    <p v-else-if="error" class="muted">{{ error.message }}</p>
    <p v-else-if="!summary?.configured" class="muted">AI 用量暂未配置。</p>
    <template v-else-if="current">
      <div class="usage-head">
        <span class="usage-icon" aria-hidden="true">
          <Gauge :size="18" />
        </span>
        <div class="usage-copy">
          <strong>今日可用额度</strong>
          <p>
            {{ resetTime ? `当前周期至 ${resetTime}` : '按日额度' }}
            · 每日 04:00 刷新
          </p>
        </div>
        <div class="usage-remaining">
          <span class="usage-remaining-value">{{ amount(current.available) }}</span>
          <span class="usage-remaining-label">剩余</span>
        </div>
      </div>
      <div class="usage-bar-row">
        <div
          class="usage-track"
          role="progressbar"
          aria-label="今日 AI 用量"
          :aria-valuenow="percent"
          aria-valuemin="0"
          aria-valuemax="100"
          :aria-valuetext="`已用 ${amount(current.charged)}，额度 ${amount(current.base)}`"
        >
          <span
            class="usage-fill"
            :class="{ 'usage-fill--high': percent >= 80 }"
            :style="{ width: `${percent}%` }"
          ></span>
        </div>
        <span class="usage-figures">
          <span class="usage-charged">{{ amount(current.charged) }}</span>
          <span class="usage-slash">/</span>
          <span>{{ amount(current.base) }}</span>
        </span>
      </div>
      <div class="usage-foot">
        <span>{{ percent }}% 已用</span>
        <span v-if="Number(current.reserved) > 0">预留 {{ amount(current.reserved) }}</span>
        <span v-if="Number(current.adjustment) !== 0">调整 {{ amount(current.adjustment) }}</span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.usage-meter {
  display: grid;
  gap: 14px;
  padding: 20px;
}

.usage-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.usage-icon {
  display: grid;
  flex: 0 0 38px;
  place-items: center;
  width: 38px;
  height: 38px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-primary-container);
  color: var(--m3-on-primary-container);
}

.usage-copy {
  flex: 1 1 auto;
  min-width: 0;
}
.usage-copy strong {
  display: block;
  color: var(--m3-on-surface);
  font: 500 14px/20px var(--font);
}
.usage-copy p {
  margin: 2px 0 0;
  color: var(--m3-on-surface-variant);
  font: 400 12px/16px var(--font);
}

.usage-remaining {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 1px;
}
.usage-remaining-value {
  color: var(--m3-on-surface);
  font: 500 20px/26px var(--font);
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.2px;
}
.usage-remaining-label {
  color: var(--m3-on-surface-variant);
  font: 400 11px/14px var(--font);
}

.usage-bar-row {
  display: flex;
  align-items: center;
  gap: 14px;
}

.usage-track {
  position: relative;
  flex: 1 1 auto;
  height: 10px;
  overflow: hidden;
  border-radius: var(--m3-shape-full);
  background: var(--m3-surface-container-high);
}

.usage-fill {
  position: absolute;
  inset: 0 auto 0 0;
  min-width: 0;
  border-radius: inherit;
  background: linear-gradient(
    90deg,
    var(--m3-primary) 0%,
    color-mix(in srgb, var(--m3-primary) 78%, var(--m3-tertiary, var(--m3-primary))) 100%
  );
  transition: width 220ms ease;
}
.usage-fill--high {
  background: linear-gradient(
    90deg,
    var(--m3-tertiary, var(--m3-primary)) 0%,
    var(--m3-error) 100%
  );
}

.usage-figures {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  color: var(--m3-on-surface-variant);
  font: 400 12px/16px var(--font);
  font-variant-numeric: tabular-nums;
}
.usage-charged {
  color: var(--m3-on-surface);
  font-weight: 500;
}
.usage-slash {
  opacity: 0.55;
}

.usage-foot {
  display: flex;
  align-items: center;
  gap: 14px;
  color: var(--m3-on-surface-variant);
  font: 400 11.5px/16px var(--font);
}

.muted {
  margin: 0;
  color: var(--m3-on-surface-variant);
  font: 400 13px/18px var(--font);
}

@media (max-width: 600px) {
  .usage-meter {
    padding: 16px;
  }
  .usage-head {
    align-items: flex-start;
  }
  .usage-remaining-value {
    font-size: 17px;
    line-height: 22px;
  }
  .usage-bar-row {
    flex-direction: column;
    align-items: stretch;
    gap: 8px;
  }
  .usage-figures {
    justify-content: flex-end;
  }
}
</style>
