<script setup lang="ts">
import { computed } from 'vue'
import { CircleAlert, CircleCheck } from 'lucide-vue-next'
import type { Failure } from '../lib/api'

const props = defineProps<{ error?: Failure | null; success?: string; retryLabel?: string }>()
defineEmits<{ retry: [] }>()
const saveText = computed(() => {
  if (!props.error) return ''
  return { not_saved: '本次操作未保存。', saved: '内容已保存。', unknown: '保存状态尚未确认，请先检查结果。' }[props.error.saved]
})
</script>

<template>
  <div v-if="error" class="status error-status" role="alert" tabindex="-1">
    <CircleAlert :size="20" aria-hidden="true" />
    <div>
      <p>{{ error.message }}</p>
      <p class="status-detail">{{ saveText }}</p>
      <p v-if="error.request_id" class="request-id">请求编号：{{ error.request_id }}</p>
      <button v-if="retryLabel" type="button" class="text-button" @click="$emit('retry')">{{ retryLabel }}</button>
    </div>
  </div>
  <div v-else-if="success" class="status success-status" role="status">
    <CircleCheck :size="20" aria-hidden="true" /><p>{{ success }}</p>
  </div>
</template>
