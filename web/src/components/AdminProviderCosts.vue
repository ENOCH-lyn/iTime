<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { Calculator, RefreshCw, Search } from 'lucide-vue-next'
import FormField from './FormField.vue'
import StatusMessage from './StatusMessage.vue'
import { failure, type Failure } from '../lib/api'
import { refreshAdminSession } from '../lib/admin'
import {
  listProviderCosts,
  type ProviderCost,
  type ProviderCostStatus,
  type ProviderUsageSource,
} from '../lib/admin-costs'
import type { AIConfigCapability } from '../lib/admin-ai'
import { localDate, type Page } from '../lib/admin-management'

const items = ref<ProviderCost[]>([])
const cursor = ref<Page<ProviderCost>['next_cursor']>(null)
const search = ref('')
const capability = ref<'all' | AIConfigCapability>('all')
const status = ref<'all' | ProviderCostStatus>('all')
const usageSource = ref<'all' | ProviderUsageSource>('all')
const pricing = ref<'all' | 'priced' | 'unpriced'>('all')
const loading = ref(false)
const loaded = ref(false)
const error = ref<Failure | null>(null)

const capabilityLabels: Record<AIConfigCapability, string> = {
  calendar_screenshot: '截图识别',
  conversation: '全局对话',
  planning: '日周规划',
  review: '每日复盘',
  transcription: '语音转写',
}
const statusLabels: Record<ProviderCostStatus, string> = {
  succeeded: '成功',
  retryable_failed: '等待重试',
  failed: '失败',
  timed_out: '超时',
  cancelled: '已取消',
  abandoned: '租约失效',
}
const usageLabels: Record<ProviderUsageSource, string> = {
  provider: '供应商上报',
  estimated: '平台估算',
  measured: '平台实测',
  not_sent: '未发出请求',
}
const number = new Intl.NumberFormat('zh-CN')

function durationLabel(durationMs: number) {
  if (durationMs < 1000) return `${number.format(durationMs)} 毫秒`
  return `${new Intl.NumberFormat('zh-CN', {
    maximumFractionDigits: 3,
  }).format(durationMs / 1000)} 秒`
}

async function handleError(reason: unknown) {
  const current = failure(reason)
  if (['admin_unauthenticated', 'invalid_csrf'].includes(current.code)) {
    await refreshAdminSession()
  }
  await nextTick()
  document.querySelector<HTMLElement>('.provider-cost-settings .error-status')?.focus()
  return current
}

async function load(more = false) {
  if (loading.value) return
  loading.value = true
  error.value = null
  try {
    const query = new URLSearchParams({ limit: '30' })
    if (search.value.trim()) query.set('search', search.value.trim())
    if (capability.value !== 'all') query.set('capability', capability.value)
    if (status.value !== 'all') query.set('attempt_status', status.value)
    if (usageSource.value !== 'all') query.set('usage_source', usageSource.value)
    if (pricing.value !== 'all') {
      query.set('priced', String(pricing.value === 'priced'))
    }
    if (more && cursor.value) {
      query.set('before_time', cursor.value.before_time)
      query.set('before_id', cursor.value.before_id)
    }
    const page = await listProviderCosts(query)
    items.value = more ? [...items.value, ...page.items] : page.items
    cursor.value = page.next_cursor
    loaded.value = true
  } catch (reason) {
    error.value = await handleError(reason)
  } finally {
    loading.value = false
  }
}

onMounted(() => void load())
</script>

