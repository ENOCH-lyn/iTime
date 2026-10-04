<script setup lang="ts">
import {
  Check,
  ListPlus,
  LoaderCircle,
  Mic,
  RefreshCw,
  Square,
  Trash2,
  X,
} from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import StatusMessage from './StatusMessage.vue'
import {
  createVoiceRecorder,
  VoiceRecorderError,
} from '../lib/audio-recorder'
import { failure, localFailure, type Failure } from '../lib/api'
import { voiceWavDurationMs } from '../lib/pcm-wav'
import {
  getVoiceTranscription,
  listVoiceTranscriptions,
  resolveVoiceTranscription,
  sha256Blob,
  uploadVoiceTranscription,
  type VoiceTranscriptionPurpose,
  type VoiceTranscriptionResult,
} from '../lib/voice-transcriptions'

type Phase =
  | 'loading'
  | 'idle'
  | 'requesting'
  | 'recording'
  | 'preparing'
  | 'uploading'
  | 'transcribing'
  | 'ready'
  | 'resolving'
  | 'error'

const props = withDefaults(defineProps<{
  purpose: VoiceTranscriptionPurpose
  targetLabel: string
  fieldLimit: number
  currentText: string
  disabled?: boolean
}>(), { disabled: false })

const emit = defineEmits<{
  replace: [text: string]
  append: [text: string]
}>()

const phase = ref<Phase>('loading')
const current = ref<VoiceTranscriptionResult | null>(null)
const draft = ref('')
const elapsedMs = ref(0)
const error = ref<Failure | null>(null)
const notice = ref('')
const pendingBlob = ref<Blob | null>(null)
let pendingHash = ''
let uploadOperation: { id: string; fingerprint: string } | null = null
let resolveOperation: { id: string; fingerprint: string } | null = null
let uploadAbort: AbortController | null = null
let elapsedTimer = 0
let automaticStopTimer = 0
let pollTimer = 0
let pollDelay = 700
let flowRevision = 0
let mounted = false

const recorder = createVoiceRecorder(reason => {
  if (!mounted) return
  clearRecordingTimers()
  phase.value = 'error'
  error.value = localFailure(reason.message)
})

const draftLength = computed(() => Array.from(draft.value.trim()).length)
const hasCurrentText = computed(() => Boolean(props.currentText.trim()))
const replacementTooLong = computed(() => draftLength.value > props.fieldLimit)
const appendedText = computed(() => (
  [props.currentText.trim(), draft.value.trim()].filter(Boolean).join('\n')
))
const appendTooLong = computed(
  () => Array.from(appendedText.value).length > props.fieldLimit,
)
const activeJob = computed(() => (
  current.value
  && ['queued', 'running'].includes(current.value.job.status)
))
const statusText = computed(() => {
  switch (phase.value) {
    case 'loading': return '正在读取未完成的语音转写…'
    case 'requesting': return '正在等待麦克风权限…'
    case 'recording': return `正在录音 ${formatElapsed(elapsedMs.value)}，最长 01:00`
    case 'preparing': return '正在本机整理录音…'
    case 'uploading': return '正在安全上传录音…'
    case 'transcribing':
      return current.value?.job.status === 'running'
        ? '正在转写，离开页面后服务端仍会继续。'
        : '转写已排队，离开页面后服务端仍会继续。'
    case 'ready': return '转写草稿已就绪，应用前可以修改。'
    case 'resolving': return '正在处理转写草稿…'
    default: return notice.value
  }
})

function formatElapsed(milliseconds: number) {
  const totalSeconds = Math.min(60, Math.floor(milliseconds / 1000))
  return `${String(Math.floor(totalSeconds / 60)).padStart(2, '0')}:${String(
    totalSeconds % 60,
  ).padStart(2, '0')}`
}

function clearRecordingTimers() {
  window.clearInterval(elapsedTimer)
  window.clearTimeout(automaticStopTimer)
  elapsedTimer = 0
  automaticStopTimer = 0
}

function stopPolling() {
  window.clearTimeout(pollTimer)
  pollTimer = 0
}

function schedulePolling() {
  stopPolling()
  if (!mounted || document.hidden || !activeJob.value) return
  pollTimer = window.setTimeout(() => void refreshCurrent(true), pollDelay)
  pollDelay = Math.min(3_000, pollDelay + 500)
}

