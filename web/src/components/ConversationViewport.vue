<script setup lang="ts">
import {
  ArrowDown,
  Bot,
  CircleAlert,
  Clock3,
  LoaderCircle,
} from 'lucide-vue-next'
import {
  nextTick,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
} from 'vue'
import type { Conversation, ConversationMessage } from '../lib/conversations'
import AgentProposalMessageCard from './AgentProposalMessageCard.vue'
import CourseActionProposalCard from './CourseActionProposalCard.vue'
import MarkdownMessage from './MarkdownMessage.vue'
import ProposalMessageCard from './ProposalMessageCard.vue'
import TaskActionProposalCard from './TaskActionProposalCard.vue'
import TermActionProposalCard from './TermActionProposalCard.vue'
import TimetableImportMessageCard from './TimetableImportMessageCard.vue'

const props = defineProps<{
  conversation: Conversation | null
  messages: ConversationMessage[]
  loading: boolean
  hasOlder: boolean
  loadingOlder: boolean
}>()

const emit = defineEmits<{
  loadOlder: []
  retryMessage: [message: ConversationMessage]
  changed: []
}>()

const viewport = ref<HTMLElement | null>(null)
const messageList = ref<HTMLElement | null>(null)
const nearBottom = ref(true)
let preserveOlder: { height: number; top: number } | null = null
let resizeObserver: ResizeObserver | null = null

function readableTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

function stateLabel(message: ConversationMessage) {
  if (message.status === 'cancelled') return '已取消，以上内容可能不完整'
  if (message.status === 'timed_out') return '已超时，以上内容可能不完整'
  if (message.status === 'failed') return '生成失败，以上内容可能不完整'
  return ''
}

function timetableImportIds(message: ConversationMessage) {
  const ids = new Set(
    (message.calendar_imports ?? []).map(item => item.id),
  )
  const outputId = message.job?.output?.import_batch_id
  if (outputId) ids.add(outputId)
  return [...ids]
}

function measureBottom() {
  const element = viewport.value
  if (!element) return
  nearBottom.value = (
    element.scrollHeight - element.scrollTop - element.clientHeight <= 96
  )
}

async function scrollToLatest(force = false) {
  await nextTick()
  const element = viewport.value
  if (!element || (!force && !nearBottom.value)) return
  element.scrollTop = element.scrollHeight
  measureBottom()
}

function loadOlder() {
  const element = viewport.value
  if (element) {
    preserveOlder = {
      height: element.scrollHeight,
      top: element.scrollTop,
    }
  }
  emit('loadOlder')
}

watch(
  () => props.messages.map(message => (
    `${message.id}:${message.content.length}:${message.status}`
  )).join('|'),
  async () => {
    const follow = nearBottom.value
    await nextTick()
    const element = viewport.value
    if (!element) return
    if (preserveOlder) {
      element.scrollTop = (
        preserveOlder.top + element.scrollHeight - preserveOlder.height
      )
      preserveOlder = null
      measureBottom()
      return
    }
    if (follow) await scrollToLatest(true)
  },
)

watch(messageList, element => {
  resizeObserver?.disconnect()
  if (!element) return
  resizeObserver = new ResizeObserver(() => {
    if (nearBottom.value) void scrollToLatest(true)
  })
  resizeObserver.observe(element)
})

onMounted(measureBottom)
onBeforeUnmount(() => resizeObserver?.disconnect())

defineExpose({ scrollToLatest })
</script>

