<script setup lang="ts">
import { Archive, ArchiveRestore, Ellipsis, Menu, Pencil, Square } from 'lucide-vue-next'
import type { Conversation } from '../lib/conversations'

defineProps<{
  conversation: Conversation | null
  cancelling: boolean
}>()

const emit = defineEmits<{
  openNavigation: []
  cancel: []
  rename: [conversation: Conversation]
  archive: [conversation: Conversation]
}>()
</script>

<template>
  <header class="conversation-header" aria-label="会话标题">
    <ElButton
      text
      circle
      class="conversation-navigation-trigger"
      aria-label="打开导航"
      title="打开导航"
      @click="emit('openNavigation')"
    >
      <Menu :size="20" aria-hidden="true" />
    </ElButton>

    <h1 id="conversation-title" tabindex="-1">
      {{ conversation?.title ?? '新对话' }}
    </h1>

    <div class="conversation-header-actions">
      <ElButton
        v-if="conversation?.active_job"
        plain
        class="conversation-stop"
        :loading="cancelling"
        :disabled="cancelling"
        aria-label="取消生成"
        @click="emit('cancel')"
      >
        <Square :size="15" aria-hidden="true" />
        <span>{{ cancelling ? '正在停止…' : '停止' }}</span>
      </ElButton>

      <ElDropdown v-if="conversation" trigger="click" placement="bottom-end">
        <ElButton
          text
          circle
          class="small-icon-button"
          aria-label="会话操作"
        >
          <Ellipsis :size="19" aria-hidden="true" />
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
    </div>
  </header>
</template>