function clearTransientAudio() {
  pendingBlob.value = null
  pendingHash = ''
  uploadOperation = null
  uploadAbort = null
}

function clearCurrent(nextNotice = '') {
  stopPolling()
  current.value = null
  draft.value = ''
  resolveOperation = null
  clearTransientAudio()
  error.value = null
  notice.value = nextNotice
  phase.value = 'idle'
}

function jobFailure(result: VoiceTranscriptionResult): Failure {
  const terminal = result.job.status === 'timed_out'
    ? '转写等待超时，录音已清理。请重新录制或继续手工输入。'
    : result.job.error?.message
      ? `${result.job.error.message} 录音已清理，可重新录制或继续手工输入。`
      : '转写未能完成，录音已清理。请重新录制或继续手工输入。'
  return {
    code: result.job.error?.code ?? `voice_transcription_${result.job.status}`,
    message: terminal,
    request_id: null,
    saved: 'not_saved',
    retryable: true,
  }
}

function adoptResult(result: VoiceTranscriptionResult) {
  current.value = result
  error.value = null
  notice.value = ''
  if (result.transcription.resolution === 'accepted') {
    clearCurrent(`这份转写已应用到${props.targetLabel}。`)
    return
  }
  if (result.transcription.resolution === 'discarded') {
    clearCurrent('这份转写已丢弃。')
    return
  }
  if (result.transcription.resolution === 'expired') {
    current.value = result
    phase.value = 'error'
    error.value = localFailure('转写草稿已超过 24 小时，请重新录制。')
    stopPolling()
    return
  }
  if (
    result.job.status === 'succeeded'
    && result.transcription.transcript
    && result.transcription.transcript_sha256
  ) {
    if (phase.value !== 'ready' || current.value?.transcription.id !== result.transcription.id) {
      draft.value = result.transcription.transcript
    }
    phase.value = 'ready'
    stopPolling()
    return
  }
  if (['queued', 'running'].includes(result.job.status)) {
    phase.value = 'transcribing'
    schedulePolling()
    return
  }
  phase.value = 'error'
  error.value = jobFailure(result)
  stopPolling()
}

async function loadRecent(silent = false) {
  const revision = ++flowRevision
  if (!silent) {
    phase.value = 'loading'
    error.value = null
  }
  try {
    const result = await listVoiceTranscriptions(props.purpose)
    if (!mounted || revision !== flowRevision) return
    const recoverable = result.items.find(item => (
      item.transcription.resolution === 'pending'
      && Date.parse(item.transcription.expires_at) > Date.now()
    ))
    if (recoverable) adoptResult(recoverable)
    else if (!silent || current.value) clearCurrent()
  } catch (reason) {
    if (!mounted || revision !== flowRevision) return
    phase.value = 'error'
    error.value = failure(reason)
  }
}

async function refreshCurrent(silent = false) {
  if (!current.value) {
    await loadRecent(silent)
    return
  }
  const id = current.value.transcription.id
  const revision = ++flowRevision
  if (!silent) error.value = null
  try {
    const result = await getVoiceTranscription(id)
    if (!mounted || revision !== flowRevision) return
    pollDelay = result.job.status === current.value?.job.status ? pollDelay : 700
    adoptResult(result)
  } catch (reason) {
    if (!mounted || revision !== flowRevision) return
    phase.value = 'error'
    error.value = failure(reason)
  }
}

function recorderFailure(reason: unknown): Failure {
  if (reason instanceof VoiceRecorderError) {
    return localFailure(reason.message)
  }
  if (reason instanceof DOMException) {
    if (['NotAllowedError', 'SecurityError'].includes(reason.name)) {
      return localFailure(
        '未允许麦克风权限。请在浏览器地址栏开启权限，或继续手工输入。',
      )
    }
    if (reason.name === 'NotFoundError') {
      return localFailure('没有找到可用麦克风，请连接设备后重试。')
    }
    if (['NotReadableError', 'AbortError'].includes(reason.name)) {
      return localFailure('麦克风暂时不可用，请关闭占用设备的应用后重试。')
    }
  }
  return localFailure('无法开始录音，请检查麦克风后重试。')
}

