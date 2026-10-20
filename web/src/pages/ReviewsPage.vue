<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { MessageCircleMore, RefreshCcw } from 'lucide-vue-next'
import MemoryManager from '../components/MemoryManager.vue'
import ReviewFacts from '../components/ReviewFacts.vue'
import ReviewNavigation from '../components/ReviewNavigation.vue'
import ReviewReport from '../components/ReviewReport.vue'
import ReviewThread from '../components/ReviewThread.vue'
import StatusMessage from '../components/StatusMessage.vue'
import AppShell from '../shell/AppShell.vue'
import { useAppShell } from '../shell/useAppShell'
import { failure, type Failure } from '../lib/api'
import {
  identity,
  refreshSession,
} from '../lib/auth'
import { cancelAIJob, getAIJob, type AIJob } from '../lib/ai-jobs'
import { getTimeSettings, saveTimeSettings } from '../lib/calendar'
import {
  confirmMemoryProposal,
  createReview,
  createReviewAnnotation,
  deleteReviewAnnotation,
  getReview,
  getReviewReport,
  listReviews,
  rejectMemoryProposal,
  saveReviewFacts,
  updateReviewAnnotation,
  type MemoryProposal,
  type ReviewAnnotation,
  type ReviewDetail,
  type ReviewReportDetail,
  type ReviewSummary,
} from '../lib/reviews'
import { useSyncRefresh } from '../lib/use-sync-refresh'
import '../reviews.css'

const route = useRoute()
const router = useRouter()
const view = ref<'review' | 'memories'>(
  route.query.view === 'memories' ? 'memories' : 'review',
)
const reviews = ref<ReviewSummary[]>([])
const detail = ref<ReviewDetail | null>(null)
const report = ref<ReviewReportDetail | null>(null)
const selectedDate = ref('')
const maximumDate = ref('')
const loading = ref(true)
const pending = ref(false)
const { pendingLogout, logoutError, logout } = useAppShell()
const pageError = ref<Failure | null>(null)
const pageSuccess = ref('')
const activeJob = ref<AIJob | null>(null)
const memorySyncRevision = ref(0)
let pollTimer: number | null = null
let disposed = false
let pendingOperation: { fingerprint: string; id: string } | null = null

const selectedReviewId = computed(() => detail.value?.review.id ?? null)

function currentActivityDate(timeZone: string) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(new Date()).map(part => [part.type, part.value]),
  )
  const date = new Date(Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
  ))
  if (Number(parts.hour) < 4) date.setUTCDate(date.getUTCDate() - 1)
  return date.toISOString().slice(0, 10)
}

function operationId(fingerprint: string) {
  if (pendingOperation?.fingerprint !== fingerprint) {
    pendingOperation = { fingerprint, id: crypto.randomUUID() }
  }
  return pendingOperation.id
}

function finishOperation() {
  pendingOperation = null
}

function setError(reason: unknown) {
  pageError.value = failure(reason)
  void nextTick(() => {
    document.querySelector<HTMLElement>('.review-status .error-status')?.focus()
  })
}

async function replaceQuery() {
  await router.replace({
    path: '/reviews',
    query: {
      ...(view.value === 'memories' ? { view: 'memories' } : {}),
      ...(detail.value ? { date: detail.value.review.activity_date } : {}),
      ...(report.value ? { version: String(report.value.report.version) } : {}),
    },
  })
}

async function loadHistory() {
  reviews.value = (await listReviews()).items
}

async function loadReport(version?: number) {
  if (!detail.value?.reports.length) {
    report.value = null
    return
  }
  const requested = version
    ?? (typeof route.query.version === 'string' ? Number(route.query.version) : NaN)
  const selected = detail.value.reports.some(item => item.version === requested)
    ? requested
    : detail.value.reports[0].version
  report.value = await getReviewReport(detail.value.review.id, selected)
}

async function openReview(review: Pick<ReviewSummary, 'id' | 'activity_date'>) {
  loading.value = true
  pageError.value = null
  try {
    selectedDate.value = review.activity_date
    detail.value = await getReview(review.id)
    activeJob.value = detail.value.active_job
    await loadReport()
    await replaceQuery()
    if (activeJob.value) schedulePoll()
  } catch (reason) {
    setError(reason)
  } finally {
    loading.value = false
  }
}

async function openDate() {
  if (!selectedDate.value || pending.value) return
  pending.value = true
  pageError.value = null
  const fingerprint = `review:${selectedDate.value}`
  try {
    const result = await createReview(
      selectedDate.value,
      operationId(fingerprint),
    )
    finishOperation()
    await loadHistory()
    await openReview(result.review as ReviewSummary)
  } catch (reason) {
    setError(reason)
  } finally {
    pending.value = false
  }
}

