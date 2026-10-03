<script setup lang="ts">
import {
  Archive,
  ArchiveRestore,
  CirclePause,
  LogOut,
  MessageCircleMore,
  Pencil,
  Plus,
} from 'lucide-vue-next'
import type { Failure } from '../lib/api'
import type { Conversation } from '../lib/conversations'
import ProductNavigation from './ProductNavigation.vue'
import StatusMessage from './StatusMessage.vue'

defineProps<{
  conversations: Conversation[]
  selectedId: string | null
  archived: boolean
  loading: boolean
  hasMore: boolean
  email: string
  pendingLogout: boolean
  error: Failure | null
  success: string
  aiFrozen: boolean
}>()

const emit = defineEmits<{
  select: [conversation: Conversation]
  create: []
  archivedView: [archived: boolean]
  loadMore: []
  rename: [conversation: Conversation]
  archive: [conversation: Conversation]
  logout: []
  retry: []
}>()

function readableTime(value: string) {
  return new Date(value).toLocaleString('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function activityLabel(conversation: Conversation) {
  if (conversation.active_job?.status === 'queued') return '排队中'
  if (conversation.active_job?.status === 'running') return '生成中'
  return ''
}
</script>

<template>
  <aside class="conversation-navigation" aria-label="iTime 导航">
    <div class="conversation-navigation-top">
      <RouterLink to="/ai" class="wordmark" aria-label="iTime AI">iTime<span aria-hidden="true">.</span></RouterLink>
      <ProductNavigation current="ai" />
    </div>
    <div class="conversation-navigation-status">
      <div v-if="aiFrozen" class="status restriction-status" role="status">
        <CirclePause :size="20" aria-hidden="true" />
        <p>此账号的 AI 功能已暂停，已有会话仍可查看和整理。</p>
      </div>
      <StatusMessage :error="error" :success="success"
        :retry-label="error?.retryable ? '重新同步' : undefined"
        @retry="emit('retry')" />
    </div>

    <section class="conversation-list-section" aria-labelledby="conversation-list-title">
      <div class="conversation-list-heading">
        <h2 id="conversation-list-title">会话</h2>
        <button type="button" class="small-icon-button" title="新建会话"
          aria-label="新建会话" @click="emit('create')">
          <Plus :size="18" aria-hidden="true" />
        </button>
      </div>
      <div class="conversation-tabs" role="group" aria-label="会话范围">
        <button type="button" :class="{ selected: !archived }"
          :aria-pressed="!archived" @click="emit('archivedView', false)">进行中</button>
        <button type="button" :class="{ selected: archived }"
          :aria-pressed="archived" @click="emit('archivedView', true)">已归档</button>
      </div>

      <p v-if="loading && !conversations.length" class="conversation-loading" role="status">
        正在读取会话…
      </p>
      <p v-else-if="!conversations.length" class="navigation-empty">
        {{ archived ? '还没有归档会话。' : '从一个新问题开始。' }}
      </p>
      <ol v-else class="conversation-list">
        <li v-for="conversation in conversations" :key="conversation.id"
          :class="{ selected: selectedId === conversation.id }">
          <button type="button" class="conversation-select"
            :aria-pressed="selectedId === conversation.id"
            @click="emit('select', conversation)">
            <MessageCircleMore :size="17" aria-hidden="true" />
            <span>
              <strong>{{ conversation.title }}</strong>
              <small>
                {{ activityLabel(conversation) || readableTime(conversation.updated_at) }}
              </small>
            </span>
          </button>
          <div class="conversation-row-actions">
            <button type="button" class="small-icon-button"
              :aria-label="`重命名 ${conversation.title}`"
              :title="`重命名 ${conversation.title}`"
              @click="emit('rename', conversation)">
              <Pencil :size="14" aria-hidden="true" />
            </button>
            <button type="button" class="small-icon-button"
              :aria-label="`${conversation.archived ? '重新打开' : '归档'} ${conversation.title}`"
              :title="`${conversation.archived ? '重新打开' : '归档'} ${conversation.title}`"
              :disabled="!!conversation.active_job"
              @click="emit('archive', conversation)">
              <ArchiveRestore v-if="conversation.archived" :size="15" aria-hidden="true" />
              <Archive v-else :size="15" aria-hidden="true" />
            </button>
          </div>
        </li>
      </ol>
      <button v-if="hasMore" type="button" class="text-button conversation-more"
        :disabled="loading" @click="emit('loadMore')">
        {{ loading ? '正在读取…' : '更早的会话' }}
      </button>
    </section>

    <div class="conversation-account">
      <p>{{ email }}</p>
      <button type="button" class="nav-link logout-button"
        :disabled="pendingLogout" @click="emit('logout')">
        <LogOut :size="18" aria-hidden="true" />
        {{ pendingLogout ? '正在退出…' : '退出登录' }}
      </button>
    </div>
  </aside>
</template>
