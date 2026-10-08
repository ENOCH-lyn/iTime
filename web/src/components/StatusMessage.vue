<script setup lang="ts">
import { computed } from 'vue'
import type { Failure } from '../lib/api'

const props = defineProps<{ error?: Failure | null; success?: string; retryLabel?: string }>()
defineEmits<{ retry: [] }>()
const saveText = computed(() => {
  if (!props.error) return ''
  return { not_saved: '本次操作未保存。', saved: '内容已保存。', unknown: '保存状态尚未确认，请先检查结果。' }[props.error.saved]
})
</script>

<template>
  <ElAlert
    v-if="error"
    class="status error-status"
    type="error"
    :closable="false"
    show-icon
    role="alert"
  >
    <template #title>{{ error.message }}</template>
    <p class="status-detail">{{ saveText }}</p>
    <p v-if="error.request_id" class="request-id">请求编号：{{ error.request_id }}</p>
    <ElButton v-if="retryLabel" link type="primary" @click="$emit('retry')">
      {{ retryLabel }}
    </ElButton>
  </ElAlert>
  <ElAlert
    v-else-if="success"
    class="status success-status"
    type="success"
    :closable="false"
    show-icon
    role="status"
    :title="success"
  />
</template>
