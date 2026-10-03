<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { ClipboardCheck, Eye, RefreshCw, Search, ShieldCheck, X } from 'lucide-vue-next'
import FormField from './FormField.vue'
import StatusMessage from './StatusMessage.vue'
import { failure, type Failure } from '../lib/api'
import { refreshAdminSession } from '../lib/admin'
import type { AIConfigCapability } from '../lib/admin-ai'
import {
  accessQualitySample,
  listQualitySamples,
  reviewQualitySample,
  type AIQualitySample,
  type AIQualitySampleDetail,
  type QualityOutcome,
  type QualityPurpose,
  type QualityStatus,
} from '../lib/admin-governance'
import { localDate, type Page } from '../lib/admin-management'

const items = ref<AIQualitySample[]>([])
const cursor = ref<Page<AIQualitySample>['next_cursor']>(null)
const search = ref('')
const capability = ref<'all' | AIConfigCapability>('all')
const status = ref<'all' | QualityStatus>('all')
const outcome = ref<'all' | QualityOutcome>('all')
const loading = ref(false)
const loaded = ref(false)
const error = ref<Failure | null>(null)
const selected = ref<AIQualitySample | null>(null)
const detail = ref<AIQualitySampleDetail | null>(null)
const detailError = ref<Failure | null>(null)
const detailSuccess = ref('')
const purpose = ref<QualityPurpose>('manual_quality_review')
const reviewOutcome = ref<QualityOutcome>('acceptable')
const reviewNotes = ref('')
const pendingAction = ref<'access' | 'review' | ''>('')
const dialog = ref<HTMLDialogElement | null>(null)

const capabilityLabels: Record<AIConfigCapability, string> = {
  calendar_screenshot: '截图识别',
  conversation: '全局对话',
  planning: '日周规划',
  review: '每日复盘',
}
const purposeLabels: Record<QualityPurpose, string> = {
  manual_quality_review: '人工质检',
  prompt_optimization: '提示词优化',
  effect_evaluation: '效果评测',
}
const outcomeLabels: Record<QualityOutcome, string> = {
  acceptable: '可接受',
  needs_improvement: '需改进',
}

async function handleError(reason: unknown, selector: string) {
  const current = failure(reason)
  if (['admin_unauthenticated', 'invalid_csrf'].includes(current.code)) {
    await refreshAdminSession()
  }
  await nextTick()
  document.querySelector<HTMLElement>(selector)?.focus()
  return current
}

async function load(more = false, keepStatus = false) {
  if (loading.value) return
  loading.value = true
  if (!keepStatus) error.value = null
  try {
    const query = new URLSearchParams({ limit: '30' })
    if (search.value.trim()) query.set('search', search.value.trim())
    if (capability.value !== 'all') query.set('capability', capability.value)
    if (status.value !== 'all') query.set('status', status.value)
    if (outcome.value !== 'all') query.set('outcome', outcome.value)
    if (more && cursor.value) {
      query.set('before_time', cursor.value.before_time)
      query.set('before_id', cursor.value.before_id)
    }
    const page = await listQualitySamples(query)
    items.value = more ? [...items.value, ...page.items] : page.items
    cursor.value = page.next_cursor
    loaded.value = true
  } catch (reason) {
    error.value = await handleError(reason, '.quality-settings .error-status')
  } finally {
    loading.value = false
  }
}

function open(item: AIQualitySample) {
  selected.value = item
  detail.value = null
  detailError.value = null
  detailSuccess.value = ''
  purpose.value = 'manual_quality_review'
  dialog.value?.showModal()
}

async function access() {
  if (!selected.value || pendingAction.value) return
  pendingAction.value = 'access'
  detailError.value = null
  try {
    detail.value = await accessQualitySample(selected.value.id, purpose.value)
    reviewOutcome.value = detail.value.outcome ?? 'acceptable'
    reviewNotes.value = detail.value.review_notes ?? ''
  } catch (reason) {
    detailError.value = await handleError(
      reason,
      '.sensitive-dialog .error-status',
    )
  } finally {
    pendingAction.value = ''
  }
}

