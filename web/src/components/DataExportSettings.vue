<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import {
  Archive,
  CircleCheck,
  CircleX,
  Clock3,
  Download,
  LoaderCircle,
  RefreshCw,
} from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import { failure, localFailure, type Failure } from '../lib/api'
import { refreshSession } from '../lib/auth'
import {
  createDataExport,
  downloadDataExport,
  formatExportBytes,
  getDataExport,
  listDataExports,
  type DataExportJob,
  type DataExportStatus,
} from '../lib/data-exports'

const root = ref<HTMLElement | null>(null)
const jobs = ref<DataExportJob[]>([])
const loading = ref(false)
const creating = ref(false)
const downloadingId = ref('')
const error = ref<Failure | null>(null)
const success = ref('')
let pollTimer = 0
let pendingOperationId = ''

const active = computed(() => (
  jobs.value.find(job => ['queued', 'running'].includes(job.status)) ?? null
))

const statusLabels: Record<DataExportStatus, string> = {
  queued: '等待生成',
  running: '正在生成',
  succeeded: '可以下载',
  failed: '生成失败',
  expired: '文件已清理',
}

const statusIcons = {
  queued: Clock3,
  running: LoaderCircle,
  succeeded: CircleCheck,
  failed: CircleX,
  expired: Archive,
} as const

function localTime(value: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN') : '尚未生成'
}

function upsert(job: DataExportJob) {
  const remaining = jobs.value.filter(item => item.id !== job.id)
  jobs.value = [job, ...remaining]
    .sort((left, right) => right.created_at.localeCompare(left.created_at))
    .slice(0, 20)
}

async function focusError() {
  await nextTick()
  root.value?.querySelector<HTMLElement>('.error-status')?.focus()
}

async function handleAuth(current: Failure) {
  if (
    ['unauthenticated', 'invalid_csrf', 'account_suspended'].includes(current.code)
  ) {
    await refreshSession()
  }
}

function clearPoll() {
  window.clearTimeout(pollTimer)
  pollTimer = 0
}

function schedulePoll() {
  clearPoll()
  if (!active.value) return
  pollTimer = window.setTimeout(async () => {
    await load({ quiet: true })
    schedulePoll()
  }, document.hidden ? 5_000 : 2_000)
}

async function load(options: { quiet?: boolean } = {}) {
  if (loading.value) return jobs.value
  loading.value = true
  if (!options.quiet) {
    error.value = null
    success.value = ''
  }
  try {
    const result = await listDataExports()
    jobs.value = result.jobs
    schedulePoll()
    return jobs.value
  } catch (reason) {
    const current = failure(reason)
    if (!options.quiet || jobs.value.length === 0) {
      error.value = current
      await handleAuth(current)
      await focusError()
    }
    return jobs.value
  } finally {
    loading.value = false
  }
}

async function createExport() {
  if (creating.value || active.value) return
  creating.value = true
  error.value = null
  success.value = ''
  const knownJobIds = new Set(jobs.value.map(job => job.id))
  if (!pendingOperationId) pendingOperationId = crypto.randomUUID()
  try {
    const result = await createDataExport(pendingOperationId)
    upsert(result.job)
    pendingOperationId = ''
    success.value = result.reused
      ? '已恢复正在生成的数据包。'
      : '数据包已进入生成队列，可以离开此页面。'
    schedulePoll()
  } catch (reason) {
    const current = failure(reason)
    error.value = current
    await handleAuth(current)
    if (current.saved === 'unknown') {
      await load({ quiet: true })
      const recovered = jobs.value.find(job => !knownJobIds.has(job.id))
      if (recovered) {
        pendingOperationId = ''
        error.value = null
        success.value = recovered.status === 'succeeded'
          ? '已从服务器确认数据包生成成功。'
          : '已从服务器确认数据包正在生成。'
        schedulePoll()
      }
    }
    if (error.value) await focusError()
  } finally {
    creating.value = false
  }
}

async function download(job: DataExportJob) {
  if (downloadingId.value) return
  downloadingId.value = job.id
  error.value = null
  success.value = ''
  try {
    const latest = (await getDataExport(job.id)).job
    upsert(latest)
    if (latest.status !== 'succeeded') {
      error.value = localFailure(
        latest.status === 'expired'
          ? '此文件已清理，请重新生成。'
          : '数据包尚未完成，请稍后再下载。',
      )
      await focusError()
      return
    }
    const result = await downloadDataExport(job.id)
    const url = URL.createObjectURL(result.blob)
    const link = document.createElement('a')
    link.href = url
    link.download = result.filename
    link.hidden = true
    document.body.append(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 0)
    success.value = '数据包下载已开始。'
  } catch (reason) {
    const current = failure(reason)
    error.value = current
    await handleAuth(current)
    await focusError()
  } finally {
    downloadingId.value = ''
  }
}

