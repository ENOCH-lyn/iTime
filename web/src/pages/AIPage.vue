<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { CirclePause } from 'lucide-vue-next'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import ConversationComposer from '../components/ConversationComposer.vue'
import ConversationSidebar from '../components/ConversationSidebar.vue'
import ConversationViewport from '../components/ConversationViewport.vue'
import StatusMessage from '../components/StatusMessage.vue'
import AppShell from '../shell/AppShell.vue'
import { useAppShell } from '../shell/useAppShell'
import { failure, localFailure, type Failure } from '../lib/api'
import {
  authError,
  authLoading,
  identity,
  refreshSession,
} from '../lib/auth'
import { cancelAIJob } from '../lib/ai-jobs'
import { extractAIDocument, uploadAIImage } from '../lib/ai-documents'
import {
  uploadCalendarFile,
} from '../lib/calendar-imports'
import {
  createConversation,
  getConversation,
  jobEventsUrl,
  listConversationMessages,
  listConversations,
  sendConversationMessage,
  updateConversation,
  type AIJobEvent,
  type Conversation,
  type ConversationMessage,
  type ConversationPage,
} from '../lib/conversations'
import { useSyncRefresh } from '../lib/use-sync-refresh'
import { getDifficulty } from '../lib/execution'
import '../ai-workspace.css'

const route = useRoute()
const router = useRouter()
const conversations = ref<Conversation[]>([])
const conversationCursor = ref<ConversationPage['next_cursor']>(null)
const archivedView = ref(false)
const selected = ref<Conversation | null>(null)
const messages = ref<ConversationMessage[]>([])
const olderCursor = ref<number | null>(null)
const composer = ref('')
const composerAttachment = ref<File | null>(null)
const loadingList = ref(false)
const loadingThread = ref(false)
const loadingOlder = ref(false)
const creating = ref(false)
const sending = ref(false)
const cancelling = ref(false)
const { pendingLogout, logoutError, logout } = useAppShell()
const error = ref<Failure | null>(null)
const success = ref('')
const renameOpen = ref(false)
const renameTarget = ref<Conversation | null>(null)
const renameTitle = ref('')
const renamePending = ref(false)
const renameError = ref('')
const archiveOpen = ref(false)
const archiveTarget = ref<Conversation | null>(null)
const archiveBusy = ref(false)
const visualHeight = ref(window.visualViewport?.height ?? window.innerHeight)
const viewport = ref<{
  scrollToLatest: (force?: boolean) => Promise<void>
} | null>(null)
let listRevision = 0
let threadRevision = 0
let eventSource: EventSource | null = null
let eventGeneration = 0
let lastEventSequence = 0
let recoveryTimer = 0
let successTimer = 0
let recoveryDelay = 1000
let createOperation = ''
let createPromise: Promise<Conversation | null> | null = null
let sendOperation: { id: string; fingerprint: string } | null = null
let attachmentOperation: { id: string; fingerprint: string } | null = null
let updateOperation: { id: string; fingerprint: string } | null = null

const activeJob = computed(() => selected.value?.active_job ?? null)
const composerDisabled = computed(() => (
  !!selected.value?.archived
  || identity.value?.user.ai_access === 'frozen'
))
const pageTitle = computed(() => selected.value?.title?.trim() || 'AI 助手')

function suggestPrompt(text: string) {
  composer.value = text
  void nextTick(() => {
    document.querySelector<HTMLTextAreaElement>('#conversation-message')?.focus()
  })
}

function onConversationCommand(command: string | number | object) {
  if (!selected.value) return
  if (command === 'rename') openRename(selected.value)
  else if (command === 'archive') askToggleArchive(selected.value)
}

watch(success, value => {
  window.clearTimeout(successTimer)
  if (!value) return
  successTimer = window.setTimeout(() => {
    success.value = ''
  }, 6000)
})

watch(authError, value => {
  if (value?.code === 'unauthenticated') void router.replace('/login')
})

onMounted(async () => {
  window.visualViewport?.addEventListener('resize', updateVisualHeight)
  document.addEventListener('keydown', handleComposerKeydown, true)
  document.title = 'AI 对话 · iTime'
  await refreshSession()
  if (!identity.value) {
    await router.replace('/login')
    return
  }
  await loadConversationList()
  const requested = typeof route.query.conversation === 'string'
    ? conversations.value.find(item => item.id === route.query.conversation)
    : null
  if (requested) await selectConversation(requested)
  const difficultyId = typeof route.query.difficulty === 'string'
    ? route.query.difficulty
    : null
  if (difficultyId) await prepareDifficultyDraft(difficultyId)
  const prompt = typeof route.query.prompt === 'string'
    ? route.query.prompt.trim()
    : ''
  if (prompt) {
    composer.value = prompt
    await send()
    await router.replace({
      path: '/ai',
      query: selected.value ? { conversation: selected.value.id } : {},
    })
  }
  window.addEventListener('focus', recoverOnFocus)
  document.addEventListener('visibilitychange', recoverOnFocus)
})