<template>
  <section class="conversation-viewport-shell" aria-label="会话内容">
    <div v-if="!conversation" class="conversation-welcome">
      <Bot :size="30" aria-hidden="true" />
      <h2>今天想先理清哪件事？</h2>
    </div>

    <div
      v-else
      ref="viewport"
      class="conversation-message-viewport"
      tabindex="0"
      aria-label="消息历史"
      @scroll.passive="measureBottom"
    >
      <ElButton
        v-if="hasOlder"
        link
        type="primary"
        class="conversation-older"
        :disabled="loadingOlder"
        @click="loadOlder"
      >
        {{ loadingOlder ? '正在读取…' : '读取更早消息' }}
      </ElButton>
      <p v-if="loading && !messages.length" class="thread-loading" role="status">
        正在读取消息…
      </p>
      <div v-else-if="!messages.length" class="conversation-empty">
        <Bot :size="28" aria-hidden="true" />
        <h2>从眼前的问题开始</h2>
      </div>
      <ol
        v-else
        ref="messageList"
        class="conversation-messages"
        aria-label="会话消息"
      >
        <li
          v-for="message in messages"
          :id="`message-${message.id}`"
          :key="message.id"
          :class="[`message-${message.role}`, `message-${message.status}`]"
        >
          <article
            class="message-body"
            :aria-label="message.role === 'user' ? '你的消息' : 'iTime 回复'"
            :title="readableTime(message.created_at)"
          >
            <time class="sr-only" :datetime="message.created_at">
              {{ readableTime(message.created_at) }}
            </time>
            <MarkdownMessage
              v-if="message.role === 'assistant' && message.content"
              :content="message.content"
            />
            <p v-else-if="message.content" class="message-content">
              {{ message.content }}
            </p>
            <div
              v-else-if="['queued', 'running'].includes(message.status)"
              class="message-progress"
              role="status"
              aria-live="polite"
            >
              <LoaderCircle :size="16" class="spin" aria-hidden="true" />
              {{ message.job?.capability === 'planning'
                ? message.status === 'queued' ? '计划正在排队' : '正在生成并校验计划'
                : message.status === 'queued' ? '正在排队' : '正在生成' }}
            </div>
            <CourseActionProposalCard
              v-if="message.role === 'assistant'
                && message.job?.output?.proposal_id
                && message.job.output.proposal_kind === 'course_action'"
              :proposal-id="message.job.output.proposal_id"
              @applied="emit('changed')"
            />
            <TaskActionProposalCard
              v-else-if="message.role === 'assistant'
                && message.job?.output?.proposal_id
                && message.job.output.proposal_kind === 'task_action'"
              :proposal-id="message.job.output.proposal_id"
              @applied="emit('changed')"
            />
            <AgentProposalMessageCard
              v-else-if="message.role === 'assistant'
                && message.job?.output?.proposal_id
                && message.job.output.proposal_kind === 'task_plan'"
              :proposal-id="message.job.output.proposal_id"
            />
            <TermActionProposalCard
              v-else-if="message.role === 'assistant' && message.job?.output?.proposal_kind === 'term_action' && message.job.output.proposal_id"
              :proposal-id="message.job.output.proposal_id"
              @applied="emit('changed')"
            />
            <ProposalMessageCard
              v-else-if="message.role === 'assistant' && message.job?.output?.proposal_id"
              :proposal-id="message.job.output.proposal_id"
            />
            <TimetableImportMessageCard
              v-for="batchId in timetableImportIds(message)"
              :key="batchId"
              :batch-id="batchId"
            />
            <div
              v-if="stateLabel(message)"
              class="message-state"
              :class="{ failed: ['failed', 'timed_out'].includes(message.status) }"
              role="status"
            >
              <CircleAlert
                v-if="['failed', 'timed_out'].includes(message.status)"
                :size="15"
                aria-hidden="true"
              />
              <Clock3 v-else :size="15" aria-hidden="true" />
              <span>{{ stateLabel(message) }}</span>
              <ElButton
                v-if="['failed', 'timed_out'].includes(message.status)"
                link
                type="primary"
                @click="emit('retryMessage', message)"
              >
                重新发送
              </ElButton>
            </div>
            <p v-if="message.error" class="message-error">
              {{ message.error.message }}
            </p>
          </article>
        </li>
      </ol>
    </div>

    <ElButton
      v-if="conversation && !nearBottom"
      class="conversation-latest"
      plain
      round
      @click="scrollToLatest(true)"
    >
      <ArrowDown :size="16" aria-hidden="true" />
      回到最新
    </ElButton>
  </section>
</template>