async function startRecording() {
  if (props.disabled || ['requesting', 'recording', 'preparing'].includes(phase.value)) return
  if (current.value?.transcription.resolution === 'pending') {
    await discardCurrent(false)
    if (current.value) return
  }
  const revision = ++flowRevision
  stopPolling()
  clearTransientAudio()
  error.value = null
  notice.value = ''
  elapsedMs.value = 0
  phase.value = 'requesting'
  try {
    await recorder.start()
    if (!mounted || revision !== flowRevision) {
      await recorder.cancel()
      return
    }
    phase.value = 'recording'
    const startedAt = performance.now()
    elapsedTimer = window.setInterval(() => {
      elapsedMs.value = performance.now() - startedAt
    }, 100)
    automaticStopTimer = window.setTimeout(() => void stopRecording(), 60_000)
  } catch (reason) {
    if (!mounted || revision !== flowRevision) return
    phase.value = 'error'
    error.value = recorderFailure(reason)
  }
}

async function cancelLocalRecording() {
  if (!['requesting', 'recording', 'preparing'].includes(phase.value)) return
  flowRevision += 1
  clearRecordingTimers()
  await recorder.cancel()
  clearCurrent('录音已取消，原内容没有变化。')
}

async function stopRecording() {
  if (phase.value !== 'recording') return
  const revision = ++flowRevision
  clearRecordingTimers()
  phase.value = 'preparing'
  try {
    const blob = await recorder.stop()
    if (!mounted || revision !== flowRevision) return
    const durationMs = voiceWavDurationMs(blob)
    if (durationMs < 250) {
      throw new VoiceRecorderError(
        'audio_stop_failed',
        '录音不足 0.25 秒，请重新录制。',
      )
    }
    if (durationMs > 60_000 || blob.size > 2 * 1024 * 1024) {
      throw new VoiceRecorderError(
        'audio_stop_failed',
        '录音超过 60 秒或 2 MiB，请重新录制。',
      )
    }
    pendingBlob.value = blob
    pendingHash = await sha256Blob(blob)
    if (!mounted || revision !== flowRevision) return
    await uploadRecording(revision)
  } catch (reason) {
    if (!mounted || revision !== flowRevision) return
    phase.value = 'error'
    error.value = recorderFailure(reason)
  }
}

async function findUploadedRecording(contentHash: string) {
  const result = await listVoiceTranscriptions(props.purpose)
  return result.items.find(item => (
    item.transcription.resolution === 'pending'
    && item.job.input_summary.content_sha256 === contentHash
  )) ?? null
}

async function uploadRecording(existingRevision?: number) {
  const blob = pendingBlob.value
  if (!blob || !pendingHash) return
  const revision = existingRevision ?? ++flowRevision
  const fingerprint = `${props.purpose}:${pendingHash}:${blob.size}`
  uploadOperation = uploadOperation?.fingerprint === fingerprint
    ? uploadOperation
    : { id: crypto.randomUUID(), fingerprint }
  const operationId = uploadOperation.id
  uploadAbort?.abort()
  uploadAbort = new AbortController()
  phase.value = 'uploading'
  error.value = null
  try {
    const result = await uploadVoiceTranscription(
      blob,
      props.purpose,
      operationId,
      uploadAbort.signal,
    )
    if (!mounted || revision !== flowRevision) return
    clearTransientAudio()
    pollDelay = 700
    adoptResult(result)
  } catch (reason) {
    if (!mounted || revision !== flowRevision) return
    const currentError = failure(reason)
    if (currentError.saved === 'unknown') {
      try {
        const recovered = await findUploadedRecording(pendingHash)
        if (!mounted || revision !== flowRevision) return
        if (recovered) {
          clearTransientAudio()
          pollDelay = 700
          adoptResult(recovered)
          return
        }
      } catch {
        // Preserve the original upload failure and operation ID for exact replay.
      }
    }
    phase.value = 'error'
    error.value = currentError
  }
}

async function cancelSubmitted() {
  if (!current.value || current.value.transcription.resolution !== 'pending') return
  await discardCurrent(true)
}

async function cancelUnknownUpload() {
  if (phase.value !== 'uploading') return
  const hash = pendingHash
  flowRevision += 1
  uploadAbort?.abort()
  phase.value = 'preparing'
  notice.value = '正在确认录音是否已被服务端接收…'
  try {
    await new Promise(resolve => window.setTimeout(resolve, 250))
    const recovered = hash ? await findUploadedRecording(hash) : null
    if (recovered) {
      current.value = recovered
      await discardCurrent(true)
      return
    }
    clearCurrent('上传已停止，原内容没有变化。')
  } catch (reason) {
    phase.value = 'error'
    error.value = failure(reason)
  }
}

