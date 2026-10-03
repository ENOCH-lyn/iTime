<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import {
  Clock3,
  Image,
  MessageCircleMore,
  ReceiptText,
  RefreshCw,
} from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import { failure, type Failure } from '../lib/api'
import { refreshSession } from '../lib/auth'
import { getTimeSettings, saveTimeSettings } from '../lib/calendar'
import {
  getQuota,
  listQuotaLedger,
  type QuotaLedgerEntry,
  type QuotaLedgerKind,
  type QuotaLedgerPage,
  type QuotaSummary,
} from '../lib/quota'

const root = ref<HTMLElement | null>(null)
const summary = ref<QuotaSummary | null>(null)
const ledger = ref<QuotaLedgerEntry[]>([])
const cursor = ref<QuotaLedgerPage['next_cursor']>(null)
const draftKind = ref<QuotaLedgerKind | 'all'>('all')
const appliedKind = ref<QuotaLedgerKind | 'all'>('all')
const summaryLoading = ref(false)
const ledgerLoading = ref(false)
const ledgerLoaded = ref(false)
const summaryError = ref<Failure | null>(null)
const ledgerError = ref<Failure | null>(null)
let timeZoneOperationId = ''

const current = computed(() => summary.value?.current ?? null)
const refreshing = computed(() => summaryLoading.value || ledgerLoading.value)
const capabilityLabels = {
  conversation: '全局对话',
  calendar_screenshot: '截图识别',
} as const
const kindLabels: Record<QuotaLedgerKind, string> = {
  usage_charge: 'AI 使用',
  admin_credit: '管理员增加',
  admin_debit: '管理员扣减',
}
const usageLabels = {
  provider: '供应商上报',
  estimated: '平台估算',
} as const

function money(currency: string, amount: string) {
  return `${currency} ${amount}`
}

function activityDate(value: string) {
  const [year, month, day] = value.split('-')
  return `${year}年${Number(month)}月${Number(day)}日`
}