async function initialize() {
  loading.value = true
  pageError.value = null
  try {
    let settings = await getTimeSettings()
    if (!settings.initialized) {
      const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
      settings = await saveTimeSettings(settings, zone)
    }
    await loadHistory()
    maximumDate.value = currentActivityDate(settings.time_zone!)
    selectedDate.value = typeof route.query.date === 'string'
      ? route.query.date
      : maximumDate.value
    const existing = reviews.value.find(
      item => item.activity_date === selectedDate.value,
    )
    if (existing) await openReview(existing)
    else await openDate()
  } catch (reason) {
    setError(reason)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  document.title = '复盘 · iTime'
  await refreshSession()
  if (!identity.value) {
    await router.replace('/login')
    return
  }
  window.addEventListener('focus', refreshOnFocus)
  await initialize()
})

onBeforeUnmount(() => {
  disposed = true
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  window.removeEventListener('focus', refreshOnFocus)
})

useSyncRefresh(
  [
    'review_session',
    'review_report',
    'review_annotation',
    'memory',
    'memory_settings',
    'task',
    'task_occurrence',
    'plan',
    'focus_session',
    'difficulty',
  ],
  async events => {
    if (events.some(event => ['memory', 'memory_settings'].includes(event.entity_type))) {
      memorySyncRevision.value += 1
    }
    await loadHistory()
    if (!pending.value && detail.value) await refreshOnFocus()
  },
)

async function refreshOnFocus() {
  if (view.value !== 'review' || !detail.value || pending.value) return
  try {
    detail.value = await getReview(detail.value.review.id)
    activeJob.value = detail.value.active_job
    if (activeJob.value) schedulePoll()
  } catch (reason) {
    setError(reason)
  }
}

function schedulePoll() {
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  pollTimer = window.setTimeout(pollJob, 650)
}

async function pollJob() {
  if (disposed || !activeJob.value || !detail.value) return
  try {
    const job = await getAIJob(activeJob.value.id)
    activeJob.value = job
    if (['queued', 'running'].includes(job.status)) {
      schedulePoll()
      return
    }
    detail.value = await getReview(detail.value.review.id)
    activeJob.value = detail.value.active_job
    if (job.status === 'succeeded') {
      await loadHistory()
      await loadReport(job.output?.version)
      pageSuccess.value = job.output?.resource_type === 'review_question'
        ? '新的复盘问题已准备好。'
        : `第 ${job.output?.version} 版报告已生成。`
      await replaceQuery()
    } else if (job.status === 'cancelled') {
      pageSuccess.value = '生成已取消，本次额度未扣除。'
    } else {
      pageError.value = {
        code: job.error?.code ?? 'review_generation_failed',
        message: job.error?.message ?? '复盘生成未完成，请重试。',
        request_id: null,
        saved: 'not_saved',
        retryable: true,
      }
    }
  } catch (reason) {
    setError(reason)
    schedulePoll()
  }
}

async function saveFacts(content: string) {
  if (!detail.value || pending.value) return
  pending.value = true
  pageError.value = null
  const fingerprint = JSON.stringify({
    action: 'facts',
    id: detail.value.review.id,
    version: detail.value.review.version,
    content: content.trim(),
  })
  try {
    await saveReviewFacts(
      detail.value.review,
      content.trim(),
      operationId(fingerprint),
    )
    finishOperation()
    detail.value = await getReview(detail.value.review.id)
    pageSuccess.value = '补充事实已保存。'
  } catch (reason) {
    setError(reason)
  } finally {
    pending.value = false
  }
}

async function cancelGeneration() {
  if (!activeJob.value || pending.value) return
  pending.value = true
  try {
    await cancelAIJob(activeJob.value.id, crypto.randomUUID())
    activeJob.value = null
    if (detail.value) detail.value.active_job = null
    pageSuccess.value = '生成已取消，本次额度未扣除。'
  } catch (reason) {
    setError(reason)
  } finally {
    pending.value = false
  }
}

async function selectVersion(version: number) {
  if (!detail.value) return
  try {
    report.value = await getReviewReport(detail.value.review.id, version)
    await replaceQuery()
  } catch (reason) {
    setError(reason)
  }
}

async function reloadReport() {
  if (!detail.value || !report.value) return
  report.value = await getReviewReport(
    detail.value.review.id,
    report.value.report.version,
  )
}

async function addAnnotation(content: string) {
  if (!report.value || !content || pending.value) return
  pending.value = true
  try {
    await createReviewAnnotation(
      report.value.report.id,
      content,
      crypto.randomUUID(),
    )
    await reloadReport()
    pageSuccess.value = '批注已保存。'
  } catch (reason) {
    setError(reason)
  } finally {
    pending.value = false
  }
}