onBeforeUnmount(() => {
  stopEvents()
  window.visualViewport?.removeEventListener('resize', updateVisualHeight)
  document.removeEventListener('keydown', handleComposerKeydown, true)
  window.removeEventListener('focus', recoverOnFocus)
  document.removeEventListener('visibilitychange', recoverOnFocus)
  window.clearTimeout(successTimer)
})

function updateVisualHeight() {
  visualHeight.value = window.visualViewport?.height ?? window.innerHeight
}

function handleComposerKeydown(event: KeyboardEvent) {
  const target = event.target
  if (
    !(target instanceof HTMLTextAreaElement)
    || target.id !== 'conversation-message'
    || event.key !== 'Enter'
    || event.shiftKey
    || event.isComposing
  ) return
  event.preventDefault()
  void send()
}

useSyncRefresh(
  ['conversation', 'memory', 'memory_settings'],
  async () => {
    await loadConversationList()
    if (selected.value) await syncCurrent(true)
  },
)

function mergeConversation(conversation: Conversation) {
  const existing = conversations.value.findIndex(item => item.id === conversation.id)
  if (conversation.archived !== archivedView.value) {
    if (existing >= 0) conversations.value.splice(existing, 1)
    return
  }
  if (existing >= 0) conversations.value[existing] = conversation
  else conversations.value.unshift(conversation)
  conversations.value.sort((left, right) => (
    right.updated_at.localeCompare(left.updated_at)
    || right.id.localeCompare(left.id)
  ))
}

async function loadConversationList(more = false) {
  if (loadingList.value) return
  const revision = ++listRevision
  loadingList.value = true
  if (!more) error.value = null
  try {
    const result = await listConversations(
      archivedView.value,
      more ? conversationCursor.value : null,
    )
    if (revision !== listRevision) return
    conversations.value = more
      ? [...conversations.value, ...result.items]
      : result.items
    conversationCursor.value = result.next_cursor
  } catch (reason) {
    if (revision === listRevision) error.value = failure(reason)
  } finally {
    if (revision === listRevision) loadingList.value = false
  }
}

function stopEvents() {
  eventSource?.close()
  eventSource = null
  window.clearTimeout(recoveryTimer)
  recoveryTimer = 0
}

function scheduleRecovery() {
  window.clearTimeout(recoveryTimer)
  if (!selected.value?.active_job || document.hidden) return
  recoveryTimer = window.setTimeout(() => {
    void syncCurrent(true)
  }, recoveryDelay)
  recoveryDelay = Math.min(3000, recoveryDelay + 1000)
}

function updateMessage(id: string, patch: Partial<ConversationMessage>) {
  messages.value = messages.value.map(message => (
    message.id === id ? { ...message, ...patch } : message
  ))
}

function handleEvent(raw: MessageEvent<string>) {
  let event: AIJobEvent
  try {
    event = JSON.parse(raw.data) as AIJobEvent
  } catch {
    error.value = localFailure('回复事件无法读取，正在重新同步会话。')
    stopEvents()
    scheduleRecovery()
    return
  }
  if (event.sequence <= lastEventSequence) return
  lastEventSequence = event.sequence
  const messageId = event.payload.message_id
  if (!messageId) return
  if (event.kind === 'content_reset') {
    eventGeneration = event.generation
    updateMessage(messageId, {
      content: '',
      status: 'running',
      error: null,
    })
    return
  }
  if (event.generation < eventGeneration) return
  eventGeneration = event.generation
  if (event.kind === 'content_delta') {
    const message = messages.value.find(item => item.id === messageId)
    if (message) {
      updateMessage(messageId, {
        content: message.content + (event.payload.text ?? ''),
        status: 'running',
      })
      void scrollToLatest(false)
    }
    return
  }
  const status = event.payload.status
  if (!status) return
  updateMessage(messageId, {
    status: status === 'succeeded' ? 'complete' : status,
    error: event.payload.error_code
      ? {
          code: event.payload.error_code,
          message: event.payload.error_message ?? 'AI 回复未能完成。',
        }
      : null,
    completed_at: ['succeeded', 'failed', 'timed_out', 'cancelled'].includes(status)
      ? event.created_at
      : null,
  })
  if (['succeeded', 'failed', 'timed_out', 'cancelled'].includes(status)) {
    stopEvents()
    void syncCurrent(true)
  }
}

