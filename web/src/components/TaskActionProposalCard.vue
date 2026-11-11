<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { CalendarClock, Check, LoaderCircle, Pencil, Trash2, X } from 'lucide-vue-next'
import { failure } from '../lib/api'
import {
  applyToolProposal,
  getToolProposal,
  rejectToolProposal,
  type ToolProposal,
} from '../lib/tool-proposals'

const props = defineProps<{ proposalId: string }>()
const emit = defineEmits<{ applied: [] }>()
const proposal = ref<ToolProposal | null>(null)
const loading = ref(false)
const pending = ref(false)
const error = ref('')

const title = computed(() => {
  const details = proposal.value?.preview.details ?? {}
  return String(details.block_title ?? details.task_title ?? '此任务')
})
const summary = computed(() => proposal.value?.preview.summary ?? '')
const isComplete = computed(() => proposal.value?.tool_name === 'complete_task')
const isDelete = computed(() => proposal.value?.tool_name === 'delete_task')
const isCreateTask = computed(() => proposal.value?.tool_name === 'create_task')
const isPlan = computed(() => proposal.value?.tool_name === 'update_plan_block')
const isPlanCreate = computed(() => proposal.value?.tool_name === 'create_plan_block')
const isPlanDelete = computed(() => proposal.value?.tool_name === 'delete_plan_block')
const isPlanBatch = computed(() => proposal.value?.tool_name === 'batch_update_plan_blocks')
const batchCount = computed(() => {
  const details = proposal.value?.preview.details
  return details && Array.isArray(details.changes) ? details.changes.length : 0
})
const actionLabel = computed(() => {
  if (isCreateTask.value) return '新建'
  if (isDelete.value) return '删除'
  if (isPlan.value) return '调整'
  if (isPlanCreate.value) return '安排'
  if (isPlanDelete.value) return '移除'
  if (isPlanBatch.value) return '批量调整'
  if (isComplete.value) return '完成'
  return '修改'
})
const statusLabel = computed(() => {
  if (!proposal.value) return ''
  if (proposal.value.status === 'pending') return '待确认'
  if (proposal.value.status === 'applied') return '已确认，Agent 继续执行'
  if (proposal.value.status === 'rejected') return '已拒绝，Agent 继续处理'
  return '已过期'
})
const changes = computed(() => {
  if (isCreateTask.value) {
    const details = proposal.value?.preview.details ?? {}
    const time = (value: unknown) => String(value).slice(0, 5)
    const rows: string[] = []
    if (details.local_date != null) rows.push(`日期 ${String(details.local_date)}`)
    if (details.starts_at != null && details.ends_at != null) {
      rows.push(`时间 ${time(details.starts_at)}–${time(details.ends_at)}`)
    }
    if (details.estimated_minutes != null) {
      rows.push(`预计 ${String(details.estimated_minutes)} 分钟`)
    }
    rows.push('写入任务列表，同时在日历里显示')
    return rows
  }
  if (isDelete.value) return ['移入回收站，30 天后清理']
  const details = proposal.value?.preview.details ?? {}
  const rows: string[] = []
  if (isPlanDelete.value) {
    return ['从本周计划中移除', '任务本身保留，回到未排状态']
  }
  if (isPlanBatch.value) {
    const batch = Array.isArray(details.changes)
      ? details.changes as Record<string, unknown>[]
      : []
    return batch.map((row) => {
      const time = (value: unknown) => String(value).slice(0, 5)
      const target = [
        row.local_date != null ? String(row.local_date) : null,
        row.starts_at != null && row.ends_at != null
          ? `${time(row.starts_at)}–${time(row.ends_at)}`
          : null,
      ].filter(Boolean).join(' ')
      return `${String(row.block_title ?? '计划块')} → ${target || '时间不变'}`
    })
  }
  if (isPlan.value || isPlanCreate.value) {
    if (details.local_date != null) rows.push(`日期改为 ${String(details.local_date)}`)
    const time = (value: unknown) => String(value).slice(0, 5)
    if (details.starts_at != null) rows.push(`开始时间改为 ${time(details.starts_at)}`)
    if (details.ends_at != null) rows.push(`结束时间改为 ${time(details.ends_at)}`)
    return rows
  }
  if (details.title != null) rows.push(`名称改为「${String(details.title)}」`)
  if (details.description != null) rows.push('更新描述')
  if (details.estimated_minutes != null) {
    rows.push(`预计用时 ${String(details.estimated_minutes)} 分钟`)
  }
  if (details.priority != null) {
    const labels = ['未设置', '低', '中', '高']
    rows.push(`优先级 ${labels[Number(details.priority)] ?? details.priority}`)
  }
  if (details.due_date != null) {
    const parts = String(details.due_date).split('-')
    rows.push(`截止日期改为 ${Number(parts[1])}月${Number(parts[2])}日`)
  }
  if (details.repeat != null) {
    const labels: Record<string, string> = {
      none: '不重复',
      daily: '每天',
      weekdays: '每个工作日',
      weekly: '每周',
      monthly: '每月',
      yearly: '每年',
    }
    rows.push(`重复规则改为「${labels[String(details.repeat)] ?? String(details.repeat)}」`)
  }
  if (Array.isArray(details.repeat_weekdays) && details.repeat_weekdays.length) {
    const weekdays = ['', '一', '二', '三', '四', '五', '六', '日']
    rows.push(`重复星期：周${details.repeat_weekdays.map(day => weekdays[Number(day)]).join('、')}`)
  }
  if (details.repeat_until != null) rows.push(`重复至 ${String(details.repeat_until)}`)
  if (details.repeat_count != null) rows.push(`重复 ${String(details.repeat_count)} 次`)
  return rows
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    proposal.value = await getToolProposal(props.proposalId)
  } catch (reason) {
    error.value = failure(reason).message
  } finally {
    loading.value = false
  }
}

