<script setup lang="ts">
import {
  Bot,
  CircleAlert,
  Clock3,
  LoaderCircle,
  UserRound,
} from 'lucide-vue-next'
import type { Conversation, ConversationMessage } from '../lib/conversations'
import AgentProposalMessageCard from './AgentProposalMessageCard.vue'
import MarkdownMessage from './MarkdownMessage.vue'
import ProposalMessageCard from './ProposalMessageCard.vue'
import TimetableImportMessageCard from './TimetableImportMessageCard.vue'

defineProps<{
  conversation: Conversation | null
  messages: ConversationMessage[]
  loading: boolean
  hasOlder: boolean
  loadingOlder: boolean
}>()

const emit = defineEmits<{
  loadOlder: []
  retryMessage: [message: ConversationMessage]
}>()

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
</script>

<template>
  <section class="conversation-thread" aria-label="会话内容">
    <div v-if="!conversation" class="conversation-welcome">
      <Bot :size="30" aria-hidden="true" />
      <h2>今天想先理清哪件事？</h2>
    </div>

    <div v-else ref="messageViewport" class="conversation-message-viewport">
      <ElButton v-if="hasOlder" link type="primary" class="conversation-older"
        :disabled="loadingOlder" @click="emit('loadOlder')">
        {{ loadingOlder ? '正在读取…' : '读取更早消息' }}
      </ElButton>
      <p v-if="loading && !messages.length" class="thread-loading" role="status">
        正在读取消息…
      </p>
      <div v-else-if="!messages.length" class="conversation-empty">
        <Bot :size="28" aria-hidden="true" />
        <h2>从眼前的问题开始</h2>
      </div>
      <ol v-else class="conversation-messages" aria-label="会话消息">
        <li v-for="message in messages" :id="`message-${message.id}`"
          :key="message.id" :class="[`message-${message.role}`, `message-${message.status}`]">
          <div class="message-author" aria-hidden="true">
            <UserRound v-if="message.role === 'user'" :size="17" />
            <Bot v-else :size="17" />
          </div>
          <div class="message-body">
            <div class="message-meta">
              <strong>{{ message.role === 'user' ? '你' : 'iTime' }}</strong>
              <time :datetime="message.created_at">{{ readableTime(message.created_at) }}</time>
            </div>
            <MarkdownMessage
              v-if="message.role === 'assistant' && message.content"
              :content="message.content"
            />
            <p v-else-if="message.content" class="message-content">
              {{ message.content }}
            </p>
            <div v-else-if="['queued', 'running'].includes(message.status)"
              class="message-progress" role="status" aria-live="polite">
              <LoaderCircle :size="16" class="spin" aria-hidden="true" />
              {{ message.job?.capability === 'planning'
                ? message.status === 'queued' ? '计划正在排队' : '正在生成并校验计划'
                : message.status === 'queued' ? '正在排队' : '正在生成' }}
            </div>
            <AgentProposalMessageCard
              v-if="message.role === 'assistant'
                && message.job?.output?.proposal_id
                && message.job.output.proposal_kind === 'task_plan'"
              :proposal-id="message.job.output.proposal_id"
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
            <div v-if="stateLabel(message)" class="message-state"
              :class="{ failed: ['failed', 'timed_out'].includes(message.status) }"
              role="status">
              <CircleAlert v-if="['failed', 'timed_out'].includes(message.status)"
                :size="15" aria-hidden="true" />
              <Clock3 v-else :size="15" aria-hidden="true" />
              <span>{{ stateLabel(message) }}</span>
              <ElButton v-if="['failed', 'timed_out'].includes(message.status)"
                link type="primary" @click="emit('retryMessage', message)">
                重新发送
              </ElButton>
            </div>
            <p v-if="message.error" class="message-error">
              {{ message.error.message }}
            </p>
          </div>
        </li>
      </ol>
    </div>
  </section>
</template>
