<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CalendarSearch, ImageUp, RefreshCw, XCircle } from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import {
  cancelAIJob,
  listScreenshotJobs,
  uploadCalendarScreenshot,
  type CalendarScreenshotJob,
  type AIJobStatus,
} from '../lib/ai-jobs'
import { failure, localFailure, type Failure } from '../lib/api'

const props = defineProps<{ active: boolean }>()
const emit = defineEmits<{ batchReady: [batchId: string] }>()

const fileInput = ref<HTMLInputElement | null>(null)
const file = ref<File | null>(null)
const jobs = ref<CalendarScreenshotJob[]>([])
const loading = ref(false)
const uploadPending = ref(false)
const cancellingId = ref('')
const error = ref<Failure | null>(null)
const success = ref('')
const autoOpenIds = new Set<string>()
const emittedIds = new Set<string>()
const cancelOperations = new Map<string, string>()
let uploadOperation: { id: string; fingerprint: string } | null = null
let timer = 0
let revision = 0
let pollDelay = 1000

const activeJobs = computed(() => jobs.value.filter(
  job => ['queued', 'running'].includes(job.status),
))
const recentJobs = computed(() => jobs.value.slice(0, 5))
const stateLabels: Record<AIJobStatus, string> = {
  queued: '排队中',
  running: '识别中',
  succeeded: '等待校对',
  failed: '识别失败',
  timed_out: '已超时',
  cancelled: '已取消',
}

function stopPolling() {
  window.clearTimeout(timer)
  timer = 0
}

function schedulePolling() {
  stopPolling()
  if (!props.active || document.hidden || !activeJobs.value.length) return
  timer = window.setTimeout(() => {
    void loadJobs(true)
  }, pollDelay)
  pollDelay = Math.min(3000, pollDelay + 1000)
}

function processSucceeded(items: CalendarScreenshotJob[]) {
  for (const job of items) {
    if (
      job.status === 'succeeded'
      && job.output?.resource_type === 'calendar_import_batch'
      && autoOpenIds.has(job.id)
      && !emittedIds.has(job.id)
    ) {
      emittedIds.add(job.id)
      autoOpenIds.delete(job.id)
      success.value = `已识别 ${job.output.candidate_count} 条候选，等待校对。`
      emit('batchReady', job.output.batch_id)
    }
  }
}

async function loadJobs(silent = false) {
  const current = ++revision
  if (!silent) loading.value = true
  if (!silent) error.value = null
  try {
    const items = await listScreenshotJobs()
    if (current !== revision) return
    jobs.value = items
    processSucceeded(items)
    if (!activeJobs.value.length) pollDelay = 1000
  } catch (reason) {
    if (current !== revision) return
    error.value = failure(reason)
  } finally {
    if (current === revision) {
      loading.value = false
      schedulePolling()
    }
  }
}

function chooseFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
  uploadOperation = null
  error.value = null
  success.value = ''
}

function validateFile(selected: File) {
  const allowed = ['image/png', 'image/jpeg', 'image/webp']
  if (!allowed.includes(selected.type)) {
    return localFailure('请选择 PNG、JPEG 或 WebP 图片。')
  }
  if (!selected.size || selected.size > 5 * 1024 * 1024) {
    return localFailure('图片必须大于 0 且不超过 5 MiB。')
  }
  return null
}

async function upload() {
  if (!file.value || uploadPending.value) return
  const validation = validateFile(file.value)
  if (validation) {
    error.value = validation
    return
  }
  const selected = file.value
  const fingerprint = `${selected.name}:${selected.size}:${selected.lastModified}`
  uploadOperation = uploadOperation?.fingerprint === fingerprint
    ? uploadOperation
    : { id: crypto.randomUUID(), fingerprint }
  uploadPending.value = true
  error.value = null
  success.value = ''
  try {
    const result = await uploadCalendarScreenshot(selected, uploadOperation.id)
    uploadOperation = null
    autoOpenIds.add(result.job.id)
    jobs.value = [
      result.job,
      ...jobs.value.filter(job => job.id !== result.job.id),
    ]
    file.value = null
    if (fileInput.value) fileInput.value.value = ''
    success.value = '截图已提交，离开此页面也会继续识别。'
    pollDelay = 1000
    schedulePolling()
  } catch (reason) {
    error.value = failure(reason)
    if (error.value.saved === 'unknown') {
      await loadJobs(true)
      const possible = jobs.value.find(job => (
        job.input_summary.filename === selected.name
        && ['queued', 'running'].includes(job.status)
      ))
      if (possible) autoOpenIds.add(possible.id)
    }
  } finally {
    uploadPending.value = false
  }
}

