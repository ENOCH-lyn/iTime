<script setup lang="ts">
import { computed, onMounted } from 'vue'
import {
  Cloud,
  CloudOff,
  RefreshCw,
  TriangleAlert,
} from 'lucide-vue-next'
import {
  discardTaskConflict,
  refreshSyncServerStatus,
  requestSync,
  syncStatus,
} from '../lib/sync'
import StatusMessage from './StatusMessage.vue'

const statusLabel = computed(() => {
  if (syncStatus.phase === 'offline') return '离线'
  if (syncStatus.phase === 'connecting') return '正在同步'
  if (syncStatus.phase === 'pending') return '有待同步操作'
  if (syncStatus.phase === 'conflict') return '需要处理冲突'
  if (syncStatus.phase === 'error') return '同步失败'
  return '已同步'
})

const statusIcon = computed(() => {
  if (syncStatus.phase === 'offline') return CloudOff
  if (syncStatus.phase === 'connecting' || syncStatus.phase === 'pending') {
    return RefreshCw
  }
  if (syncStatus.phase === 'error' || syncStatus.phase === 'conflict') {
    return TriangleAlert
  }
  return Cloud
})

function localTime(value: string | null) {
  return value === null ? '暂无变更' : new Date(value).toLocaleString('zh-CN')
}

async function refresh() {
  requestSync()
  await refreshSyncServerStatus()
}

onMounted(refreshSyncServerStatus)
</script>

<template>
  <section class="sync-settings" aria-labelledby="sync-summary-title">
    <div class="section-intro sync-intro">
      <div>
        <p class="page-description">核对当前设备与服务端的数据状态。</p>
      </div>
      <button
        type="button"
        class="button secondary"
        :disabled="syncStatus.phase === 'connecting' || syncStatus.phase === 'offline'"
        @click="refresh"
      >
        <RefreshCw :size="17" aria-hidden="true" />
        {{ syncStatus.phase === 'connecting' ? '正在同步' : '立即同步' }}
      </button>
    </div>

    <StatusMessage
      :error="syncStatus.error"
      :success="syncStatus.reset_message"
      :retry-label="syncStatus.error?.retryable ? '重试同步' : undefined"
      @retry="refresh"
    />

    <div class="sync-summary">
      <component
        :is="statusIcon"
        :size="26"
        :class="{ spinning: syncStatus.phase === 'connecting' }"
        aria-hidden="true"
      />
      <div>
        <h2 id="sync-summary-title">{{ statusLabel }}</h2>
        <p v-if="syncStatus.phase === 'offline'">显示最近同步的数据。</p>
        <p v-else-if="syncStatus.phase === 'error'">已保留当前页面和本地待同步操作。</p>
        <p v-else>服务端数据是当前权威状态。</p>
      </div>
    </div>

    <dl class="sync-metrics">
      <div>
        <dt>最后同步</dt>
        <dd>{{ localTime(syncStatus.last_success_at) }}</dd>
      </div>
      <div>
        <dt>最近变更</dt>
        <dd data-testid="sync-latest-change">{{ localTime(syncStatus.latest_change_at) }}</dd>
      </div>
      <div>
        <dt>待同步</dt>
        <dd>{{ syncStatus.pending_count }} 项</dd>
      </div>
      <div>
        <dt>冲突</dt>
        <dd>{{ syncStatus.conflict_count }} 项</dd>
      </div>
    </dl>

    <section class="sync-section" aria-labelledby="sync-local-title">
      <h2 id="sync-local-title">本设备</h2>
      <dl class="sync-local-details">
        <div>
          <dt>网络</dt>
          <dd>{{ syncStatus.phase === 'offline' ? '离线' : '在线' }}</dd>
        </div>
        <div>
          <dt>最近缓存</dt>
          <dd>{{ localTime(syncStatus.cache_fallback_at) }}</dd>
        </div>
      </dl>
    </section>

    <section
      v-if="syncStatus.task_conflict"
      class="sync-section sync-conflict"
      aria-labelledby="sync-conflict-title"
    >
      <h2 id="sync-conflict-title">同步冲突</h2>
      <p>{{ syncStatus.task_conflict.error?.message ?? '任务步骤已变化，请核对后处理。' }}</p>
      <p
        v-if="syncStatus.task_conflict.error?.request_id"
        class="request-id wrap-anywhere"
      >
        请求编号 {{ syncStatus.task_conflict.error.request_id }}
      </p>
      <div class="sync-conflict-actions">
        <RouterLink to="/tasks" class="button secondary">查看任务</RouterLink>
        <button type="button" class="button danger" @click="discardTaskConflict">
          丢弃此操作
        </button>
      </div>
    </section>
  </section>
</template>
