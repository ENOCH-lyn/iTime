<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { ElMessageBox, type UploadFile, type UploadFiles, type UploadInstance } from 'element-plus'
import {
  Check,
  Download,
  FileClock,
  FileSpreadsheet,
  ImageUp,
  Link2,
  LoaderCircle,
  Pencil,
  RefreshCw,
  SkipForward,
  Upload,
  X,
  XCircle,
} from 'lucide-vue-next'
import CalendarImportCandidateEditor from './CalendarImportCandidateEditor.vue'
import CalendarSubscriptionManager from './CalendarSubscriptionManager.vue'
import StatusMessage from './StatusMessage.vue'
import { failure, localFailure, type Failure } from '../lib/api'
import {
  cancelCalendarImportJob,
  confirmCalendarImport,
  createAIImport,
  deleteCalendarImport,
  getCalendarImport,
  getCalendarImportJob,
  listCalendarImports,
  uploadCalendarFile,
  type CalendarAIImportJob,
  type CalendarImportBatch,
  type CalendarImportCandidate,
  type CalendarImportDetail,
  type ImportOutcome,
  type ImportSourceType,
} from '../lib/calendar-imports'
import type { Term } from '../lib/calendar'

type ImportMode = 'file' | 'image' | 'public_url' | 'text' | 'subscription'
type RetryAction = 'history' | 'submit' | 'batch' | null

const props = defineProps<{
  open: boolean
  terms: Term[]
  timeZone: string
  initialDate: string
  initialBatchId?: string | null
  subscriptionTarget?: { sourceId: string; eventId: string | null } | null
}>()

const emit = defineEmits<{
  close: []
  changed: [message: string, conflictCount: number]
}>()

const fileUpload = ref<UploadInstance | null>(null)
const imageUpload = ref<UploadInstance | null>(null)
const history = ref<CalendarImportBatch[]>([])
const detail = ref<CalendarImportDetail | null>(null)
const file = ref<File | null>(null)
const image = ref<File | null>(null)
const publicUrl = ref('')
const description = ref('')
const selectedIds = ref<string[]>([])
const editingId = ref<string | null>(null)
const currentJob = ref<CalendarAIImportJob | null>(null)
const pending = ref(false)
const cancelling = ref(false)
const historyLoading = ref(false)
const detailLoading = ref(false)
const error = ref<Failure | null>(null)
const success = ref('')
const outcomes = ref<ImportOutcome[]>([])
const importMode = ref<ImportMode>('file')
const retryAction = ref<RetryAction>(null)
const lastBatchId = ref<string | null>(null)
let submitOperation: { id: string; fingerprint: string } | null = null
let confirmOperation: { id: string; fingerprint: string } | null = null
let historyRevision = 0
let detailRevision = 0
let pollTimer = 0

const importModes = [
  { label: '文件', value: 'file' },
  { label: '图片', value: 'image' },
  { label: '网址', value: 'public_url' },
  { label: '文字', value: 'text' },
  { label: '订阅', value: 'subscription' },
]
const dialogTransition = { name: 'calendar-import-dialog-transition', duration: 0 }
const batch = computed(() => detail.value?.batch ?? null)
const loading = computed(() => historyLoading.value || detailLoading.value)
const candidates = computed(() => detail.value?.candidates ?? [])
const editingCandidate = computed(() => (
  candidates.value.find(candidate => candidate.id === editingId.value) ?? null
))
const selectedCandidates = computed(() => candidates.value.filter(candidate => (
  selectedIds.value.includes(candidate.id) && candidate.state === 'valid'
)))
const unresolvedCount = computed(() => (
  (batch.value?.counts.valid ?? 0) + (batch.value?.counts.invalid ?? 0)
))
const totalConflicts = computed(() => outcomes.value.reduce(
  (total, outcome) => total + (outcome.conflicts?.items.length ?? 0),
  0,
))
const processing = computed(() => (
  batch.value ? ['queued', 'processing'].includes(batch.value.state) : false
))
const canSubmit = computed(() => {
  if (pending.value) return false
  if (importMode.value === 'file') return !!file.value
  if (importMode.value === 'image') return !!image.value
  if (importMode.value === 'public_url') return !!publicUrl.value.trim()
  if (importMode.value === 'text') return !!description.value.trim()
  return false
})
const submitLabel = computed(() => {
  if (pending.value) return importMode.value === 'file' ? '正在读取…' : '正在提交…'
  return importMode.value === 'file' ? '读取候选' : '生成候选'
})