async function cancel(job: CalendarScreenshotJob) {
  if (cancellingId.value) return
  const operationId = cancelOperations.get(job.id) ?? crypto.randomUUID()
  cancelOperations.set(job.id, operationId)
  cancellingId.value = job.id
  error.value = null
  try {
    const result = await cancelAIJob<CalendarScreenshotJob>(job.id, operationId)
    cancelOperations.delete(job.id)
    autoOpenIds.delete(job.id)
    jobs.value = jobs.value.map(item => item.id === job.id ? result.job : item)
    success.value = '识别作业已取消，未写入日历。'
    schedulePolling()
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    cancellingId.value = ''
  }
}

function openBatch(job: CalendarScreenshotJob) {
  if (job.output?.batch_id) emit('batchReady', job.output.batch_id)
}

function readableTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function visibilityChanged() {
  if (!props.active) return
  if (document.hidden) stopPolling()
  else void loadJobs(true)
}

watch(() => props.active, async active => {
  if (!active) {
    stopPolling()
    return
  }
  pollDelay = 1000
  await loadJobs()
  await nextTick()
  fileInput.value?.focus()
}, { immediate: true })

onMounted(() => {
  document.addEventListener('visibilitychange', visibilityChanged)
  window.addEventListener('focus', visibilityChanged)
})
onBeforeUnmount(() => {
  stopPolling()
  document.removeEventListener('visibilitychange', visibilityChanged)
  window.removeEventListener('focus', visibilityChanged)
})
</script>

<template>
  <section class="import-screenshot" aria-labelledby="screenshot-import-title">
    <div class="import-section-heading">
      <div>
        <h3 id="screenshot-import-title">识别截图</h3>
        <p>PNG、JPEG 或 WebP，最大 5 MiB。</p>
      </div>
      <button type="button" class="small-icon-button" aria-label="刷新识别作业"
        title="刷新识别作业" :disabled="loading" @click="loadJobs()">
        <RefreshCw :size="17" aria-hidden="true" />
      </button>
    </div>
    <StatusMessage :error="error" :success="success"
      :retry-label="error?.retryable ? '重试读取' : undefined"
      @retry="loadJobs()" />
    <div class="screenshot-upload-actions">
      <label class="button secondary compact-button import-file-button">
        <ImageUp :size="17" aria-hidden="true" />
        <span>{{ file?.name ?? '选择截图' }}</span>
        <input ref="fileInput" type="file" accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp"
          :disabled="uploadPending" @change="chooseFile" />
      </label>
      <button type="button" class="button compact-button"
        :disabled="uploadPending || !file" @click="upload">
        <CalendarSearch :size="17" aria-hidden="true" />
        {{ uploadPending ? '正在上传…' : '开始识别' }}
      </button>
    </div>

    <p v-if="loading && !jobs.length" role="status" class="muted screenshot-loading">
      正在读取识别作业…
    </p>
    <ol v-else-if="recentJobs.length" class="screenshot-job-list" aria-label="截图识别作业">
      <li v-for="job in recentJobs" :key="job.id">
        <div class="screenshot-job-copy">
          <div class="identity-line">
            <strong>{{ job.input_summary.filename }}</strong>
            <span class="state-label" :data-state="job.status">
              {{ stateLabels[job.status] }}
            </span>
          </div>
          <p>
            {{ job.input_summary.width }}×{{ job.input_summary.height }} ·
            {{ readableTime(job.created_at) }} · 尝试 {{ job.attempt_count }}/{{ job.max_attempts }}
          </p>
          <p v-if="job.error" class="import-candidate-error">{{ job.error.message }}</p>
        </div>
        <div class="row-actions">
          <button v-if="['queued', 'running'].includes(job.status)" type="button"
            class="button secondary compact-button" :disabled="!!cancellingId"
            @click="cancel(job)">
            <XCircle :size="16" aria-hidden="true" />
            {{ cancellingId === job.id ? '正在取消…' : '取消' }}
          </button>
          <button v-else-if="job.status === 'succeeded'" type="button"
            class="button compact-button" @click="openBatch(job)">
            <CalendarSearch :size="16" aria-hidden="true" />校对候选
          </button>
        </div>
      </li>
    </ol>
  </section>
</template>
