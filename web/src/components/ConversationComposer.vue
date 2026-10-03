<script setup lang="ts">
import { SendHorizontal } from 'lucide-vue-next'

const props = defineProps<{
  pending: boolean
  disabled: boolean
  archived: boolean
  aiFrozen: boolean
  active: boolean
}>()
const model = defineModel<string>({ required: true })
const emit = defineEmits<{ submit: [] }>()

function submit() {
  if (props.pending || props.disabled || !model.value.trim()) return
  emit('submit')
}

function keydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
    event.preventDefault()
    submit()
  }
}
</script>

<template>
  <form class="conversation-composer" @submit.prevent="submit">
    <label for="conversation-message" class="sr-only">消息</label>
    <textarea id="conversation-message" v-model="model" rows="3" maxlength="12000"
      :disabled="disabled" :placeholder="archived
        ? '重新打开会话后可继续'
        : aiFrozen
          ? 'AI 功能已暂停'
          : active
            ? '等待当前回复完成'
            : '输入消息'"
      @keydown="keydown" />
    <div class="composer-actions">
      <span>{{ Array.from(model).length }}/12000</span>
      <button type="submit" class="button composer-send"
        :disabled="pending || disabled || !model.trim()" aria-label="发送消息">
        <SendHorizontal :size="18" aria-hidden="true" />
        <span>{{ pending ? '正在发送…' : '发送' }}</span>
      </button>
    </div>
  </form>
</template>