const stateLabels: Record<CalendarImportCandidate['state'], string> = {
  valid: '可确认',
  invalid: '需校对',
  duplicate: '已存在',
  confirmed: '已导入',
  skipped: '已跳过',
}
const batchStateLabels: Record<CalendarImportBatch['state'], string> = {
  queued: '等待处理',
  processing: '处理中',
  needs_review: '待确认',
  confirmed: '已完成',
  cancelled: '已取消',
  failed: '处理失败',
  expired: '已过期',
}
const jobStateLabels: Record<CalendarAIImportJob['status'], string> = {
  queued: '等待处理',
  running: '正在解析',
  succeeded: '解析完成',
  failed: '解析失败',
  timed_out: '处理超时',
  cancelled: '已取消',
}
const sourceLabels: Record<ImportSourceType, string> = {
  xlsx_file: 'Excel 文件',
  ics_file: 'ICS 文件',
  image: '课表图片',
  public_url: '公开网页',
  text: '文字描述',
}

watch(() => props.open, async open => {
  if (!open) {
    stopPolling()
    return
  }
  error.value = null
  success.value = ''
  outcomes.value = []
  if (props.subscriptionTarget) {
    importMode.value = 'subscription'
    return
  }
  const items = await loadHistory()
  const targetId = props.initialBatchId
    ?? detail.value?.batch.id
    ?? items.find(item => (
      ['queued', 'processing', 'needs_review'].includes(item.state)
    ))?.id
  if (targetId) await openBatch(targetId)
  await nextTick()
  focusCurrentSource()
})

watch(() => props.initialBatchId, async batchId => {
  if (props.open && batchId) await openBatch(batchId)
})

watch(importMode, () => {
  error.value = null
  success.value = ''
  submitOperation = null
  void nextTick(focusCurrentSource)
})

onBeforeUnmount(stopPolling)

function close() {
  if (pending.value || cancelling.value) return
  stopPolling()
  emit('close')
}

function focusCurrentSource() {
  const selector = {
    file: '.import-file-source input[type=file]',
    image: '.import-image-source input[type=file]',
    public_url: '#calendar-import-url',
    text: '#calendar-import-text',
    subscription: '.subscription-manager input',
  }[importMode.value]
  document.querySelector<HTMLElement>(selector)?.focus()
}

function readableDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(new Date(value))
}

function sourceName(item: CalendarImportBatch) {
  return item.source_display || item.filename || sourceLabels[item.source_type]
}

function candidateName(candidate: CalendarImportCandidate) {
  const raw = candidate.raw_data.name ?? candidate.raw_data.summary
  return candidate.draft?.name || (typeof raw === 'string' ? raw : '') || `候选 ${candidate.position}`
}

function candidateTiming(candidate: CalendarImportCandidate) {
  const draft = candidate.draft
  if (!draft) return `来源中的第 ${candidate.position} 项`
  const date = draft.start_date || '待选择日期'
  if (draft.start_period_number) {
    return `${date} · 第 ${draft.start_period_number}-${draft.end_period_number} 节`
  }
  return `${date} · ${draft.starts_at?.slice(0, 5) ?? '--:--'}-${draft.ends_at?.slice(0, 5) ?? '--:--'}`
}

function canSelect(candidate: CalendarImportCandidate) {
  return candidate.state === 'valid' && batch.value?.state === 'needs_review'
}

function selectDefaults(items: CalendarImportCandidate[]) {
  selectedIds.value = items
    .filter(canSelect)
    .slice(0, 50)
    .map(candidate => candidate.id)
}