function connectEvents() {
  stopEvents()
  const job = selected.value?.active_job
  if (!job || document.hidden) return
  lastEventSequence = job.last_event_sequence
  eventSource = new EventSource(jobEventsUrl(job.id, lastEventSequence))
  for (const kind of ['job_state', 'content_reset', 'content_delta'] as const) {
    eventSource.addEventListener(kind, handleEvent as EventListener)
  }
  eventSource.onerror = () => {
    stopEvents()
    scheduleRecovery()
  }
}

async function syncCurrent(silent = false) {
  if (!selected.value) return
  const id = selected.value.id
  const revision = ++threadRevision
  if (!silent) loadingThread.value = true
  try {
    const [conversation, page] = await Promise.all([
      getConversation(id),
      listConversationMessages(id),
    ])
    if (revision !== threadRevision || selected.value?.id !== id) return
    selected.value = conversation
    messages.value = page.items
    olderCursor.value = page.next_before_sequence
    mergeConversation(conversation)
    recoveryDelay = 1000
    if (conversation.active_job) connectEvents()
    else stopEvents()
    if (!silent) await scrollToLatest(true)
  } catch (reason) {
    if (revision === threadRevision) {
      error.value = failure(reason)
      scheduleRecovery()
    }
  } finally {
    if (revision === threadRevision) loadingThread.value = false
  }
}

async function selectConversation(conversation: Conversation) {
  if (selected.value?.id === conversation.id) return
  stopEvents()
  composerAttachment.value = null
  attachmentOperation = null
  selected.value = conversation
  messages.value = []
  olderCursor.value = null
  eventGeneration = 0
  lastEventSequence = 0
  error.value = null
  success.value = ''
  await router.replace({ path: '/ai', query: { conversation: conversation.id } })
  await syncCurrent()
}

function startDraft() {
  if (sending.value) return
  selected.value = null
  messages.value = []
  eventGeneration = 0
  lastEventSequence = 0
  stopEvents()
  composer.value = ''
  composerAttachment.value = null
  error.value = null
  success.value = ''
  void router.replace('/ai')
  void nextTick(() => document.getElementById('conversation-message')?.focus())
}

function createNewConversation(): Promise<Conversation | null> {
  if (createPromise) return createPromise
  createPromise = performCreateConversation()
  return createPromise
}

async function performCreateConversation(): Promise<Conversation | null> {
  if (authLoading.value || !identity.value) {
    await refreshSession()
    if (!identity.value) return null
  }
  creating.value = true
  error.value = null
  success.value = ''
  createOperation ||= crypto.randomUUID()
  try {
    const result = await createConversation(createOperation)
    createOperation = ''
    archivedView.value = false
    await loadConversationList()
    await selectConversation(result.conversation)
    await nextTick()
    document.getElementById('conversation-message')?.focus()
    return result.conversation
  } catch (reason) {
    error.value = failure(reason)
    return null
  } finally {
    creating.value = false
    createPromise = null
  }
}

async function prepareDifficultyDraft(difficultyId: string) {
  try {
    const difficulty = await getDifficulty(difficultyId)
    if (difficulty.lifecycle !== 'active') {
      error.value = localFailure('这条困难记录已不在活动列表中。')
      return
    }
    if (!selected.value) startDraft()
    composer.value = [
      '我遇到了这个困难：',
      difficulty.content,
      difficulty.category ? `分类：${difficulty.category}` : '',
      difficulty.emotion ? `感受：${difficulty.emotion}` : '',
      difficulty.task_title ? `关联任务：${difficulty.task_title}` : '',
    ].filter(Boolean).join('\n')
    success.value = '困难已放入草稿，发送前可以继续修改。'
    await nextTick()
    document.getElementById('conversation-message')?.focus()
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    await router.replace({
      path: '/ai',
      query: selected.value ? { conversation: selected.value.id } : {},
    })
  }
}

async function switchArchived(value: boolean) {
  if (archivedView.value === value) return
  archivedView.value = value
  selected.value = null
  messages.value = []
  eventGeneration = 0
  lastEventSequence = 0
  stopEvents()
  await router.replace('/ai')
  await loadConversationList()
}

