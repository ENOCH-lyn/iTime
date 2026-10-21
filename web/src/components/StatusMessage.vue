<script setup lang="ts">
import type { Failure } from '../lib/api'

defineProps<{ error?: Failure | null; success?: string; retryLabel?: string }>()
defineEmits<{ retry: [] }>()
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
