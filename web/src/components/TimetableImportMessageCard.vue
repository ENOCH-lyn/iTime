<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  CalendarCheck,
  Check,
  CircleAlert,
  LoaderCircle,
  Pencil,
  RefreshCw,
  XCircle,
} from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import { failure, type Failure } from '../lib/api'
import {
  cancelCalendarImportJob,
  confirmCalendarImport,
  getCalendarImport,
  getCalendarImportJob,
  type CalendarAIImportJob,
  type CalendarImportDetail,
} from '../lib/calendar-imports'

const props = defineProps<{ batchId: string }>()
const router = useRouter()
const detail = ref<CalendarImportDetail | null>(null)
const job = ref<CalendarAIImportJob | null>(null)
const loading = ref(false)
const pending = ref(false)
const cancelling = ref(false)
const error = ref<Failure | null>(null)
const resultText = ref('')
let pollTimer = 0
let revision = 0

const batch = computed(() => detail.value?.batch ?? null)
const validCandidates = computed(() => (
  detail.value?.candidates.filter(candidate => candidate.state === 'valid').slice(0, 50) ?? []
))
const firstDate = computed(() => (
  detail.value?.candidates.find(candidate => candidate.draft?.start_date)?.draft?.start_date
  ?? null
))
const stateLabel = computed(() => {
  if (!batch.value) return '正在读取'
  return {
    queued: '等待处理',
    processing: '正在解析',
    needs_review: '待确认',
    confirmed: '已完成',
    cancelled: '已取消',
    failed: '处理失败',
    expired: '已过期',
  }[batch.value.state]
})
const sourceLabel = computed(() => {
  if (!batch.value) return '课表导入'
  return {
    xlsx_file: 'Excel 文件',
    ics_file: 'ICS 文件',
    image: '课表图片',
    public_url: '公开网页',
    text: '文字描述',
  }[batch.value.source_type]
})
const processing = computed(() => (
  batch.value ? ['queued', 'processing'].includes(batch.value.state) : false
))

watch(() => props.batchId, () => {
  stopPolling()
  void load()
})

onMounted(load)
onBeforeUnmount(stopPolling)

function stopPolling() {
  window.clearTimeout(pollTimer)
  pollTimer = 0
}

function schedulePoll() {
  stopPolling()
  if (!processing.value) return
  pollTimer = window.setTimeout(() => {
    void load(true)
  }, 900)
}

async function load(silent = false) {
  const currentRevision = ++revision
  if (!silent) loading.value = true
  try {
    const nextDetail = await getCalendarImport(props.batchId)
    if (currentRevision !== revision) return
    detail.value = nextDetail
    if (nextDetail.batch.job_id && ['queued', 'processing'].includes(nextDetail.batch.state)) {
      job.value = await getCalendarImportJob(nextDetail.batch.job_id)
    } else {
      job.value = null
    }
    error.value = null
    schedulePoll()
  } catch (reason) {
    if (currentRevision !== revision) return
    error.value = failure(reason)
  } finally {
    if (currentRevision === revision) loading.value = false
  }
}

async function confirmAll() {
  if (!batch.value || !validCandidates.value.length || pending.value) return
  pending.value = true
  error.value = null
  resultText.value = ''
  try {
    const result = await confirmCalendarImport(
      batch.value,
      validCandidates.value.map(candidate => ({
        candidate_id: candidate.id,
        expected_version: candidate.version,
        action: 'confirm' as const,
      })),
    )
    const created = result.outcomes.filter(item => item.outcome === 'created').length
    const skipped = result.outcomes.filter(item => item.outcome === 'skipped').length
    resultText.value = [
      created ? `已导入 ${created} 条` : '',
      skipped ? `跳过 ${skipped} 条` : '',
    ].filter(Boolean).join('，') || '候选已处理。'
    await load(true)
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    pending.value = false
  }
}

async function cancel() {
  if (!batch.value?.job_id || cancelling.value) return
  cancelling.value = true
  error.value = null
  try {
    await cancelCalendarImportJob(batch.value.job_id)
    resultText.value = '处理已取消。'
    await load(true)
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    cancelling.value = false
  }
}

function adjust() {
  void router.push({
    path: '/calendar',
    query: { import: props.batchId },
  })
}

function openCalendar() {
  void router.push({
    path: '/calendar',
    query: { view: 'week', ...(firstDate.value ? { date: firstDate.value } : {}) },
  })
}
</script>

<template>
  <section class="message-proposal timetable-import-card" aria-label="课表导入">
    <div v-if="loading && !batch" class="message-proposal-loading" role="status">
      <LoaderCircle :size="16" class="spin" aria-hidden="true" />
      正在读取导入状态
    </div>
    <template v-else-if="batch">
      <div class="message-proposal-heading">
        <CalendarCheck :size="19" aria-hidden="true" />
        <div>
          <strong>{{ sourceLabel }}</strong>
          <span :data-state="batch.state">{{ stateLabel }}</span>
        </div>
      </div>

      <div v-if="processing" class="timetable-import-progress" role="status">
        <LoaderCircle :size="16" class="spin" aria-hidden="true" />
        <span>
          {{ stateLabel }}
          <template v-if="job">
            · 第 {{ Math.max(job.attempt_count, 1) }}/{{ job.max_attempts }} 次尝试
          </template>
        </span>
      </div>
      <dl v-else-if="batch.candidate_count">
        <div>
          <dt>候选</dt>
          <dd>{{ batch.candidate_count }}</dd>
        </div>
        <div>
          <dt>需校对</dt>
          <dd>{{ batch.counts.invalid }}</dd>
        </div>
        <div>
          <dt>已存在</dt>
          <dd>{{ batch.counts.duplicate }}</dd>
        </div>
      </dl>

      <StatusMessage
        :error="error"
        :success="resultText"
        :retry-label="error?.retryable ? '重试' : undefined"
        @retry="load()"
      />

      <p v-if="batch.state === 'failed'" class="message-proposal-error">
        <CircleAlert :size="15" aria-hidden="true" />
        {{ batch.error?.message ?? '课表内容未能处理。' }}
      </p>

      <div class="proposal-actions">
        <ElButton
          v-if="batch.state === 'needs_review' && validCandidates.length"
          type="primary"
          :loading="pending"
          :disabled="pending"
          @click="confirmAll"
        >
          <Check v-if="!pending" :size="16" aria-hidden="true" />
          {{ pending ? '正在确认…' : `确认导入 ${validCandidates.length} 条` }}
        </ElButton>
        <ElButton
          v-if="batch.state === 'needs_review'"
          plain
          @click="adjust"
        >
          <Pencil :size="16" aria-hidden="true" />
          调整
        </ElButton>
        <ElButton
          v-if="processing"
          plain
          :loading="cancelling"
          :disabled="cancelling"
          @click="cancel"
        >
          <XCircle :size="16" aria-hidden="true" />
          取消处理
        </ElButton>
        <ElButton
          v-if="batch.state === 'confirmed'"
          plain
          @click="openCalendar"
        >
          <CalendarCheck :size="16" aria-hidden="true" />
          在日历中查看
        </ElButton>
        <ElButton
          v-if="['failed', 'expired'].includes(batch.state)"
          link
          type="primary"
          @click="load()"
        >
          <RefreshCw :size="16" aria-hidden="true" />
          刷新
        </ElButton>
      </div>
    </template>
  </section>
</template>