async function apply() {
  if (!proposal.value || pending.value) return
  pending.value = true
  error.value = ''
  try {
    proposal.value = (await applyToolProposal(proposal.value)).proposal
    emit('applied')
  } catch (reason) {
    error.value = failure(reason).message
    await load()
  } finally {
    pending.value = false
  }
}

async function reject() {
  if (!proposal.value || pending.value) return
  pending.value = true
  error.value = ''
  try {
    proposal.value = (await rejectToolProposal(proposal.value)).proposal
    emit('applied')
  } catch (reason) {
    error.value = failure(reason).message
  } finally {
    pending.value = false
  }
}

onMounted(load)
watch(() => props.proposalId, load)
</script>

<template>
  <section class="task-proposal" aria-label="任务修改提案">
    <div v-if="loading && !proposal" class="task-proposal-loading" role="status">
      <LoaderCircle :size="15" class="spin" /> 读取任务提案
    </div>
    <template v-else-if="proposal">
      <header>
        <component :is="(isDelete || isPlanDelete) ? Trash2 : ((isCreateTask || isPlan || isPlanCreate || isPlanBatch) ? CalendarClock : (isComplete ? Check : Pencil))" :size="17" aria-hidden="true" />
        <div>
          <strong>{{ isCreateTask ? `新建任务「${title}」` : (isPlanBatch ? `批量调整 ${batchCount} 个计划块` : (isPlanDelete ? `移除计划块「${title}」` : (isPlanCreate ? `安排计划块「${title}」` : (isPlan ? `调整计划块「${title}」` : `${actionLabel}任务「${title}」`)))) }}</strong>
          <span>{{ statusLabel }}</span>
        </div>
      </header>
      <p v-if="summary" class="task-proposal-summary">{{ summary }}</p>
      <ul v-if="changes.length" class="task-proposal-changes">
        <li v-for="change in changes" :key="change">{{ change }}</li>
      </ul>
      <div v-if="proposal.status === 'pending'" class="task-proposal-actions">
        <ElButton type="primary" :loading="pending" @click="apply">
          <Check :size="15" aria-hidden="true" />
          {{ isCreateTask ? '确认新建' : (isPlanBatch ? '确认批量调整' : (isPlanDelete ? '确认移除' : (isPlanCreate ? '确认安排' : (isPlan ? '确认调整' : (isDelete ? '确认删除' : (isComplete ? '确认完成' : '确认修改')))))) }}
        </ElButton>
        <ElButton :disabled="pending" @click="reject">
          <X :size="15" aria-hidden="true" /> 拒绝
        </ElButton>
      </div>
      <p v-if="error" class="task-proposal-error" role="alert">{{ error }}</p>
    </template>
  </section>
</template>

<style scoped>
.task-proposal {
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  color: var(--text);
}
.task-proposal header { display: flex; align-items: center; gap: 8px; }
.task-proposal header > svg { color: var(--accent); flex-shrink: 0; }
.task-proposal header > div { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.task-proposal header strong { font-size: 13px; font-weight: 650; line-height: 1.35; }
.task-proposal header span { font-size: 11px; color: var(--muted); }
.task-proposal-summary {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.55;
  color: var(--muted);
}
.task-proposal-changes {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.6;
}
.task-proposal-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.task-proposal-error { margin: 10px 0 0; font-size: 12px; color: var(--error); }
.task-proposal-loading {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