async function saveReview() {
  if (!detail.value || pendingAction.value) return
  pendingAction.value = 'review'
  detailError.value = null
  detailSuccess.value = ''
  try {
    const updated = await reviewQualitySample(detail.value.id, {
      expected_version: detail.value.version,
      outcome: reviewOutcome.value,
      review_notes: reviewNotes.value.trim() || null,
    })
    detail.value = {
      ...detail.value,
      ...updated,
      request_body: detail.value.request_body,
      response_body: detail.value.response_body,
    }
    items.value = items.value.map(item => item.id === updated.id ? updated : item)
    reviewNotes.value = updated.review_notes ?? ''
    detailSuccess.value = '质检结论已保存。'
  } catch (reason) {
    detailError.value = await handleError(
      reason,
      '.sensitive-dialog .error-status',
    )
    if (detailError.value.code === 'ai_quality_sample_version_conflict') {
      await load(false, true)
    }
  } finally {
    pendingAction.value = ''
  }
}

function close() {
  dialog.value?.close()
  selected.value = null
  detail.value = null
  detailError.value = null
  detailSuccess.value = ''
  reviewNotes.value = ''
}

function formattedRequest(value: Record<string, unknown>) {
  return JSON.stringify(value, null, 2)
}

onMounted(() => void load())
</script>

