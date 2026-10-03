<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { Eye, RefreshCw, Search, ShieldCheck, X } from 'lucide-vue-next'
import FormField from './FormField.vue'
import StatusMessage from './StatusMessage.vue'
import { failure, type Failure } from '../lib/api'
import { refreshAdminSession } from '../lib/admin'
import type { AIConfigCapability } from '../lib/admin-ai'
import {
  accessBodyLog,
  listBodyLogs,
  type AIBodyLog,
  type AIBodyLogDetail,
  type BodyLogStatus,
} from '../lib/admin-governance'
import { localDate, type Page } from '../lib/admin-management'

const items = ref<AIBodyLog[]>([])
const cursor = ref<Page<AIBodyLog>['next_cursor']>(null)
const search = ref('')
const capability = ref<'all' | AIConfigCapability>('all')
const status = ref<'all' | BodyLogStatus>('all')
const loading = ref(false)
const loaded = ref(false)
const error = ref<Failure | null>(null)
const selected = ref<AIBodyLog | null>(null)
const detail = ref<AIBodyLogDetail | null>(null)
const detailError = ref<Failure | null>(null)
const accessing = ref(false)
const dialog = ref<HTMLDialogElement | null>(null)

const capabilityLabels: Record<AIConfigCapability, string> = {
  calendar_screenshot: '截图识别',
  conversation: '全局对话',
}
const statusLabels: Record<BodyLogStatus, string> = {
  running: '执行中',
  succeeded: '成功',
  retryable_failed: '等待重试',
  failed: '失败',
  timed_out: '超时',
  cancelled: '已取消',
  abandoned: '租约失效',
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

async function load(more = false) {
  if (loading.value) return
  loading.value = true
  error.value = null
  try {
    const query = new URLSearchParams({ limit: '30' })
    if (search.value.trim()) query.set('search', search.value.trim())
    if (capability.value !== 'all') query.set('capability', capability.value)
    if (status.value !== 'all') query.set('status', status.value)
    if (more && cursor.value) {
      query.set('before_time', cursor.value.before_time)
      query.set('before_id', cursor.value.before_id)
    }
    const page = await listBodyLogs(query)
    items.value = more ? [...items.value, ...page.items] : page.items
    cursor.value = page.next_cursor
    loaded.value = true
  } catch (reason) {
    error.value = await handleError(reason, '.body-log-settings .error-status')
  } finally {
    loading.value = false
  }
}

function open(item: AIBodyLog) {
  selected.value = item
  detail.value = null
  detailError.value = null
  dialog.value?.showModal()
}

async function access() {
  if (!selected.value || accessing.value) return
  accessing.value = true
  detailError.value = null
  try {
    detail.value = await accessBodyLog(selected.value.id)
  } catch (reason) {
    detailError.value = await handleError(
      reason,
      '.sensitive-dialog .error-status',
    )
  } finally {
    accessing.value = false
  }
}

function close() {
  dialog.value?.close()
  selected.value = null
  detail.value = null
  detailError.value = null
}

function formattedRequest(value: Record<string, unknown>) {
  return JSON.stringify(value, null, 2)
}

onMounted(() => void load())
</script>

<template>
  <div class="governance-settings body-log-settings">
    <div class="section-intro">
      <div>
        <p class="page-description">
          技术正文默认保留 7 天。列表只显示元数据，读取正文会单独记录审计。
        </p>
      </div>
      <button type="button" class="button secondary compact-button"
        :disabled="loading" @click="load()">
        <RefreshCw :size="16" aria-hidden="true" />刷新
      </button>
    </div>
    <StatusMessage :error="error" :retry-label="error?.retryable ? '重新读取' : undefined"
      @retry="load()" />
    <form class="management-filters governance-filters" role="search"
      @submit.prevent="load()">
      <FormField id="body-log-search" v-model="search" label="用户邮箱"
        type="text" autocomplete="off" :maxlength="254" :disabled="loading" />
      <div class="field">
        <label for="body-log-capability">能力</label>
        <select id="body-log-capability" v-model="capability" :disabled="loading">
          <option value="all">全部</option>
          <option value="conversation">全局对话</option>
          <option value="calendar_screenshot">截图识别</option>
        </select>
      </div>
      <div class="field">
        <label for="body-log-status">状态</label>
        <select id="body-log-status" v-model="status" :disabled="loading">
          <option value="all">全部</option>
          <option v-for="(label, value) in statusLabels" :key="value" :value="value">
            {{ label }}
          </option>
        </select>
      </div>
      <button class="button filter-submit" type="submit" :disabled="loading">
        <Search :size="17" aria-hidden="true" />筛选
      </button>
    </form>

    <ul class="management-list governance-list" aria-label="AI 技术正文日志">
      <li v-for="item in items" :key="item.id">
        <div class="management-primary">
          <div class="identity-line">
            <strong class="wrap-anywhere">{{ item.owner.email }}</strong>
            <span class="state-label" :data-state="item.status">
              {{ statusLabels[item.status] }}
            </span>
            <span class="state-label">{{ capabilityLabels[item.capability] }}</span>
          </div>
          <p>
            尝试 {{ item.attempt_number }} · {{ localDate(item.started_at) }}
            · 到期 {{ localDate(item.expires_at) }}
          </p>
          <p class="wrap-anywhere">
            作业 {{ item.job_id }}
            <template v-if="item.error_code"> · {{ item.error_code }}</template>
          </p>
        </div>
        <button type="button" class="button secondary compact-button"
          @click="open(item)">
          <Eye :size="16" aria-hidden="true" />查看正文
        </button>
      </li>
    </ul>
    <p v-if="loading && !loaded" role="status" class="muted">正在读取技术日志…</p>
    <p v-else-if="loaded && !items.length" class="empty-state">没有符合条件的技术日志。</p>
    <button v-if="cursor" type="button" class="button secondary"
      :disabled="loading" @click="load(true)">加载更多</button>

    <dialog ref="dialog" class="sensitive-dialog" aria-labelledby="body-log-dialog-title"
      @close="selected = null; detail = null; detailError = null">
      <div class="dialog-heading">
        <div>
          <p class="muted">敏感内容访问</p>
          <h2 id="body-log-dialog-title">AI 技术正文</h2>
        </div>
        <button type="button" class="icon-button" title="关闭" aria-label="关闭"
          @click="close"><X :size="20" aria-hidden="true" /></button>
      </div>
      <p v-if="selected" class="dialog-target wrap-anywhere">
        {{ selected.owner.email }} · 作业 {{ selected.job_id }}
      </p>
      <StatusMessage :error="detailError" />
      <template v-if="!detail">
        <p>本次用途为技术排障。读取后会记录管理员、管理会话、目标、用途、结果和请求编号。</p>
        <div class="dialog-actions">
          <button type="button" class="button secondary" :disabled="accessing"
            @click="close">取消</button>
          <button type="button" class="button" :disabled="accessing" @click="access">
            <ShieldCheck :size="18" aria-hidden="true" />
            {{ accessing ? '正在审计并读取…' : '审计并读取正文' }}
          </button>
        </div>
      </template>
      <template v-else>
        <div class="sensitive-content">
          <section aria-labelledby="body-log-request-title">
            <h3 id="body-log-request-title">请求正文</h3>
            <pre>{{ formattedRequest(detail.request_body) }}</pre>
          </section>
          <section aria-labelledby="body-log-response-title">
            <h3 id="body-log-response-title">响应正文</h3>
            <pre>{{ detail.response_body || '（没有已保存的响应正文）' }}</pre>
          </section>
        </div>
        <div class="dialog-actions">
          <button type="button" class="button secondary" @click="close">关闭</button>
        </div>
      </template>
    </dialog>
  </div>
</template>