<template>
  <div class="governance-settings provider-cost-settings">
    <div class="section-intro">
      <div>
        <p class="page-description">
          每次供应商尝试独立记录。失败和重试成本由平台承担，不代表用户额度扣减。
        </p>
      </div>
      <button type="button" class="button secondary compact-button"
        :disabled="loading" @click="load()">
        <RefreshCw :size="16" aria-hidden="true" />刷新
      </button>
    </div>
    <StatusMessage :error="error" :retry-label="error?.retryable ? '重新读取' : undefined"
      @retry="load()" />
    <form class="management-filters cost-filters" role="search"
      @submit.prevent="load()">
      <FormField id="provider-cost-search" v-model="search" label="用户邮箱"
        type="text" autocomplete="off" :maxlength="254" :disabled="loading" />
      <div class="field">
        <label for="provider-cost-capability">能力</label>
        <select id="provider-cost-capability" v-model="capability" :disabled="loading">
          <option value="all">全部</option>
          <option value="conversation">全局对话</option>
          <option value="calendar_screenshot">截图识别</option>
          <option value="planning">日周规划</option>
          <option value="review">每日复盘</option>
          <option value="transcription">语音转写</option>
        </select>
      </div>
      <div class="field">
        <label for="provider-cost-status">尝试状态</label>
        <select id="provider-cost-status" v-model="status" :disabled="loading">
          <option value="all">全部</option>
          <option v-for="(label, value) in statusLabels" :key="value" :value="value">
            {{ label }}
          </option>
        </select>
      </div>
      <div class="field">
        <label for="provider-cost-source">用量来源</label>
        <select id="provider-cost-source" v-model="usageSource" :disabled="loading">
          <option value="all">全部</option>
          <option v-for="(label, value) in usageLabels" :key="value" :value="value">
            {{ label }}
          </option>
        </select>
      </div>
      <div class="field">
        <label for="provider-cost-pricing">金额状态</label>
        <select id="provider-cost-pricing" v-model="pricing" :disabled="loading">
          <option value="all">全部</option>
          <option value="priced">已计价</option>
          <option value="unpriced">未配置价格</option>
        </select>
      </div>
      <button class="button filter-submit" type="submit" :disabled="loading">
        <Search :size="17" aria-hidden="true" />筛选
      </button>
    </form>

    <ul class="management-list governance-list provider-cost-list"
      aria-label="AI 供应商成本">
      <li v-for="item in items" :key="item.id">
        <div class="management-primary">
          <div class="identity-line">
            <strong class="wrap-anywhere">{{ item.owner.email }}</strong>
            <span class="state-label" :data-state="item.attempt_status">
              {{ statusLabels[item.attempt_status] }}
            </span>
            <span class="state-label">{{ capabilityLabels[item.capability] }}</span>
            <span class="state-label">{{ usageLabels[item.usage_source] }}</span>
          </div>
          <p>
            <strong v-if="item.amounts && item.pricing" class="cost-amount">
              {{ item.pricing.currency }} {{ item.amounts.total }}
            </strong>
            <strong v-else class="cost-unpriced">未配置价格</strong>
            <template v-if="item.billing_basis === 'audio_minute'">
              · 音频 {{ durationLabel(item.usage.audio_duration_ms) }}
              <template v-if="item.pricing">
                · 单价 {{ item.pricing.audio_per_minute }} / 分钟
              </template>
            </template>
            <template v-else>
              · 输入 {{ number.format(item.usage.input_tokens) }}
              <template v-if="item.usage.cached_input_tokens">
                （缓存 {{ number.format(item.usage.cached_input_tokens) }}）
              </template>
              · 输出 {{ number.format(item.usage.output_tokens) }} Token
            </template>
          </p>
          <p>
            {{ item.config.model }} · 配置版本 {{ item.config.version }}
            · 尝试 {{ item.attempt_number }} · {{ localDate(item.created_at) }}
          </p>
          <p class="wrap-anywhere">
            作业 {{ item.job_id }}
            <template v-if="item.provider_request_id">
              · 上游请求 {{ item.provider_request_id }}
            </template>
            <template v-if="item.error_code"> · {{ item.error_code }}</template>
          </p>
        </div>
        <Calculator :size="20" aria-hidden="true" class="provider-cost-icon" />
      </li>
    </ul>
    <p v-if="loading && !loaded" role="status" class="muted">正在读取供应商成本…</p>
    <p v-else-if="loaded && !items.length" class="empty-state">没有符合条件的供应商成本。</p>
    <button v-if="cursor" type="button" class="button secondary"
      :disabled="loading" @click="load(true)">加载更多</button>
  </div>
</template>
