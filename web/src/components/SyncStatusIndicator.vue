<script setup lang="ts">
import { computed } from 'vue'
import {
  Cloud,
  CloudOff,
  RefreshCw,
  TriangleAlert,
} from 'lucide-vue-next'
import { syncStatus } from '../lib/sync'

const label = computed(() => {
  if (syncStatus.phase === 'offline') {
    return syncStatus.pending_count
      ? `同步状态：离线，${syncStatus.pending_count} 项待同步`
      : '同步状态：离线'
  }
  if (syncStatus.phase === 'connecting') return '同步状态：正在同步'
  if (syncStatus.phase === 'conflict') {
    return `同步状态：${syncStatus.conflict_count} 项冲突`
  }
  if (syncStatus.phase === 'pending') {
    return `同步状态：${syncStatus.pending_count} 项待同步`
  }
  if (syncStatus.phase === 'error') {
    return syncStatus.pending_count
      ? `同步状态：同步失败，${syncStatus.pending_count} 项待同步`
      : '同步状态：同步失败'
  }
  return '同步状态：已同步'
})

const icon = computed(() => {
  if (syncStatus.phase === 'offline') return CloudOff
  if (syncStatus.phase === 'connecting' || syncStatus.phase === 'pending') {
    return RefreshCw
  }
  if (syncStatus.phase === 'conflict' || syncStatus.phase === 'error') {
    return TriangleAlert
  }
  return Cloud
})

const count = computed(() =>
  syncStatus.conflict_count || syncStatus.pending_count)
</script>

<template>
  <RouterLink
    to="/account/sync"
    class="sync-status-indicator"
    :class="{ spinning: syncStatus.phase === 'connecting' }"
    :aria-label="label"
    :title="label"
    :data-state="syncStatus.phase"
  >
    <component :is="icon" :size="18" :stroke-width="1.8" aria-hidden="true" />
    <span v-if="count" class="sync-status-count" aria-hidden="true">{{ count > 99 ? '99+' : count }}</span>
  </RouterLink>
</template>