<template>
  <div class="governance-settings quality-settings">
    <div class="section-intro">
      <div>
        <p class="page-description">
          样本最多保留 30 天，仅用于人工质检、提示词优化和效果评测，不用于训练。
        </p>
      </div>
      <button type="button" class="button secondary compact-button"
        :disabled="loading" @click="load()">
        <RefreshCw :size="16" aria-hidden="true" />刷新
      </button>
    </div>
    <StatusMessage :error="error" :retry-label="error?.retryable ? '重新读取' : undefined"
      @retry="load()" />
    <form class="management-filters governance-filters quality-filters"
      role="search" @submit.prevent="load()">
      <FormField id="quality-search" v-model="search" label="用户邮箱"
        type="text" autocomplete="off" :maxlength="254" :disabled="loading" />
      <div class="field">
        <label for="quality-capability">能力</label>
        <select id="quality-capability" v-model="capability" :disabled="loading">
          <option value="all">全部</option>
          <option value="conversation">全局对话</option>
          <option value="calendar_screenshot">截图识别</option>
        </select>
      </div>
      <div class="field">
        <label for="quality-status">评审状态</label>
        <select id="quality-status" v-model="status" :disabled="loading">
          <option value="all">全部</option>
          <option value="pending">待质检</option>
          <option value="reviewed">已质检</option>
        </select>
      </div>
      <div class="field">
        <label for="quality-outcome">结论</label>
        <select id="quality-outcome" v-model="outcome" :disabled="loading">
          <option value="all">全部</option>
          <option value="acceptable">可接受</option>
          <option value="needs_improvement">需改进</option>
        </select>
      </div>
      <button class="button filter-submit" type="submit" :disabled="loading">
        <Search :size="17" aria-hidden="true" />筛选
      </button>
    </form>

    <ul class="management-list governance-list" aria-label="AI 质量样本">
      <li v-for="item in items" :key="item.id">
        <div class="management-primary">
          <div class="identity-line">
            <strong class="wrap-anywhere">{{ item.owner.email }}</strong>
            <span class="state-label" :data-state="item.status">
              {{ item.status === 'pending' ? '待质检' : '已质检' }}
            </span>
            <span v-if="item.outcome" class="state-label" :data-state="item.outcome">
              {{ outcomeLabels[item.outcome] }}
            </span>
            <span class="state-label">{{ capabilityLabels[item.capability] }}</span>
          </div>
          <p>采样于 {{ localDate(item.sampled_at) }} · 到期 {{ localDate(item.expires_at) }}</p>
          <p class="wrap-anywhere">来源作业 {{ item.source_job_id }}</p>
        </div>
        <button type="button" class="button secondary compact-button"
          @click="open(item)">
          <Eye :size="16" aria-hidden="true" />查看并质检
        </button>
      </li>
    </ul>
    <p v-if="loading && !loaded" role="status" class="muted">正在读取质量样本…</p>
    <p v-else-if="loaded && !items.length" class="empty-state">没有符合条件的质量样本。</p>
    <button v-if="cursor" type="button" class="button secondary"
      :disabled="loading" @click="load(true)">加载更多</button>

    <dialog ref="dialog" class="sensitive-dialog" aria-labelledby="quality-dialog-title"
      @close="selected = null; detail = null; detailError = null; detailSuccess = ''">
      <div class="dialog-heading">
        <div>
          <p class="muted">敏感内容访问</p>
          <h2 id="quality-dialog-title">AI 质量样本</h2>
        </div>
        <button type="button" class="icon-button" title="关闭" aria-label="关闭"
          @click="close"><X :size="20" aria-hidden="true" /></button>
      </div>
      <p v-if="selected" class="dialog-target wrap-anywhere">
        {{ selected.owner.email }} · 作业 {{ selected.source_job_id }}
      </p>
      <StatusMessage :error="detailError" :success="detailSuccess" />
      <template v-if="!detail">
        <div class="field purpose-field">
          <label for="quality-purpose">本次访问用途</label>
          <select id="quality-purpose" v-model="purpose"
            :disabled="!!pendingAction">
            <option v-for="(label, value) in purposeLabels" :key="value" :value="value">
              {{ label }}
            </option>
          </select>
          <p class="field-hint">读取后会记录管理员、管理会话、目标、用途、结果和请求编号。</p>
        </div>
        <div class="dialog-actions">
          <button type="button" class="button secondary" :disabled="!!pendingAction"
            @click="close">取消</button>
          <button type="button" class="button" :disabled="!!pendingAction" @click="access">
            <ShieldCheck :size="18" aria-hidden="true" />
            {{ pendingAction === 'access' ? '正在审计并读取…' : '审计并读取正文' }}
          </button>
        </div>
      </template>
      <template v-else>
        <div class="sensitive-content">
          <section aria-labelledby="quality-request-title">
            <h3 id="quality-request-title">请求正文</h3>
            <pre>{{ formattedRequest(detail.request_body) }}</pre>
          </section>
          <section aria-labelledby="quality-response-title">
            <h3 id="quality-response-title">响应正文</h3>
            <pre>{{ detail.response_body }}</pre>
          </section>
        </div>
        <form class="quality-review-form" @submit.prevent="saveReview">
          <fieldset class="segmented-field">
            <legend>质检结论</legend>
            <label :class="{ selected: reviewOutcome === 'acceptable' }">
              <input v-model="reviewOutcome" type="radio" name="quality-outcome"
                value="acceptable" :disabled="!!pendingAction" />可接受
            </label>
            <label :class="{ selected: reviewOutcome === 'needs_improvement' }">
              <input v-model="reviewOutcome" type="radio" name="quality-outcome"
                value="needs_improvement" :disabled="!!pendingAction" />需改进
            </label>
          </fieldset>
          <div class="field">
            <label for="quality-review-notes">评审备注（可选）</label>
            <textarea id="quality-review-notes" v-model="reviewNotes" rows="4"
              maxlength="2000" :disabled="!!pendingAction"></textarea>
            <p class="field-hint">不要粘贴无关凭据或额外个人信息。</p>
          </div>
          <div class="dialog-actions">
            <button type="button" class="button secondary" :disabled="!!pendingAction"
              @click="close">关闭</button>
            <button type="submit" class="button" :disabled="!!pendingAction">
              <ClipboardCheck :size="18" aria-hidden="true" />
              {{ pendingAction === 'review' ? '正在保存…' : '保存质检结论' }}
            </button>
          </div>
        </form>
      </template>
    </dialog>
  </div>
</template>