function timeInZone(value: string, timeZone: string) {
  try {
    return new Intl.DateTimeFormat('zh-CN', {
      timeZone,
      month: 'long',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).format(new Date(value))
  } catch {
    return new Date(value).toLocaleString('zh-CN')
  }
}

function activityBoundary(minutes: number) {
  const hours = Math.floor(minutes / 60).toString().padStart(2, '0')
  const remainder = (minutes % 60).toString().padStart(2, '0')
  return `${hours}:${remainder}`
}

async function focusError() {
  await nextTick()
  root.value?.querySelector<HTMLElement>('.error-status')?.focus()
}

async function handleAuthError(currentError: Failure) {
  if (
    ['unauthenticated', 'invalid_csrf', 'account_suspended'].includes(
      currentError.code,
    )
  ) {
    await refreshSession()
  }
}

async function initializeTimeZone() {
  const settings = await getTimeSettings()
  if (settings.initialized) return
  const browserTimeZone = Intl.DateTimeFormat().resolvedOptions().timeZone
  if (!timeZoneOperationId) timeZoneOperationId = crypto.randomUUID()
  await saveTimeSettings(settings, browserTimeZone, timeZoneOperationId)
  timeZoneOperationId = ''
}

async function loadSummary() {
  if (summaryLoading.value) return
  summaryLoading.value = true
  summaryError.value = null
  try {
    summary.value = await getQuota()
  } catch (reason) {
    let currentError = failure(reason)
    if (currentError.code === 'time_settings_required') {
      try {
        await initializeTimeZone()
        summary.value = await getQuota()
        return
      } catch (timeReason) {
        currentError = failure(timeReason)
      }
    }
    summaryError.value = currentError
    await handleAuthError(currentError)
    await focusError()
  } finally {
    summaryLoading.value = false
  }
}

async function loadLedger(more = false) {
  if (ledgerLoading.value) return
  ledgerLoading.value = true
  ledgerError.value = null
  try {
    const requestKind = more ? appliedKind.value : draftKind.value
    const page = await listQuotaLedger(
      requestKind,
      more ? cursor.value : null,
    )
    ledger.value = more ? [...ledger.value, ...page.items] : page.items
    cursor.value = page.next_cursor
    if (!more) appliedKind.value = requestKind
    ledgerLoaded.value = true
  } catch (reason) {
    const currentError = failure(reason)
    ledgerError.value = currentError
    await handleAuthError(currentError)
    await focusError()
  } finally {
    ledgerLoading.value = false
  }
}

async function refresh() {
  await Promise.all([loadSummary(), loadLedger()])
}

function refreshOnFocus() {
  void loadSummary()
}

onMounted(() => {
  void refresh()
  window.addEventListener('focus', refreshOnFocus)
})
onUnmounted(() => window.removeEventListener('focus', refreshOnFocus))
</script>

<template>
  <div ref="root" class="quota-overview">
    <div class="section-intro">
      <p class="page-description">
        查看本活动日的可用额度、处理中预留和已结算记录。
      </p>
      <button
        type="button"
        class="button secondary compact-button"
        :disabled="refreshing"
        @click="refresh"
      >
        <RefreshCw :size="16" aria-hidden="true" />
        {{ refreshing ? '正在刷新…' : '刷新' }}
      </button>
    </div>

    <StatusMessage
      :error="summaryError"
      :retry-label="summaryError?.retryable ? '重新读取' : undefined"
      @retry="loadSummary"
    />
    <p v-if="summaryLoading && !summary" role="status" class="muted">
      正在读取额度…
    </p>
    <div
      v-else-if="summary && !summary.configured"
      class="status restriction-status"
      role="status"
    >
      <Clock3 :size="20" aria-hidden="true" />
      <p>每日 AI 额度尚未配置，新的 AI 请求暂时不能接收。</p>
    </div>

    <template v-else-if="current">
      <dl class="quota-metrics" aria-label="本活动日额度摘要">
        <div>
          <dt>今日可用</dt>
          <dd data-testid="quota-available">
            <span>{{ current.currency }}</span>{{ current.available }}
          </dd>
        </div>
        <div>
          <dt>已扣</dt>
          <dd data-testid="quota-charged">
            <span>{{ current.currency }}</span>{{ current.charged }}
          </dd>
        </div>
        <div>
          <dt>处理中</dt>
          <dd data-testid="quota-reserved">
            <span>{{ current.currency }}</span>{{ current.reserved }}
          </dd>
        </div>
        <div>
          <dt>每日基础</dt>
          <dd data-testid="quota-base">
            <span>{{ current.currency }}</span>{{ current.base }}
          </dd>
        </div>
      </dl>

      <dl class="quota-context">
        <div>
          <dt>活动日</dt>
          <dd>{{ activityDate(current.activity_date) }}</dd>
        </div>
        <div>
          <dt>冻结时区</dt>
          <dd class="wrap-anywhere">{{ current.time_zone }}</dd>
        </div>
        <div>
          <dt>刷新边界</dt>
          <dd>当地 {{ activityBoundary(current.activity_day_start_minute) }}</dd>
        </div>
        <div>
          <dt>下次刷新</dt>
          <dd>{{ timeInZone(current.window_end_at, current.time_zone) }}</dd>
        </div>
      </dl>

      <section class="quota-section" aria-labelledby="quota-reservations-title">
        <div class="quota-section-heading">
          <div>
            <h2 id="quota-reservations-title">处理中预留</h2>
            <p>作业完成后按实际成功成本结算，失败或取消会释放。</p>
          </div>
          <span class="state-label" data-state="pending">
            {{ summary?.reservations.length ?? 0 }} 项
          </span>
        </div>
        <ul
          v-if="summary?.reservations.length"
          class="quota-list"
          aria-label="处理中额度预留"
        >
          <li
            v-for="reservation in summary.reservations"
            :key="reservation.id"
          >
            <component
              :is="reservation.capability === 'conversation'
                ? MessageCircleMore
                : Image"
              :size="19"
              aria-hidden="true"
            />
            <div>
              <strong>{{ capabilityLabels[reservation.capability] }}</strong>
              <p>
                {{ timeInZone(reservation.accepted_at, current.time_zone) }}
                · 作业 <span class="wrap-anywhere">{{ reservation.job_id }}</span>
              </p>
            </div>
            <span class="quota-list-amount">
              {{ money(current.currency, reservation.reserved) }}
            </span>
          </li>
        </ul>
        <p v-else class="empty-state">当前没有处理中的额度预留。</p>
      </section>
    </template>

    <section class="quota-section" aria-labelledby="quota-ledger-title">
      <div class="quota-section-heading">
        <div>
          <h2 id="quota-ledger-title">额度账本</h2>
          <p>成功用量与管理员调整按发生时间记录，不包含失败重试的平台成本。</p>
        </div>
      </div>
      <StatusMessage
        :error="ledgerError"
        :retry-label="ledgerError?.retryable ? '重新读取' : undefined"
        @retry="loadLedger()"
      />
      <form class="quota-ledger-tools" @submit.prevent="loadLedger()">
        <div class="field">
          <label for="quota-ledger-kind">记录类型</label>
          <select
            id="quota-ledger-kind"
            v-model="draftKind"
            :disabled="ledgerLoading"
          >
            <option value="all">全部记录</option>
            <option value="usage_charge">AI 使用</option>
            <option value="admin_credit">管理员增加</option>
            <option value="admin_debit">管理员扣减</option>
          </select>
        </div>
        <button
          type="submit"
          class="button secondary compact-button"
          :disabled="ledgerLoading"
        >
          <ReceiptText :size="16" aria-hidden="true" />
          筛选
        </button>
      </form>
      <ul v-if="ledger.length" class="quota-list" aria-label="额度账本记录">
        <li v-for="entry in ledger" :key="entry.id">
          <ReceiptText :size="19" aria-hidden="true" />
          <div>
            <div class="identity-line">
              <strong>{{ kindLabels[entry.kind] }}</strong>
              <span
                v-if="entry.usage_source"
                class="state-label"
                :data-state="entry.usage_source"
              >
                {{ usageLabels[entry.usage_source] }}
              </span>
            </div>
            <p>
              {{ activityDate(entry.activity_date) }}
              · {{ timeInZone(entry.created_at, current?.time_zone ?? 'UTC') }}
            </p>
            <p v-if="entry.job" class="wrap-anywhere">
              {{ capabilityLabels[entry.job.capability] }} · 作业 {{ entry.job.id }}
            </p>
          </div>
          <span
            class="quota-list-amount"
            :data-direction="entry.delta.startsWith('-') ? 'debit' : 'credit'"
          >
            {{ money(entry.currency, entry.delta) }}
          </span>
        </li>
      </ul>
      <p v-if="ledgerLoading && !ledgerLoaded" role="status" class="muted">
        正在读取额度账本…
      </p>
      <p v-else-if="ledgerLoaded && !ledger.length" class="empty-state">
        暂无额度账本记录。
      </p>
      <button
        v-if="cursor && draftKind === appliedKind"
        type="button"
        class="button secondary"
        :disabled="ledgerLoading"
        @click="loadLedger(true)"
      >
        加载更多
      </button>
    </section>
  </div>
</template>