async function discardCurrent(showNotice = true) {
  const result = current.value
  if (!result) {
    clearCurrent(showNotice ? '转写草稿已丢弃，原内容没有变化。' : '')
    return
  }
  const item = result.transcription
  const fingerprint = `${item.id}:${item.version}:discard`
  resolveOperation = resolveOperation?.fingerprint === fingerprint
    ? resolveOperation
    : { id: crypto.randomUUID(), fingerprint }
  const revision = ++flowRevision
  phase.value = 'resolving'
  error.value = null
  try {
    const resolved = await resolveVoiceTranscription(
      item,
      'discard',
      resolveOperation.id,
    )
    if (!mounted || revision !== flowRevision) return
    if (resolved.transcription.resolution === 'accepted') {
      clearCurrent(`这份转写已在其他页面应用到${props.targetLabel}。`)
    } else {
      clearCurrent(showNotice ? '转写草稿已丢弃，原内容没有变化。' : '')
    }
  } catch (reason) {
    if (!mounted || revision !== flowRevision) return
    phase.value = result.job.status === 'succeeded' ? 'ready' : 'error'
    error.value = failure(reason)
    if (error.value.code === 'voice_transcription_version_conflict') {
      await refreshCurrent(true)
    }
  }
}

function validateDraft(action: 'replace' | 'append') {
  if (!draft.value.trim()) {
    error.value = localFailure('转写草稿不能为空。')
    return false
  }
  const tooLong = action === 'append' ? appendTooLong.value : replacementTooLong.value
  if (tooLong) {
    error.value = localFailure(
      `${action === 'append' ? '追加后' : ''}${props.targetLabel}不能超过 ${props.fieldLimit} 个字符，请先缩短转写草稿。`,
    )
    return false
  }
  return true
}

async function applyDraft(action: 'replace' | 'append') {
  const result = current.value
  if (!result || phase.value !== 'ready' || !validateDraft(action)) return
  const text = draft.value.trim()
  const item = result.transcription
  const fingerprint = `${item.id}:${item.version}:accept:${item.transcript_sha256}`
  resolveOperation = resolveOperation?.fingerprint === fingerprint
    ? resolveOperation
    : { id: crypto.randomUUID(), fingerprint }
  const revision = ++flowRevision
  phase.value = 'resolving'
  error.value = null
  const emitAppliedText = () => {
    if (action === 'replace') emit('replace', text)
    else emit('append', text)
    clearCurrent(`已应用到${props.targetLabel}，尚未保存。`)
  }
  try {
    const resolved = await resolveVoiceTranscription(
      item,
      'accept',
      resolveOperation.id,
    )
    if (!mounted || revision !== flowRevision) return
    if (resolved.transcription.resolution === 'accepted') {
      emitAppliedText()
      return
    }
    adoptResult(resolved)
  } catch (reason) {
    if (!mounted || revision !== flowRevision) return
    const currentError = failure(reason)
    if (currentError.saved === 'unknown') {
      try {
        const recovered = await getVoiceTranscription(item.id)
        if (!mounted || revision !== flowRevision) return
        if (recovered.transcription.resolution === 'accepted') {
          emitAppliedText()
          return
        }
        adoptResult(recovered)
      } catch {
        phase.value = 'ready'
      }
    } else {
      phase.value = 'ready'
    }
    error.value = currentError
  }
}

function visibilityChanged() {
  if (!mounted) return
  if (document.hidden) {
    stopPolling()
  } else if (current.value) {
    void refreshCurrent(true)
  } else {
    void loadRecent(true)
  }
}

onMounted(() => {
  mounted = true
  document.addEventListener('visibilitychange', visibilityChanged)
  window.addEventListener('focus', visibilityChanged)
  void loadRecent()
})

onBeforeUnmount(() => {
  mounted = false
  flowRevision += 1
  clearRecordingTimers()
  stopPolling()
  uploadAbort?.abort()
  document.removeEventListener('visibilitychange', visibilityChanged)
  window.removeEventListener('focus', visibilityChanged)
  void recorder.cancel()
})
</script>