function toggleCandidate(id: string, checked: boolean) {
  if (checked) {
    if (selectedIds.value.length >= 50) {
      error.value = localFailure('每次最多确认 50 条，请分批处理。')
      return
    }
    selectedIds.value = [...selectedIds.value, id]
  } else {
    selectedIds.value = selectedIds.value.filter(value => value !== id)
  }
}

function selectAll(checked: boolean) {
  selectedIds.value = checked
    ? candidates.value.filter(canSelect).slice(0, 50).map(candidate => candidate.id)
    : []
}

function operationId(
  current: { id: string; fingerprint: string } | null,
  fingerprint: string,
) {
  return current?.fingerprint === fingerprint
    ? current
    : { id: crypto.randomUUID(), fingerprint }
}

function onFileChange(uploadFile: UploadFile, _files: UploadFiles) {
  file.value = uploadFile.raw ?? null
  error.value = null
  success.value = ''
  submitOperation = null
}

function onImageChange(uploadFile: UploadFile, _files: UploadFiles) {
  image.value = uploadFile.raw ?? null
  error.value = null
  success.value = ''
  submitOperation = null
}

function clearFile() {
  file.value = null
  fileUpload.value?.clearFiles()
  submitOperation = null
}

function clearImage() {
  image.value = null
  imageUpload.value?.clearFiles()
  submitOperation = null
}

async function loadHistory() {
  const revision = ++historyRevision
  historyLoading.value = true
  try {
    const items = await listCalendarImports()
    if (revision !== historyRevision) return history.value
    history.value = items
    retryAction.value = null
    return items
  } catch (reason) {
    if (revision !== historyRevision) return history.value
    error.value = failure(reason)
    retryAction.value = 'history'
    return history.value
  } finally {
    if (revision === historyRevision) historyLoading.value = false
  }
}