async function updateAnnotation(annotation: ReviewAnnotation, content: string) {
  if (!report.value || pending.value) return
  pending.value = true
  try {
    await updateReviewAnnotation(
      annotation,
      content,
      crypto.randomUUID(),
    )
    await reloadReport()
    pageSuccess.value = '批注已更新。'
  } catch (reason) {
    setError(reason)
  } finally {
    pending.value = false
  }
}

async function deleteAnnotation(annotation: ReviewAnnotation) {
  if (!report.value || pending.value) return
  pending.value = true
  try {
    await deleteReviewAnnotation(annotation, crypto.randomUUID())
    await reloadReport()
    pageSuccess.value = '批注已删除。'
  } catch (reason) {
    setError(reason)
  } finally {
    pending.value = false
  }
}

async function decideProposal(
  proposal: MemoryProposal,
  action: 'confirm' | 'reject',
) {
  if (pending.value) return
  pending.value = true
  try {
    if (action === 'confirm') {
      await confirmMemoryProposal(proposal, crypto.randomUUID())
      pageSuccess.value = '记忆已确认保存。'
    } else {
      await rejectMemoryProposal(proposal, crypto.randomUUID())
      pageSuccess.value = '记忆建议已拒绝。'
    }
    await reloadReport()
  } catch (reason) {
    setError(reason)
  } finally {
    pending.value = false
  }
}

async function setView(next: 'review' | 'memories') {
  view.value = next
  pageError.value = null
  pageSuccess.value = ''
  await replaceQuery()
}
</script>

<template>
  <AppShell
    current="reviews"
    has-secondary
    secondary-label="复盘"
    title="复盘"
    :pending-logout="pendingLogout"
    @logout="logout"
  >
    <template #secondary>
      <ReviewNavigation
        :view="view"
        :selected-date="selectedDate"
        :maximum-date="maximumDate"
        :selected-review-id="selectedReviewId"
        :reviews="reviews"
        :loading="loading"
        @view="setView"
        @update:selected-date="selectedDate = $event"
        @select="openReview"
      />
    </template>
    <template #drawer-secondary="{ close }">
      <ReviewNavigation
        :view="view"
        :selected-date="selectedDate"
        :maximum-date="maximumDate"
        :selected-review-id="selectedReviewId"
        :reviews="reviews"
        :loading="loading"
        @view="(v) => { setView(v); close() }"
        @update:selected-date="selectedDate = $event"
        @select="(v) => { openReview(v); close() }"
      />
    </template>

    <div class="review-main">
      <div class="review-status">
        <StatusMessage :error="logoutError" />
        <StatusMessage
          :error="pageError"
          :success="pageSuccess"
          retry-label="重新读取"
          @retry="initialize"
        />
      </div>

      <section v-if="view === 'review'" class="review-ai-cta">
        <div>
          <h2>复盘走 AI 对话</h2>
          <p>要复盘今天、追问卡点或生成周报，直接到 AI 对话。本页只展示历史复盘、报告与长期记忆。</p>
        </div>
        <RouterLink
          :to="{ path: '/ai', query: { prompt: '复盘今天' } }"
          class="button"
        >
          <MessageCircleMore :size="18" aria-hidden="true" />
          去 AI 说复盘今天
        </RouterLink>
      </section>

      <h1 v-if="view === 'review' && !detail" class="sr-only">每日复盘</h1>

      <MemoryManager v-if="view === 'memories'" :key="memorySyncRevision" />

      <template v-else>
        <p v-if="loading && !detail" class="review-loading" role="status">
          正在读取复盘...
        </p>
        <div v-else-if="detail" class="review-content">
          <header class="review-page-heading">
            <div>
              <p class="review-eyebrow">{{ detail.review.time_zone }} · 活动日</p>
              <h1>{{ detail.review.activity_date }}</h1>
            </div>
            <button
              type="button"
              class="icon-button"
              title="重新读取"
              aria-label="重新读取复盘"
              :disabled="pending"
              @click="openReview(detail.review)"
            >
              <RefreshCcw :size="18" aria-hidden="true" />
            </button>
          </header>

          <ReviewFacts :detail="detail" :pending="pending" @save="saveFacts" />
          <ReviewThread
            :review="detail.review"
            :messages="detail.messages"
            :active-job="activeJob"
            :pending="pending"
            readonly
            @cancel="cancelGeneration"
          />
          <ReviewReport
            :detail="report"
            :reports="detail.reports"
            :freshness="detail.report_freshness"
            :pending="pending || !!activeJob"
            readonly
            @version="selectVersion"
            @add-annotation="addAnnotation"
            @update-annotation="updateAnnotation"
            @delete-annotation="deleteAnnotation"
            @confirm="decideProposal($event, 'confirm')"
            @reject="decideProposal($event, 'reject')"
          />
        </div>
      </template>
    </div>
  </AppShell>
</template>