function openRename(conversation: Conversation) {
  renameTarget.value = conversation
  renameTitle.value = conversation.title
  renameError.value = ''
  updateOperation = null
  renameOpen.value = true
  void nextTick(() => document.getElementById('conversation-title-input')?.focus())
}

function closeRename() {
  if (renamePending.value) return
  renameOpen.value = false
  renameTarget.value = null
  renameError.value = ''
}

async function saveRename() {
  if (!renameTarget.value || renamePending.value) return
  const title = renameTitle.value.trim().replace(/\s+/g, ' ')
  if (!title) {
    renameError.value = '请输入会话名称。'
    await nextTick()
    document.getElementById('conversation-title-input')?.focus()
    return
  }
  const fingerprint = `${renameTarget.value.id}:${renameTarget.value.version}:${title}`
  updateOperation = updateOperation?.fingerprint === fingerprint
    ? updateOperation
    : { id: crypto.randomUUID(), fingerprint }
  renamePending.value = true
  try {
    const result = await updateConversation(
      renameTarget.value,
      updateOperation.id,
      { title },
    )
    updateOperation = null
    mergeConversation(result.conversation)
    if (selected.value?.id === result.conversation.id) {
      selected.value = result.conversation
    }
    success.value = '会话名称已保存。'
    renameOpen.value = false
    renameTarget.value = null
    renameError.value = ''
  } catch (reason) {
    const current = failure(reason)
    error.value = current
    if (current.code === 'conversation_version_conflict') {
      await loadConversationList()
      const latest = conversations.value.find(item => item.id === renameTarget.value?.id)
      if (latest) renameTarget.value = latest
    }
  } finally {
    renamePending.value = false
  }
}

function askToggleArchive(conversation: Conversation) {
  archiveTarget.value = conversation
  archiveOpen.value = true
}

async function toggleArchive() {
  const conversation = archiveTarget.value
  if (!conversation) return
  archiveOpen.value = false
  archiveBusy.value = true
  const action = conversation.archived ? '重新打开' : '归档'
  const fingerprint = `${conversation.id}:${conversation.version}:${action}`
  updateOperation = updateOperation?.fingerprint === fingerprint
    ? updateOperation
    : { id: crypto.randomUUID(), fingerprint }
  error.value = null
  try {
    const result = await updateConversation(
      conversation,
      updateOperation.id,
      { archived: !conversation.archived },
    )
    updateOperation = null
    conversations.value = conversations.value.filter(item => item.id !== conversation.id)
    if (selected.value?.id === conversation.id) {
      selected.value = null
      messages.value = []
      stopEvents()
      await router.replace('/ai')
    }
    success.value = result.conversation.archived ? '会话已归档。' : '会话已重新打开。'
  } catch (reason) {
    error.value = failure(reason)
    if (error.value.code === 'conversation_version_conflict') {
      await loadConversationList()
    }
  } finally {
    archiveBusy.value = false
    archiveTarget.value = null
  }
}

async function loadOlder() {
  if (!selected.value || !olderCursor.value || loadingOlder.value) return
  loadingOlder.value = true
  try {
    const page = await listConversationMessages(
      selected.value.id,
      olderCursor.value,
    )
    const known = new Set(messages.value.map(message => message.id))
    messages.value = [
      ...page.items.filter(message => !known.has(message.id)),
      ...messages.value,
    ]
    olderCursor.value = page.next_before_sequence
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    loadingOlder.value = false
  }
}

async function ensureConversation() {
  if (selected.value) return selected.value
  return createNewConversation()
}

function isCalendarAttachment(file: File) {
  const name = file.name.toLocaleLowerCase()
  return name.endsWith('.xlsx') || name.endsWith('.ics')
}

function isDocumentAttachment(file: File) {
  return !isCalendarAttachment(file)
}