async function deleteHistoryBatch(item: CalendarImportBatch) {
  try {
    await ElMessageBox.confirm(
      `删除“${sourceName(item)}”的导入记录？候选不会再显示。`,
      '删除导入记录',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await deleteCalendarImport(item.id)
    history.value = history.value.filter(entry => entry.id !== item.id)
    if (batch.value?.id === item.id) {
      detail.value = null
      selectedIds.value = []
      lastBatchId.value = null
    }
    success.value = '导入记录已删除。'
    await loadHistory()
  } catch (reason) {
    error.value = failure(reason)
  }
}

function modeForSource(sourceType: ImportSourceType): ImportMode {
  if (sourceType === 'image') return 'image'
  if (sourceType === 'public_url') return 'public_url'
  if (sourceType === 'text') return 'text'
  return 'file'
}

async function openBatch(batchId: string, silent = false) {
  const revision = ++detailRevision
  lastBatchId.value = batchId
  detailLoading.value = true
  if (!silent) {
    error.value = null
    success.value = ''
    outcomes.value = []
  }
  editingId.value = null
  try {
    const result = await getCalendarImport(batchId)
    if (revision !== detailRevision) return
    detail.value = result
    importMode.value = modeForSource(result.batch.source_type)
    selectDefaults(result.candidates)
    retryAction.value = null
    if (
      props.open
      && result.batch.job_id
      && ['queued', 'processing'].includes(result.batch.state)
    ) {
      await refreshJob(result.batch.job_id)
    } else {
      currentJob.value = null
      stopPolling()
    }
  } catch (reason) {
    if (revision !== detailRevision) return
    error.value = failure(reason)
    retryAction.value = 'batch'
  } finally {
    if (revision === detailRevision) detailLoading.value = false
  }
}

function stopPolling() {
  window.clearTimeout(pollTimer)
  pollTimer = 0
}

function schedulePoll() {
  stopPolling()
  if (!props.open || !processing.value || !batch.value?.job_id) return
  pollTimer = window.setTimeout(() => {
    void pollCurrent()
  }, 800)
}

async function refreshJob(jobId: string) {
  try {
    currentJob.value = await getCalendarImportJob(jobId)
  } catch {
    currentJob.value = null
  }
  schedulePoll()
}

async function pollCurrent() {
  const currentBatch = batch.value
  if (!currentBatch?.job_id || !props.open) return
  try {
    currentJob.value = await getCalendarImportJob(currentBatch.job_id)
    const terminal = ['succeeded', 'failed', 'timed_out', 'cancelled']
      .includes(currentJob.value.status)
    if (terminal) {
      await openBatch(currentBatch.id, true)
      await loadHistory()
      if (detail.value?.batch.state === 'needs_review') {
        success.value = `已生成 ${detail.value.batch.candidate_count} 条候选，尚未写入日历。`
      }
      return
    }
    await openBatch(currentBatch.id, true)
  } catch (reason) {
    error.value = failure(reason)
    retryAction.value = 'batch'
  }
  schedulePoll()
}

async function submitSource() {
  if (!canSubmit.value) {
    error.value = localFailure('请先填写来源内容。')
    return
  }
  let fingerprint = ''
  if (importMode.value === 'file' && file.value) {
    fingerprint = `file:${file.value.name}:${file.value.size}:${file.value.lastModified}`
  } else if (importMode.value === 'image' && image.value) {
    fingerprint = `image:${image.value.name}:${image.value.size}:${image.value.lastModified}`
  } else if (importMode.value === 'public_url') {
    const value = publicUrl.value.trim()
    try {
      const url = new URL(value)
      const localTestUrl = window.location.hostname === 'localhost'
        && url.protocol === 'http:'
        && url.hostname === 'localhost'
      if (url.protocol !== 'https:' && !localTestUrl) {
        throw new Error('invalid_protocol')
      }
    } catch {
      error.value = localFailure('请输入可公开访问的 HTTPS 地址。')
      return
    }
    fingerprint = `public_url:${value}`
  } else {
    fingerprint = `text:${description.value.trim()}`
  }
  submitOperation = operationId(submitOperation, fingerprint)
  pending.value = true
  error.value = null
  success.value = ''
  outcomes.value = []
  try {
    if (importMode.value === 'file' && file.value) {
      const result = await uploadCalendarFile(file.value, submitOperation.id)
      submitOperation = null
      await openBatch(result.batch.id)
      success.value = `已读取 ${result.batch.candidate_count} 条候选，尚未写入日历。`
    } else {
      const result = importMode.value === 'image' && image.value
        ? await createAIImport(
            { sourceType: 'image', file: image.value },
            submitOperation.id,
          )
        : await createAIImport(
            {
              sourceType: importMode.value as 'public_url' | 'text',
              content: importMode.value === 'public_url'
                ? publicUrl.value.trim()
                : description.value.trim(),
            },
            submitOperation.id,
          )
      submitOperation = null
      detail.value = { batch: result.batch, candidates: [] }
      currentJob.value = result.job
      lastBatchId.value = result.batch.id
      success.value = '已提交，处理完成后可校对。'
      schedulePoll()
    }
    retryAction.value = null
    await loadHistory()
  } catch (reason) {
    error.value = failure(reason)
    retryAction.value = 'submit'
  } finally {
    pending.value = false
  }
}

async function cancelProcessing() {
  if (!batch.value?.job_id || cancelling.value) return
  cancelling.value = true
  error.value = null
  try {
    const result = await cancelCalendarImportJob(batch.value.job_id)
    currentJob.value = result.job
    stopPolling()
    await openBatch(batch.value.id, true)
    await loadHistory()
    success.value = '处理已取消。'
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    cancelling.value = false
  }
}

async function refreshCurrent() {
  const batchId = batch.value?.id ?? lastBatchId.value
  if (!batchId) return
  await openBatch(batchId)
}

async function applyDecisions(
  decisions: {
    candidate_id: string
    expected_version: number
    action: 'confirm' | 'skip'
  }[],
) {
  if (!batch.value || pending.value) return
  const fingerprint = JSON.stringify({
    batch: batch.value.id,
    version: batch.value.version,
    decisions,
  })
  confirmOperation = operationId(confirmOperation, fingerprint)
  pending.value = true
  error.value = null
  success.value = ''
  try {
    const result = await confirmCalendarImport(
      batch.value,
      decisions,
      confirmOperation.id,
    )
    confirmOperation = null
    await openBatch(result.batch.id, true)
    outcomes.value = result.outcomes
    const created = result.outcomes.filter(outcome => outcome.outcome === 'created').length
    const skipped = result.outcomes.filter(outcome => outcome.outcome === 'skipped').length
    const merged = result.outcomes.filter(outcome => outcome.outcome === 'merged').length
    const parts = [
      created ? `导入 ${created} 条` : '',
      skipped ? `跳过 ${skipped} 条` : '',
      merged ? `合并 ${merged} 条重复项` : '',
    ].filter(Boolean)
    pending.value = false
    success.value = parts.join('，') || '候选状态已更新。'
    retryAction.value = null
    await loadHistory()
    if (created) emit('changed', success.value, totalConflicts.value)
  } catch (reason) {
    const currentError = failure(reason)
    if (currentError.code.includes('version_conflict')) await refreshCurrent()
    error.value = currentError
    retryAction.value = 'batch'
  } finally {
    pending.value = false
  }
}

async function confirmSelected() {
  if (!selectedCandidates.value.length) {
    error.value = localFailure('请至少选择一条可确认候选。')
    return
  }
  await applyDecisions(selectedCandidates.value.map(candidate => ({
    candidate_id: candidate.id,
    expected_version: candidate.version,
    action: 'confirm',
  })))
}

async function skipCandidate(candidate: CalendarImportCandidate) {
  await applyDecisions([{
    candidate_id: candidate.id,
    expected_version: candidate.version,
    action: 'skip',
  }])
}

async function retryFailure() {
  if (retryAction.value === 'submit') {
    await submitSource()
  } else if (retryAction.value === 'batch') {
    await refreshCurrent()
  } else {
    await loadHistory()
  }
}

async function openEditor(candidate: CalendarImportCandidate) {
  editingId.value = candidate.id
  await nextTick()
  document.getElementById(`import-candidate-${candidate.id}-name`)?.focus()
}

async function closeEditor(candidateId: string) {
  editingId.value = null
  await nextTick()
  const button = document.querySelector<HTMLElement>(
    `[data-edit-candidate="${candidateId}"]`,
  )
  const row = document.querySelector<HTMLElement>(
    `[data-import-candidate="${candidateId}"]`,
  )
  ;(button ?? row)?.focus()
}

function onCandidateUpdated(
  nextBatch: CalendarImportBatch,
  candidate: CalendarImportCandidate,
) {
  if (!detail.value) return
  detail.value = {
    batch: nextBatch,
    candidates: detail.value.candidates.map(item => (
      item.id === candidate.id ? candidate : item
    )),
  }
  selectedIds.value = candidate.state === 'valid'
    ? [...new Set([...selectedIds.value, candidate.id])].slice(0, 50)
    : selectedIds.value.filter(id => id !== candidate.id)
  success.value = candidate.state === 'valid'
    ? '候选已校对，可加入本次确认。'
    : candidate.state === 'duplicate'
      ? '候选与已有来源重复，不会再次创建。'
      : ''
}
</script>

<template>
  <ElDialog
    class="calendar-import-dialog"
    :class="{ 'subscription-mode': importMode === 'subscription' }"
    :model-value="open"
    title="导入日历"
    aria-label="导入日历"
    width="min(980px, calc(100vw - 32px))"
    top="16px"
    :close-on-click-modal="!pending && !cancelling"
    :close-on-press-escape="!pending && !cancelling"
    :show-close="!pending && !cancelling"
    :transition="dialogTransition"
    append-to-body
    @update:model-value="value => { if (!value) close() }"
  >
    <template #header="{ titleId, titleClass }">
      <div class="import-dialog-heading">
        <FileSpreadsheet :size="22" aria-hidden="true" />
        <h2 :id="titleId" :class="titleClass">导入日历</h2>
      </div>
    </template>

    <StatusMessage
      :error="error"
      :success="success"
      :retry-label="error?.retryable ? '重试' : undefined"
      @retry="retryFailure"
    />

    <ElSegmented
      v-model="importMode"
      class="import-mode-switch"
      :options="importModes"
      aria-label="导入方式"
    />

    <CalendarSubscriptionManager
      v-if="importMode === 'subscription'"
      :active="open"
      :target-source-id="subscriptionTarget?.sourceId ?? null"
      :target-event-id="subscriptionTarget?.eventId ?? null"
      @changed="emit('changed', $event, 0)"
    />

    <template v-else>
      <section class="import-source" aria-labelledby="import-source-title">
        <div class="import-source-heading">
          <div>
            <h3 id="import-source-title">
              {{ importMode === 'file'
                ? '上传文件'
                : importMode === 'image'
                  ? '上传图片'
                  : importMode === 'public_url'
                    ? '公开网址'
                    : '描述课表' }}
            </h3>
            <p v-if="importMode === 'file'">支持 XLSX 和 ICS。</p>
            <p v-else-if="importMode === 'image'">支持 PNG、JPEG 和 WebP，最大 5 MiB。</p>
            <p v-else-if="importMode === 'public_url'">仅支持无需登录的 HTTPS 页面。</p>
            <p v-else>写明日期范围、星期和时间。</p>
          </div>
          <ElButton
            v-if="importMode === 'file'"
            tag="a"
            href="/api/v1/calendar/imports/template.xlsx"
            download
          >
            <Download :size="17" aria-hidden="true" />
            下载模板
          </ElButton>
        </div>

        <template v-if="importMode === 'file'">
          <ElUpload
            ref="fileUpload"
            class="import-upload-control import-file-source"
            :auto-upload="false"
            :limit="1"
            :show-file-list="false"
            accept=".xlsx,.ics,text/calendar"
            :on-change="onFileChange"
          >
            <span class="import-upload-trigger">
              <FileSpreadsheet :size="17" aria-hidden="true" />
              选择文件
            </span>
          </ElUpload>
          <div v-if="file" class="import-selected-file">
            <FileSpreadsheet :size="17" aria-hidden="true" />
            <span>{{ file.name }}</span>
            <ElButton text circle aria-label="移除文件" title="移除文件" @click="clearFile">
              <X :size="17" aria-hidden="true" />
            </ElButton>
          </div>
        </template>

        <template v-else-if="importMode === 'image'">
          <ElUpload
            ref="imageUpload"
            class="import-upload-control import-image-source"
            drag
            :auto-upload="false"
            :limit="1"
            :show-file-list="false"
            accept="image/png,image/jpeg,image/webp"
            :on-change="onImageChange"
          >
            <ImageUp :size="24" aria-hidden="true" />
            <div class="el-upload__text">拖入图片，或<em>选择图片</em></div>
          </ElUpload>
          <div v-if="image" class="import-selected-file">
            <ImageUp :size="17" aria-hidden="true" />
            <span>{{ image.name }}</span>
            <ElButton text circle aria-label="移除图片" title="移除图片" @click="clearImage">
              <X :size="17" aria-hidden="true" />
            </ElButton>
          </div>
        </template>

        <ElInput
          v-else-if="importMode === 'public_url'"
          id="calendar-import-url"
          v-model="publicUrl"
          type="url"
          maxlength="2048"
          clearable
          placeholder="https://example.com/timetable"
          aria-label="公开课表网址"
        >
          <template #prefix>
            <Link2 :size="17" aria-hidden="true" />
          </template>
        </ElInput>

        <ElInput
          v-else
          id="calendar-import-text"
          v-model="description"
          type="textarea"
          :autosize="{ minRows: 4, maxRows: 8 }"
          maxlength="12000"
          show-word-limit
          placeholder="例如：每周一、三 08:00–09:30 上高等数学，持续到 1 月 10 日。"
          aria-label="课表文字描述"
        />

        <div class="import-source-actions">
          <ElButton
            type="primary"
            :loading="pending"
            :disabled="!canSubmit"
            @click="submitSource"
          >
            <Upload v-if="!pending" :size="17" aria-hidden="true" />
            {{ submitLabel }}
          </ElButton>
        </div>
      </section>

      <section
        v-if="batch && processing"
        class="import-processing"
        aria-labelledby="import-processing-title"
      >
        <LoaderCircle :size="20" class="spin" aria-hidden="true" />
        <div>
          <h3 id="import-processing-title">
            {{ currentJob ? jobStateLabels[currentJob.status] : batchStateLabels[batch.state] }}
          </h3>
          <p>
            {{ sourceName(batch) }}
            <template v-if="currentJob">
              · 第 {{ Math.max(currentJob.attempt_count, 1) }}/{{ currentJob.max_attempts }} 次尝试
            </template>
          </p>
        </div>
        <ElButton
          plain
          :loading="cancelling"
          :disabled="cancelling"
          @click="cancelProcessing"
        >
          <XCircle :size="17" aria-hidden="true" />
          取消处理
        </ElButton>
      </section>

      <section v-if="history.length" class="import-history" aria-labelledby="import-history-title">
        <div class="import-section-heading">
          <h3 id="import-history-title">最近导入</h3>
          <ElButton
            text
            circle
            aria-label="刷新导入记录"
            title="刷新导入记录"
            :disabled="loading"
            @click="loadHistory"
          >
            <RefreshCw :size="17" aria-hidden="true" />
          </ElButton>
        </div>
        <div class="import-history-list">
          <ElButton
            v-for="item in history.slice(0, 5)"
            :key="item.id"
            :class="{ selected: batch?.id === item.id }"
            @click="openBatch(item.id)"
          >
            <FileClock :size="17" aria-hidden="true" />
            <span class="import-history-copy">
              <strong>{{ sourceName(item) }}</strong>
              <small>
                {{ sourceLabels[item.source_type] }} · {{ readableDate(item.updated_at) }} ·
                {{ item.candidate_count }} 条
              </small>
            </span>
            <span class="state-label" :data-state="item.state">
              {{ batchStateLabels[item.state] }}
            </span>
            <span
              class="history-delete"
              role="button"
              tabindex="0"
              :aria-label="`删除${sourceName(item)}的导入记录`"
              :title="`删除${sourceName(item)}的导入记录`"
              @click.stop="deleteHistoryBatch(item)"
              @keydown.enter.stop="deleteHistoryBatch(item)"
              @keydown.space.prevent.stop="deleteHistoryBatch(item)"
            >
              <X :size="15" aria-hidden="true" />
            </span>
          </ElButton>
        </div>
      </section>

      <section v-if="batch" class="import-review" aria-labelledby="import-review-title">
        <div class="import-section-heading">
          <div>
            <h3 id="import-review-title">{{ sourceName(batch) }}</h3>
            <p v-if="batch.state === 'needs_review'">
              {{ batch.counts.valid }} 条可确认 · {{ batch.counts.invalid }} 条需校对 ·
              {{ batch.counts.duplicate }} 条已存在
            </p>
            <p v-else>{{ sourceLabels[batch.source_type] }} · {{ batch.candidate_count }} 条候选</p>
          </div>
          <span class="state-label" :data-state="batch.state">
            {{ batchStateLabels[batch.state] }}
          </span>
        </div>

        <div v-if="outcomes.length" class="import-outcome" role="status">
          <Check :size="18" aria-hidden="true" />
          <p>
            本次处理 {{ outcomes.length }} 条
            <span v-if="totalConflicts">，发现 {{ totalConflicts }} 处时间冲突，均已保留。</span>
          </p>
        </div>

        <div v-if="batch.state === 'needs_review'" class="import-selection-bar">
          <ElCheckbox
            :model-value="selectedCandidates.length > 0
              && selectedCandidates.length === Math.min(batch.counts.valid, 50)"
            :indeterminate="selectedCandidates.length > 0
              && selectedCandidates.length < Math.min(batch.counts.valid, 50)"
            :disabled="pending || !batch.counts.valid"
            @change="selectAll(Boolean($event))"
          >
            选择可确认项
          </ElCheckbox>
          <span>已选 {{ selectedCandidates.length }}/50</span>
        </div>

        <ol v-if="candidates.length" class="import-candidate-list">
          <li
            v-for="candidate in candidates"
            :key="candidate.id"
            :class="[`candidate-${candidate.state}`, { editing: editingId === candidate.id }]"
            :data-import-candidate="candidate.id"
            tabindex="-1"
          >
            <CalendarImportCandidateEditor
              v-if="editingId === candidate.id && editingCandidate"
              :batch="batch"
              :candidate="editingCandidate"
              :terms="terms"
              :time-zone="timeZone"
              :initial-date="initialDate"
              @cancel="closeEditor(candidate.id)"
              @updated="onCandidateUpdated"
            />
            <template v-else>
              <ElCheckbox
                class="import-candidate-select"
                :model-value="selectedIds.includes(candidate.id)"
                :disabled="pending || !canSelect(candidate)"
                :aria-label="`选择候选 ${candidate.position} ${candidateName(candidate)}`"
                @change="toggleCandidate(candidate.id, Boolean($event))"
              />
              <div class="import-candidate-copy">
                <div class="identity-line">
                  <strong>{{ candidateName(candidate) }}</strong>
                  <span class="state-label" :data-state="candidate.state">
                    {{ stateLabels[candidate.state] }}
                  </span>
                </div>
                <p>{{ candidateTiming(candidate) }}</p>
                <p v-if="candidate.error" class="import-candidate-error">
                  {{ candidate.error.message }}
                </p>
                <p
                  v-for="warning in candidate.warnings"
                  :key="`${warning.code}-${warning.message}`"
                  class="import-candidate-warning"
                >
                  {{ warning.message }}
                </p>
              </div>
              <div
                v-if="batch.state === 'needs_review'
                  && !['confirmed', 'skipped'].includes(candidate.state)"
                class="import-candidate-actions"
              >
                <ElButton
                  v-if="candidate.state !== 'duplicate'"
                  text
                  circle
                  :aria-label="`校对 ${candidateName(candidate)}`"
                  title="校对候选"
                  :disabled="pending"
                  :data-edit-candidate="candidate.id"
                  @click="openEditor(candidate)"
                >
                  <Pencil :size="17" aria-hidden="true" />
                </ElButton>
                <ElButton
                  text
                  circle
                  :aria-label="`跳过 ${candidateName(candidate)}`"
                  title="跳过候选"
                  :disabled="pending"
                  @click="skipCandidate(candidate)"
                >
                  <SkipForward :size="17" aria-hidden="true" />
                </ElButton>
              </div>
            </template>
          </li>
        </ol>

        <div v-if="batch.state === 'failed'" class="import-terminal-note error-status">
          <XCircle :size="18" aria-hidden="true" />
          <p>{{ batch.error?.message ?? '课表内容未能处理，请重新提交。' }}</p>
        </div>
        <p v-else-if="batch.state === 'cancelled'" class="import-terminal-note">
          此批处理已取消。
        </p>
        <p v-else-if="batch.state === 'expired'" class="import-terminal-note">
          此批候选已过期，请重新提交来源。
        </p>
        <p v-else-if="batch.state === 'confirmed'" class="import-terminal-note">
          此批候选已经处理完成。
        </p>

        <div v-if="batch.state === 'needs_review'" class="import-confirm-bar">
          <span>{{ unresolvedCount }} 条仍待处理</span>
          <ElButton
            type="primary"
            :loading="pending"
            :disabled="pending || !selectedCandidates.length"
            @click="confirmSelected"
          >
            <Check v-if="!pending" :size="18" aria-hidden="true" />
            {{ pending ? '正在确认…' : `确认选中 ${selectedCandidates.length} 条` }}
          </ElButton>
        </div>
      </section>
    </template>

    <template #footer>
      <ElButton :disabled="pending || cancelling" @click="close">
        {{ processing ? '关闭' : '完成' }}
      </ElButton>
    </template>
  </ElDialog>
</template>