function visibilityChanged() {
  if (!document.hidden) void load({ quiet: true })
  schedulePoll()
}

onMounted(() => {
  void load()
  document.addEventListener('visibilitychange', visibilityChanged)
})

onUnmounted(() => {
  clearPoll()
  document.removeEventListener('visibilitychange', visibilityChanged)
})
</script>

<template>
  <div id="data-export-settings" ref="root" class="data-export-settings">
    <div class="section-intro data-export-intro">
      <div>
        <p class="page-description">
          生成包含完整 JSON 及 CSV、ICS、Markdown 的个人数据包。
        </p>
        <p class="field-hint">文件生成后保留 24 小时，仅当前账号可以下载。</p>
      </div>
      <button
        type="button"
        class="button"
        :disabled="creating || Boolean(active)"
        @click="createExport"
      >
        <Archive :size="18" aria-hidden="true" />
        {{ creating ? '正在提交…' : active ? '数据包生成中' : '生成完整数据包' }}
      </button>
    </div>

    <StatusMessage
      :error="error"
      :success="success"
      :retry-label="error?.retryable ? '重新读取' : undefined"
      @retry="load()"
    />

    <p v-if="loading && jobs.length === 0" role="status" class="muted">
      正在读取导出记录…
    </p>
    <div v-else-if="jobs.length === 0" class="empty-state">
      <p>还没有生成过数据包。</p>
    </div>

    <section v-else class="data-export-history" aria-labelledby="data-export-history-title">
      <div class="data-export-heading">
        <div>
          <h2 id="data-export-history-title">最近的数据包</h2>
          <p>最多显示最近 20 次生成记录。</p>
        </div>
        <button
          type="button"
          class="icon-button"
          title="刷新导出记录"
          aria-label="刷新导出记录"
          :disabled="loading"
          @click="load()"
        >
          <RefreshCw :size="18" aria-hidden="true" />
        </button>
      </div>
      <ul class="data-export-list" aria-label="数据导出记录">
        <li v-for="job in jobs" :key="job.id" class="data-export-row">
          <component
            :is="statusIcons[job.status]"
            class="data-export-icon"
            :data-state="job.status"
            :size="20"
            aria-hidden="true"
          />
          <div class="data-export-details">
            <div class="identity-line">
              <strong>完整数据包</strong>
              <span class="state-label" :data-state="job.status">
                {{ statusLabels[job.status] }}
              </span>
            </div>
            <p>创建于 {{ localTime(job.created_at) }}</p>
            <p v-if="job.status === 'succeeded'">
              {{ formatExportBytes(job.byte_size) }} · 下载截止 {{ localTime(job.expires_at) }}
            </p>
            <p v-else-if="job.status === 'running' || job.status === 'queued'">
              已尝试 {{ job.attempt_count }} 次；页面关闭后仍会继续。
            </p>
            <p v-else-if="job.status === 'failed'" class="error-text">
              {{ job.error?.message ?? '数据包生成失败。' }}
            </p>
            <p v-else>临时文件已按期限清理，可重新生成最新数据。</p>
            <details v-if="job.snapshot_at || job.sha256">
              <summary>文件详情</summary>
              <dl>
                <div v-if="job.snapshot_at">
                  <dt>数据快照</dt><dd>{{ localTime(job.snapshot_at) }}</dd>
                </div>
                <div v-if="job.sha256">
                  <dt>SHA-256</dt><dd class="wrap-anywhere">{{ job.sha256 }}</dd>
                </div>
              </dl>
            </details>
          </div>
          <div class="data-export-actions">
            <button
              v-if="job.status === 'succeeded'"
              type="button"
              class="button secondary compact-button"
              :disabled="Boolean(downloadingId)"
              @click="download(job)"
            >
              <Download :size="17" aria-hidden="true" />
              {{ downloadingId === job.id ? '正在下载…' : '下载 ZIP' }}
            </button>
            <button
              v-else-if="['failed', 'expired'].includes(job.status) && !active"
              type="button"
              class="button secondary compact-button"
              :disabled="creating"
              @click="createExport"
            >
              <RefreshCw :size="17" aria-hidden="true" />
              重新生成
            </button>
          </div>
        </li>
      </ul>
    </section>
  </div>
</template>
