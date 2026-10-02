<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  Check,
  Download,
  FileClock,
  FileSpreadsheet,
  Pencil,
  RotateCw,
  SkipForward,
  Upload,
  X,
} from 'lucide-vue-next'
import CalendarImportCandidateEditor from './CalendarImportCandidateEditor.vue'
import CalendarScreenshotImport from './CalendarScreenshotImport.vue'
import StatusMessage from './StatusMessage.vue'
import { failure, localFailure, type Failure } from '../lib/api'
import {
  confirmCalendarImport,
  getCalendarImport,
  listCalendarImports,
  uploadCalendarFile,
  type CalendarImportBatch,
  type CalendarImportCandidate,
  type CalendarImportDetail,
  type ImportOutcome,
} from '../lib/calendar-imports'
import type { Term } from '../lib/calendar'

const props = defineProps<{
  open: boolean
  terms: Term[]
  timeZone: string
  initialDate: string
}>()

const emit = defineEmits<{
  close: []
  changed: [message: string, conflictCount: number]
}>()

const dialog = ref<HTMLDialogElement | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const history = ref<CalendarImportBatch[]>([])
const detail = ref<CalendarImportDetail | null>(null)
const file = ref<File | null>(null)
const selectedIds = ref<string[]>([])
const editingId = ref<string | null>(null)
const pending = ref(false)
const historyLoading = ref(false)
const detailLoading = ref(false)
const error = ref<Failure | null>(null)
const success = ref('')
const outcomes = ref<ImportOutcome[]>([])
const importMode = ref<'file' | 'screenshot'>('file')
const retryAction = ref<'history' | 'upload' | 'batch' | null>(null)
const lastBatchId = ref<string | null>(null)
let uploadOperation: { id: string; fingerprint: string } | null = null
let confirmOperation: { id: string; fingerprint: string } | null = null
let historyRevision = 0
let detailRevision = 0

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

const stateLabels: Record<CalendarImportCandidate['state'], string> = {
  valid: '可确认',
  invalid: '需校对',
  duplicate: '已存在',
  confirmed: '已导入',
  skipped: '已跳过',
}
const batchStateLabels: Record<CalendarImportBatch['state'], string> = {
  ready: '待确认',
  partially_confirmed: '部分完成',
  completed: '已完成',
  expired: '已过期',
}

watch(() => props.open, async open => {
  if (!open) {
    dialog.value?.close()
    return
  }
  error.value = null
  success.value = ''
  outcomes.value = []
  await nextTick()
  dialog.value?.showModal()
  if (dialog.value) dialog.value.scrollTop = 0
  await loadHistory()
  if (!detail.value) fileInput.value?.focus()
})

function close() {
  if (pending.value) return
  emit('close')
}

function readableDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(new Date(value))
}

function candidateName(candidate: CalendarImportCandidate) {
  const raw = candidate.raw_data.name ?? candidate.raw_data.summary
  return candidate.draft?.name || (typeof raw === 'string' ? raw : '') || `候选 ${candidate.position}`
}

function candidateTiming(candidate: CalendarImportCandidate) {
  const draft = candidate.draft
  if (!draft) return `文件中的第 ${candidate.position} 项`
  const date = draft.start_date || '待选择日期'
  if (draft.start_period_number) {
    return `${date} · 第 ${draft.start_period_number}-${draft.end_period_number} 节`
  }
  return `${date} · ${draft.starts_at?.slice(0, 5) ?? '--:--'}-${draft.ends_at?.slice(0, 5) ?? '--:--'}`
}

function canSelect(candidate: CalendarImportCandidate) {
  return candidate.state === 'valid'
    && !['completed', 'expired'].includes(batch.value?.state ?? 'ready')
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

function chooseFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
  error.value = null
  success.value = ''
  uploadOperation = null
}

function operationId(
  current: { id: string; fingerprint: string } | null,
  fingerprint: string,
) {
  return current?.fingerprint === fingerprint
    ? current
    : { id: crypto.randomUUID(), fingerprint }
}

async function loadHistory() {
  const revision = ++historyRevision
  historyLoading.value = true
  try {
    const items = await listCalendarImports()
    if (revision !== historyRevision) return
    history.value = items
    retryAction.value = null
  } catch (reason) {
    if (revision !== historyRevision) return
    error.value = failure(reason)
    retryAction.value = 'history'
  } finally {
    if (revision === historyRevision) historyLoading.value = false
  }
}