async function send() {
  if (sending.value || composerDisabled.value) return
  const attachment = composerAttachment.value
  let content = composer.value.trim()
  if (!content && attachment) {
    content = isCalendarAttachment(attachment)
      ? '请把这个课表文件整理成可确认的日历候选。'
      : attachment.type.startsWith('image/')
        ? '请阅读这张图片，提炼重点并给出接下来可执行的建议。'
        : '请阅读这个文件，提炼重点并给出接下来可执行的建议。'
  }
  if (!content) return
  sending.value = true
  error.value = null
  success.value = ''
  try {
    const conversation = await ensureConversation()
    if (!conversation) return
    let importBatchId: string | undefined
    let imageMediaId: string | undefined
    if (attachment) {
      const attachmentFingerprint = [
        conversation.id,
        attachment.name,
        attachment.size,
        attachment.lastModified,
      ].join(':')
      attachmentOperation = attachmentOperation?.fingerprint === attachmentFingerprint
        ? attachmentOperation
        : { id: crypto.randomUUID(), fingerprint: attachmentFingerprint }
      if (attachment.type.startsWith('image/')) {
        const image = await uploadAIImage(attachment, attachmentOperation.id)
        imageMediaId = image.media_id
      } else if (isDocumentAttachment(attachment)) {
        const extracted = await extractAIDocument(attachment, attachmentOperation.id)
        const header = `附件《${extracted.filename}》${extracted.truncated ? '（内容过长，已截断）' : ''}`
        const overhead = content.length + header.length + 2
        const body = extracted.text.slice(0, Math.max(0, 11_990 - overhead))
        content = `${content}\n\n${header}\n${body}`.slice(0, 12_000)
      } else {
        const imported = await uploadCalendarFile(attachment, attachmentOperation.id)
        importBatchId = imported.batch.id
      }
    }
    const fingerprint = JSON.stringify({
      conversation: conversation.id,
      version: conversation.version,
      content,
      response_mode: 'auto',
      import_batch_id: importBatchId,
    })
    sendOperation = sendOperation?.fingerprint === fingerprint
      ? sendOperation
      : { id: crypto.randomUUID(), fingerprint }
    const result = await sendConversationMessage(
      conversation,
      sendOperation.id,
      content,
      'auto',
      undefined,
      importBatchId,
      imageMediaId,
    )
    sendOperation = null
    attachmentOperation = null
    composer.value = ''
    composerAttachment.value = null
    selected.value = result.conversation
    mergeConversation(result.conversation)
    const known = new Set(messages.value.map(message => message.id))
    messages.value = [
      ...messages.value,
      ...result.messages.filter(message => !known.has(message.id)),
    ]
    lastEventSequence = result.job.last_event_sequence
    eventGeneration = 0
    connectEvents()
    await scrollToLatest(true)
  } catch (reason) {
    error.value = failure(reason)
    if (
      error.value.saved === 'unknown'
      || error.value.code === 'conversation_version_conflict'
    ) {
      await syncCurrent(true)
      const latestUser = [...messages.value].reverse().find(
        message => message.role === 'user',
      )
      if (latestUser?.content === content) {
        sendOperation = null
        attachmentOperation = null
        composer.value = ''
        composerAttachment.value = null
      }
    }
  } finally {
    sending.value = false
  }
}

async function cancelGeneration() {
  const job = selected.value?.active_job
  if (!job || cancelling.value) return
  cancelling.value = true
  error.value = null
  try {
    await cancelAIJob(job.id, crypto.randomUUID())
    success.value = job.capability === 'planning'
      ? '计划生成已取消，尚未创建提案。'
      : '生成已取消，已收到的内容仍保留。'
    await syncCurrent(true)
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    cancelling.value = false
  }
}

async function retryMessage(message: ConversationMessage) {
  const previous = [...messages.value].reverse().find(
    item => item.role === 'user' && item.sequence < message.sequence,
  )
  if (!previous) return
  composer.value = previous.content
  await nextTick()
  document.getElementById('conversation-message')?.focus()
}

async function scrollToLatest(force = true) {
  await viewport.value?.scrollToLatest(force)
}

function recoverOnFocus() {
  if (document.hidden) {
    stopEvents()
    return
  }
  if (selected.value) void syncCurrent(true)
  else void loadConversationList()
}

function invalidAttachment(message: string) {
  error.value = localFailure(message)
}
</script>

