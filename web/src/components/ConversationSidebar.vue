<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  Archive,
  ArchiveRestore,
  CirclePause,
  Ellipsis,
  LoaderCircle,
  Pencil,
  Plus,
  Search,
} from 'lucide-vue-next'
import type { Conversation } from '../lib/conversations'

const props = defineProps<{
  conversations: Conversation[]
  selectedId: string | null
  archived: boolean
  loading: boolean
  hasMore: boolean
  createDisabled: boolean
  aiFrozen: boolean
}>()

const emit = defineEmits<{
  select: [conversation: Conversation]
  create: []
  archivedView: [archived: boolean]
  loadMore: []
  rename: [conversation: Conversation]
  archive: [conversation: Conversation]
}>()

const search = ref('')
const visibleConversations = computed(() => {
  const query = search.value.trim().toLocaleLowerCase()
  if (!query) return props.conversations
  return props.conversations.filter(conversation => (
    conversation.title.toLocaleLowerCase().includes(query)
  ))
})

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
  <aside class="conversation-sidebar" aria-label="会话历史">
    <div class="conversation-sidebar-header">
      <h2>会话</h2>
      <ElButton
        type="primary"
        class="conversation-create"
        :disabled="createDisabled"
        @click="emit('create')"
      >
        <Plus :size="17" aria-hidden="true" />
        新建会话
      </ElButton>
    </div>

    <ElInput
      v-model="search"
      clearable
      class="conversation-search"
      placeholder="搜索会话"
      aria-label="搜索会话"
    >
      <template #prefix>
        <Search :size="16" aria-hidden="true" />
      </template>
    </ElInput>

    <div class="conversation-tabs" role="tablist" aria-label="会话范围">
      <button
        type="button"
        role="tab"
        :aria-selected="!archived"
        :class="{ selected: !archived }"
        @click="emit('archivedView', false)"
      >
        进行中
      </button>
      <button
        type="button"
        role="tab"
        :aria-selected="archived"
        :class="{ selected: archived }"
        @click="emit('archivedView', true)"
      >
        已归档
      </button>
    </div>

    <div v-if="aiFrozen" class="conversation-sidebar-notice" role="status">
      <CirclePause :size="16" aria-hidden="true" />
      AI 已暂停
    </div>

    <p
      v-if="loading && !conversations.length"
      class="conversation-loading"
      role="status"
    >
      正在读取会话…
    </p>
    <p
      v-else-if="!visibleConversations.length"
      class="navigation-empty"
    >
      {{ search.trim()
        ? '没有匹配的会话'
        : archived
          ? '还没有归档会话'
          : '还没有会话' }}
    </p>
    <ol v-else class="conversation-list">
      <li
        v-for="conversation in visibleConversations"
        :key="conversation.id"
        :class="{ selected: selectedId === conversation.id }"
      >
        <ElButton
          text
          class="conversation-select"
          :aria-pressed="selectedId === conversation.id"
          @click="emit('select', conversation)"
        >
          <div class="conversation-select-copy">
            <ElTooltip
              :content="conversation.title"
              placement="right"
              :show-after="600"
            >
              <strong class="conversation-title-text">
                {{ conversation.title }}
              </strong>
            </ElTooltip>
            <small>
              <LoaderCircle
                v-if="activityLabel(conversation)"
                :size="12"
                class="spin"
                aria-hidden="true"
              />
              {{ activityLabel(conversation) || readableTime(conversation.updated_at) }}
            </small>
          </div>
        </ElButton>

        <ElDropdown trigger="click" placement="bottom-end">
          <ElButton
            text
            circle
            class="conversation-row-menu"
            :aria-label="`会话操作 ${conversation.title}`"
            @click.stop
          >
            <Ellipsis :size="17" aria-hidden="true" />
          </ElButton>
          <template #dropdown>
            <ElDropdownMenu>
              <ElDropdownItem @click="emit('rename', conversation)">
                <Pencil :size="15" aria-hidden="true" />
                重命名
              </ElDropdownItem>
              <ElDropdownItem
                :disabled="!!conversation.active_job"
                @click="emit('archive', conversation)"
              >
                <ArchiveRestore
                  v-if="conversation.archived"
                  :size="15"
                  aria-hidden="true"
                />
                <Archive v-else :size="15" aria-hidden="true" />
                {{ conversation.archived ? '重新打开' : '归档' }}
              </ElDropdownItem>
            </ElDropdownMenu>
          </template>
        </ElDropdown>
      </li>
    </ol>

    <ElButton
      v-if="hasMore"
      link
      type="primary"
      class="conversation-more"
      :disabled="loading"
      @click="emit('loadMore')"
    >
      {{ loading ? '正在读取…' : '读取更早会话' }}
    </ElButton>

    <div v-if="$slots.footer" class="conversation-sidebar-footer">
      <slot name="footer"></slot>
    </div>
  </aside>
</template>