async function openBatch(batchId: string) {
  const revision = ++detailRevision
  lastBatchId.value = batchId
  detailLoading.value = true
  error.value = null
  success.value = ''
  outcomes.value = []
  editingId.value = null
  try {
    const result = await getCalendarImport(batchId)
    if (revision !== detailRevision) return
    detail.value = result
    selectDefaults(result.candidates)
    retryAction.value = null
  } catch (reason) {
    if (revision !== detailRevision) return
    error.value = failure(reason)
    retryAction.value = 'batch'
  } finally {
    if (revision === detailRevision) detailLoading.value = false
  }
}

async function openScreenshotBatch(batchId: string) {
  await openBatch(batchId)
  if (detail.value?.batch.id === batchId) {
    success.value = `已识别 ${detail.value.batch.candidate_count} 条候选，尚未写入日历。`
    await loadHistory()
  }
}

async function uploadFile() {
  if (pending.value) return
  if (!file.value) {
    error.value = localFailure('请选择 XLSX 或 ICS 文件。')
    return
  }
  const fingerprint = `${file.value.name}:${file.value.size}:${file.value.lastModified}`
  uploadOperation = operationId(uploadOperation, fingerprint)
  pending.value = true
  error.value = null
  success.value = ''
  outcomes.value = []
  try {
    const result = await uploadCalendarFile(file.value, uploadOperation.id)
    uploadOperation = null
    await openBatch(result.batch.id)
    success.value = `已读取 ${result.batch.candidate_count} 条候选，尚未写入日历。`
    retryAction.value = null
    await loadHistory()
  } catch (reason) {
    error.value = failure(reason)
    retryAction.value = 'upload'
  } finally {
    pending.value = false
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
    await openBatch(result.batch.id)
    outcomes.value = result.outcomes
    const created = result.outcomes.filter(outcome => outcome.outcome === 'created').length
    const skipped = result.outcomes.filter(outcome => outcome.outcome === 'skipped').length
    const merged = result.outcomes.filter(outcome => outcome.outcome === 'merged').length
    const parts = [
      created ? `导入 ${created} 条` : '',
      skipped ? `跳过 ${skipped} 条` : '',
      merged ? `合并 ${merged} 条重复项` : '',
    ].filter(Boolean)
    success.value = parts.join('，') || '候选状态已更新。'
    retryAction.value = null
    await loadHistory()
    await nextTick()
    if (dialog.value) dialog.value.scrollTop = 0
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
  if (retryAction.value === 'upload') {
    await uploadFile()
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
  const target = button ?? row
  target?.focus()
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
  <dialog ref="dialog" class="calendar-dialog calendar-import-dialog"
    aria-labelledby="calendar-import-title" @cancel.self.prevent="close">
    <div class="calendar-dialog-heading import-dialog-heading">
      <FileSpreadsheet :size="22" aria-hidden="true" />
      <div>
        <h2 id="calendar-import-title">导入日历</h2>
        <p>截图、Excel 与 ICS 都会先生成候选，确认后才写入日历。</p>
      </div>
      <button type="button" class="small-icon-button import-close"
        aria-label="关闭导入" title="关闭导入" :disabled="pending" @click="close">
        <X :size="18" aria-hidden="true" />
      </button>
    </div>

    <StatusMessage :error="error" :success="success"
      :retry-label="error?.retryable ? '重试' : undefined"
      @retry="retryFailure" />

    <fieldset class="segmented-field import-mode-switch">
      <legend class="sr-only">导入方式</legend>
      <label :class="{ selected: importMode === 'file' }">
        <input v-model="importMode" type="radio" value="file" />Excel / ICS
      </label>
      <label :class="{ selected: importMode === 'screenshot' }">
        <input v-model="importMode" type="radio" value="screenshot" />截图识别
      </label>
    </fieldset>

    <CalendarScreenshotImport
      v-if="importMode === 'screenshot'"
      :active="open"
      @batch-ready="openScreenshotBatch"
    />

    <section v-if="importMode === 'file'" class="import-upload" aria-labelledby="import-upload-title">
      <div>
        <h3 id="import-upload-title">选择文件</h3>
        <p>XLSX 最大 5 MiB，ICS 最大 2 MiB。</p>
      </div>
      <div class="import-upload-actions">
        <a class="button secondary compact-button"
          href="/api/v1/calendar/imports/template.xlsx" download>
          <Download :size="17" aria-hidden="true" />下载 Excel 模板
        </a>
        <label class="button secondary compact-button import-file-button">
          <FileSpreadsheet :size="17" aria-hidden="true" />
          <span>{{ file?.name ?? '选择文件' }}</span>
          <input ref="fileInput" type="file" accept=".xlsx,.ics,text/calendar"
            :disabled="pending" @change="chooseFile" />
        </label>
        <button type="button" class="button compact-button"
          :disabled="pending || !file" @click="uploadFile">
          <Upload :size="17" aria-hidden="true" />
          {{ pending ? '正在读取…' : '读取候选' }}
        </button>
      </div>
    </section>

    <section v-if="history.length" class="import-history" aria-labelledby="import-history-title">
      <div class="import-section-heading">
        <h3 id="import-history-title">最近导入</h3>
        <button type="button" class="small-icon-button" aria-label="刷新导入历史"
          title="刷新导入历史" :disabled="loading" @click="loadHistory">
          <RotateCw :size="17" aria-hidden="true" />
        </button>
      </div>
      <div class="import-history-list">
        <button v-for="item in history.slice(0, 5)" :key="item.id" type="button"
          :class="{ selected: batch?.id === item.id }" @click="openBatch(item.id)">
          <FileClock :size="17" aria-hidden="true" />
          <span>
            <strong>{{ item.filename }}</strong>
            <small>{{ readableDate(item.updated_at) }} · {{ item.candidate_count }} 条</small>
          </span>
          <span class="state-label" :data-state="item.state">
            {{ batchStateLabels[item.state] }}
          </span>
        </button>
      </div>
    </section>

    <section v-if="batch" class="import-review" aria-labelledby="import-review-title">
      <div class="import-section-heading">
        <div>
          <h3 id="import-review-title">{{ batch.filename }}</h3>
          <p>
            {{ batch.counts.valid }} 条可确认 · {{ batch.counts.invalid }} 条需校对 ·
            {{ batch.counts.duplicate }} 条已存在
          </p>
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

      <div v-if="!['completed', 'expired'].includes(batch.state)"
        class="import-selection-bar">
        <label class="checkbox-label">
          <input type="checkbox"
            :checked="selectedCandidates.length > 0 && selectedCandidates.length === Math.min(batch.counts.valid, 50)"
            :disabled="pending || !batch.counts.valid"
            @change="($event.target as HTMLInputElement).checked
              ? selectDefaults(candidates)
              : selectedIds = []" />
          选择可确认项
        </label>
        <span>已选 {{ selectedCandidates.length }}/50</span>
      </div>

      <ol class="import-candidate-list">
        <li v-for="candidate in candidates" :key="candidate.id"
          :class="[`candidate-${candidate.state}`, { editing: editingId === candidate.id }]"
          :data-import-candidate="candidate.id" tabindex="-1">
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
            <label class="import-candidate-select">
              <input type="checkbox" :checked="selectedIds.includes(candidate.id)"
                :disabled="pending || !canSelect(candidate)"
                :aria-label="`选择候选 ${candidate.position} ${candidateName(candidate)}`"
                @change="toggleCandidate(candidate.id, ($event.target as HTMLInputElement).checked)" />
            </label>
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
              <p v-for="warning in candidate.warnings" :key="`${warning.code}-${warning.message}`"
                class="import-candidate-warning">
                {{ warning.message }}
              </p>
            </div>
            <div v-if="!['completed', 'expired'].includes(batch.state)
              && !['confirmed', 'skipped'].includes(candidate.state)"
              class="import-candidate-actions">
              <button v-if="candidate.state !== 'duplicate'" type="button"
                class="small-icon-button" :aria-label="`校对 ${candidateName(candidate)}`"
                title="校对候选" :disabled="pending"
                :data-edit-candidate="candidate.id" @click="openEditor(candidate)">
                <Pencil :size="17" aria-hidden="true" />
              </button>
              <button type="button" class="small-icon-button"
                :aria-label="`跳过 ${candidateName(candidate)}`" title="跳过候选"
                :disabled="pending" @click="skipCandidate(candidate)">
                <SkipForward :size="17" aria-hidden="true" />
              </button>
            </div>
          </template>
        </li>
      </ol>

      <p v-if="batch.state === 'expired'" class="conflict-note">
        此批候选已超过校对期限，请重新上传原文件。
      </p>
      <p v-else-if="batch.state === 'completed'" class="import-complete-note">
        此批候选已经处理完成。
      </p>
      <div v-else class="import-confirm-bar">
        <span>{{ unresolvedCount }} 条仍待处理</span>
        <button type="button" class="button" :disabled="pending || !selectedCandidates.length"
          @click="confirmSelected">
          <Check :size="18" aria-hidden="true" />
          {{ pending ? '正在确认…' : `确认选中 ${selectedCandidates.length} 条` }}
        </button>
      </div>
    </section>

    <div class="dialog-actions import-dialog-footer">
      <button type="button" class="button secondary" :disabled="pending" @click="close">
        完成
      </button>
    </div>
  </dialog>
</template>