<template>
  <section class="voice-transcription-control" :aria-label="`${targetLabel}语音输入`">
    <div class="voice-transcription-heading">
      <div class="voice-transcription-copy">
        <strong>语音输入</strong>
        <span>最长 60 秒，转写后先校对再应用。</span>
      </div>
      <div class="voice-capture-actions">
        <button
          v-if="phase === 'recording'"
          type="button"
          class="small-icon-button voice-stop"
          aria-label="停止录音"
          title="停止录音"
          :disabled="disabled"
          @click="stopRecording"
        ><Square :size="16" fill="currentColor" aria-hidden="true" /></button>
        <button
          v-if="['requesting', 'recording', 'preparing'].includes(phase)"
          type="button"
          class="small-icon-button"
          aria-label="取消录音"
          title="取消录音"
          :disabled="disabled"
          @click="cancelLocalRecording"
        ><X :size="18" aria-hidden="true" /></button>
        <button
          v-else-if="phase === 'uploading'"
          type="button"
          class="small-icon-button"
          aria-label="取消上传"
          title="取消上传"
          :disabled="disabled"
          @click="cancelUnknownUpload"
        ><X :size="18" aria-hidden="true" /></button>
        <button
          v-else-if="phase === 'transcribing'"
          type="button"
          class="small-icon-button"
          aria-label="取消转写"
          title="取消转写"
          :disabled="disabled"
          @click="cancelSubmitted"
        ><X :size="18" aria-hidden="true" /></button>
        <button
          v-else-if="!current || phase === 'error'"
          type="button"
          class="small-icon-button voice-record"
          :aria-label="phase === 'error' ? '重新录制语音' : '开始语音输入'"
          :title="phase === 'error' ? '重新录制语音' : '开始语音输入'"
          :disabled="disabled || phase === 'loading'"
          @click="startRecording"
        ><Mic :size="18" aria-hidden="true" /></button>
        <button
          v-if="current && !['recording', 'requesting', 'preparing', 'uploading'].includes(phase)"
          type="button"
          class="small-icon-button"
          aria-label="刷新转写状态"
          title="刷新转写状态"
          :disabled="disabled || phase === 'resolving'"
          @click="refreshCurrent()"
        ><RefreshCw :size="17" aria-hidden="true" /></button>
      </div>
    </div>

    <p
      v-if="statusText"
      class="voice-transcription-status"
      role="status"
      aria-live="polite"
    >
      <LoaderCircle
        v-if="['loading', 'requesting', 'preparing', 'uploading', 'transcribing', 'resolving'].includes(phase)"
        class="spinning"
        :size="16"
        aria-hidden="true"
      />
      <span>{{ statusText }}</span>
    </p>

    <StatusMessage
      :error="error"
      :retry-label="pendingBlob ? '重试原上传' : undefined"
      @retry="uploadRecording()"
    />

    <div v-if="phase === 'ready'" class="voice-transcription-draft">
      <div class="voice-draft-label">
        <label :for="`voice-draft-${purpose}`">转写草稿</label>
        <span :class="{ over: replacementTooLong }">
          {{ draftLength }}/{{ fieldLimit }}
        </span>
      </div>
      <textarea
        :id="`voice-draft-${purpose}`"
        v-model="draft"
        rows="3"
        maxlength="4000"
        :disabled="disabled"
        :aria-invalid="replacementTooLong"
      />
      <p v-if="hasCurrentText && appendTooLong" class="field-error">
        追加后超过 {{ fieldLimit }} 个字符，请先缩短草稿或选择替换。
      </p>
      <div class="voice-draft-actions">
        <button
          v-if="!hasCurrentText"
          type="button"
          class="button compact-button"
          :aria-label="`应用到${targetLabel}`"
          :disabled="disabled || replacementTooLong || !draft.trim()"
          @click="applyDraft('replace')"
        ><Check :size="16" aria-hidden="true" />应用</button>
        <template v-else>
          <button
            type="button"
            class="button compact-button"
            aria-label="替换当前内容"
            :disabled="disabled || replacementTooLong || !draft.trim()"
            @click="applyDraft('replace')"
          ><Check :size="16" aria-hidden="true" />替换</button>
          <button
            type="button"
            class="button secondary compact-button"
            aria-label="追加到当前内容"
            :disabled="disabled || appendTooLong || !draft.trim()"
            @click="applyDraft('append')"
          ><ListPlus :size="16" aria-hidden="true" />追加</button>
        </template>
        <button
          type="button"
          class="button secondary compact-button"
          :disabled="disabled"
          @click="discardCurrent()"
        ><Trash2 :size="16" aria-hidden="true" />丢弃</button>
      </div>
    </div>
  </section>
</template>