<template>
  <AppShell
    current="ai"
    has-secondary
    secondary-label="会话"
    :title="pageTitle"
    :pending-logout="pendingLogout"
    @logout="logout"
  >
    <template #actions>
      <ElDropdown
        v-if="selected"
        trigger="click"
        placement="bottom-end"
        @command="onConversationCommand"
      >
        <button type="button" class="m3-icon-button" aria-label="会话操作" title="会话操作">
          <span class="material-symbols-rounded" aria-hidden="true">more_vert</span>
        </button>
        <template #dropdown>
          <ElDropdownMenu class="ai-conversation-menu">
            <ElDropdownItem command="rename">
              <Pencil :size="18" aria-hidden="true" />
              <span>重命名</span>
            </ElDropdownItem>
            <ElDropdownItem command="archive" :disabled="!!selected.active_job">
              <ArchiveRestore v-if="selected.archived" :size="18" aria-hidden="true" />
              <Archive v-else :size="18" aria-hidden="true" />
              <span>{{ selected.archived ? '重新打开' : '归档' }}</span>
            </ElDropdownItem>
          </ElDropdownMenu>
        </template>
      </ElDropdown>
    </template>
    <template #secondary>
      <ConversationSidebar
        :conversations="conversations"
        :selected-id="selected?.id ?? null"
        :archived="archivedView"
        :loading="loadingList"
        :has-more="!!conversationCursor"
        :create-disabled="authLoading || creating"
        :ai-frozen="identity?.user.ai_access === 'frozen'"
        @select="selectConversation"
        @create="startDraft"
        @archived-view="switchArchived"
        @load-more="loadConversationList(true)"
        @rename="openRename"
        @archive="askToggleArchive"
      />
    </template>
    <template #drawer-secondary="{ close }">
      <ConversationSidebar
        :conversations="conversations"
        :selected-id="selected?.id ?? null"
        :archived="archivedView"
        :loading="loadingList"
        :has-more="!!conversationCursor"
        :create-disabled="authLoading || creating"
        :ai-frozen="identity?.user.ai_access === 'frozen'"
        @select="(v) => { selectConversation(v); close() }"
        @create="() => { startDraft(); close() }"
        @archived-view="switchArchived"
        @load-more="loadConversationList(true)"
        @rename="(v) => { openRename(v); close() }"
        @archive="(v) => { askToggleArchive(v); close() }"
      />
    </template>

    <div
      class="ai-main"
      :style="{ '--visual-viewport-height': `${visualHeight}px` }"
    >
      <div
        v-if="error || success || logoutError || identity?.user.ai_access === 'frozen'"
        class="ai-workspace-status"
      >
        <StatusMessage :error="logoutError" />
        <div v-if="identity?.user.ai_access === 'frozen'"
          class="status restriction-status" role="status">
          <CirclePause :size="20" aria-hidden="true" />
          <p>此账号的 AI 功能已暂停，已有会话仍可查看和整理。</p>
        </div>
        <StatusMessage :error="error" :success="success"
          :retry-label="error?.retryable ? '重新同步' : undefined"
          @retry="selected ? syncCurrent() : loadConversationList()" />
      </div>
      <ConversationViewport
        ref="viewport"
        :conversation="selected"
        :messages="messages"
        :loading="loadingThread"
        :has-older="!!olderCursor"
        :loading-older="loadingOlder"
        @load-older="loadOlder"
        @retry-message="retryMessage"
        @suggest="suggestPrompt"
      />
      <ConversationComposer
        v-model="composer"
        v-model:attachment="composerAttachment"
        :pending="sending"
        :disabled="composerDisabled"
        :archived="selected?.archived ?? false"
        :ai-frozen="identity?.user.ai_access === 'frozen'"
        :active="!!activeJob"
        @submit="send"
        @cancel="cancelGeneration"
        @invalid-attachment="invalidAttachment"
      />
    </div>

    <ConfirmDialog
      v-model:open="archiveOpen"
      :title="archiveTarget?.archived ? '重新打开会话？' : '归档会话？'"
      :message="archiveTarget
        ? archiveTarget.archived
          ? `“${archiveTarget.title}”会回到进行中列表。`
          : `“${archiveTarget.title}”会从进行中列表移到已归档，可随时重新打开。`
        : ''"
      :confirm-label="archiveTarget?.archived ? '重新打开' : '归档'"
      :busy="archiveBusy"
      @confirm="toggleArchive"
    />

    <ElDialog
      v-model="renameOpen"
      title="重命名会话"
      width="min(420px, calc(100vw - 24px))"
      :show-close="!renamePending"
      :close-on-click-modal="!renamePending"
      :close-on-press-escape="!renamePending"
    >
      <form @submit.prevent="saveRename">
        <ElFormItem label="会话名称" :error="renameError">
          <ElInput id="conversation-title-input" v-model="renameTitle"
            maxlength="120" :disabled="renamePending" />
        </ElFormItem>
        <div class="dialog-actions">
          <ElButton :disabled="renamePending" @click="closeRename">取消</ElButton>
          <ElButton type="primary" native-type="submit"
            :loading="renamePending" :disabled="renamePending">
            {{ renamePending ? '正在保存…' : '保存名称' }}
          </ElButton>
        </div>
      </form>
    </ElDialog>
  </AppShell>
</template>
