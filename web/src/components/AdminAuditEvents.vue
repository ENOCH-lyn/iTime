<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { RefreshCw, Search } from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import { failure, type Failure } from '../lib/api'
import { refreshAdminSession } from '../lib/admin'
import { listAuditEvents, type AuditEvent } from '../lib/admin-governance'
import { localDate, type Page } from '../lib/admin-management'

const items = ref<AuditEvent[]>([])
const cursor = ref<Page<AuditEvent>['next_cursor']>(null)
const action = ref('all')
const loading = ref(false)
const loaded = ref(false)
const error = ref<Failure | null>(null)

const actionLabels: Record<string, string> = {
  ai_body_log_accessed: '技术正文已读取',
  ai_body_log_access_denied: '技术正文读取未成功',
  ai_quality_sample_accessed: '质量样本已读取',
  ai_quality_sample_access_denied: '质量样本读取未成功',
  ai_quality_sample_reviewed: '质量样本已评审',
}
const purposeLabels: Record<string, string> = {
  troubleshooting: '技术排障',
  manual_quality_review: '人工质检',
  prompt_optimization: '提示词优化',
  effect_evaluation: '效果评测',
}

async function load(more = false) {
  if (loading.value) return
  loading.value = true
  error.value = null
  try {
    const query = new URLSearchParams({ limit: '50' })
    if (action.value !== 'all') query.set('action', action.value)
    if (more && cursor.value) {
      query.set('before_time', cursor.value.before_time)
      query.set('before_id', cursor.value.before_id)
    }
    const page = await listAuditEvents(query)
    items.value = more ? [...items.value, ...page.items] : page.items
    cursor.value = page.next_cursor
    loaded.value = true
  } catch (reason) {
    error.value = failure(reason)
    if (['admin_unauthenticated', 'invalid_csrf'].includes(error.value.code)) {
      await refreshAdminSession()
    }
    await nextTick()
    document.querySelector<HTMLElement>('.audit-settings .error-status')?.focus()
  } finally {
    loading.value = false
  }
}

function labelForAction(value: string) {
  return actionLabels[value] ?? value
}

function detailValue(event: AuditEvent, key: string) {
  const value = event.details[key]
  return typeof value === 'string' ? value : ''
}

onMounted(() => void load())
</script>

<template>
  <div class="governance-settings audit-settings">
    <div class="section-intro">
      <div>
        <p class="page-description">
          记录安全和管理操作。审计只保存访问元数据，不复制用户正文。
        </p>
      </div>
      <button type="button" class="button secondary compact-button"
        :disabled="loading" @click="load()">
        <RefreshCw :size="16" aria-hidden="true" />刷新
      </button>
    </div>
    <StatusMessage :error="error" :retry-label="error?.retryable ? '重新读取' : undefined"
      @retry="load()" />
    <form class="management-filters audit-filters" role="search"
      @submit.prevent="load()">
      <div class="field">
        <label for="audit-action">操作类型</label>
        <select id="audit-action" v-model="action" :disabled="loading">
          <option value="all">全部操作</option>
          <option v-for="(label, value) in actionLabels" :key="value" :value="value">
            {{ label }}
          </option>
        </select>
      </div>
      <button class="button filter-submit" type="submit" :disabled="loading">
        <Search :size="17" aria-hidden="true" />筛选
      </button>
    </form>

    <ol class="management-list governance-list audit-list" aria-label="管理审计记录">
      <li v-for="event in items" :key="event.id">
        <div class="management-primary">
          <div class="identity-line">
            <strong>{{ labelForAction(event.action) }}</strong>
            <span v-if="detailValue(event, 'result')" class="state-label"
              :data-state="detailValue(event, 'result')">
              {{ detailValue(event, 'result') === 'succeeded' ? '成功' : '未读取' }}
            </span>
          </div>
          <p>
            {{ event.actor?.email ?? '已删除的账号' }} · {{ localDate(event.created_at) }}
          </p>
          <p v-if="detailValue(event, 'purpose')">
            用途：{{ purposeLabels[detailValue(event, 'purpose')] ?? detailValue(event, 'purpose') }}
          </p>
          <details>
            <summary>查看审计标识</summary>
            <dl class="audit-identifiers">
              <div><dt>请求编号</dt><dd class="wrap-anywhere">{{ event.request_id }}</dd></div>
              <div><dt>目标编号</dt><dd class="wrap-anywhere">{{ event.target_id ?? '无' }}</dd></div>
              <div><dt>管理会话</dt><dd class="wrap-anywhere">{{ detailValue(event, 'admin_session_id') || '无' }}</dd></div>
            </dl>
          </details>
        </div>
      </li>
    </ol>
    <p v-if="loading && !loaded" role="status" class="muted">正在读取审计记录…</p>
    <p v-else-if="loaded && !items.length" class="empty-state">没有符合条件的审计记录。</p>
    <button v-if="cursor" type="button" class="button secondary"
      :disabled="loading" @click="load(true)">加载更多</button>
  </div>
</template>
