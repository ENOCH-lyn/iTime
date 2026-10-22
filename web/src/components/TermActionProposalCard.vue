<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { CalendarRange, Check, LoaderCircle, Pencil, X } from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
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

const details = computed(() => proposal.value?.preview.details ?? {})
const isCreate = computed(() => proposal.value?.tool_name === 'create_term')
const title = computed(() => (
  isCreate.value
    ? `新建学期「${String(details.value.name ?? '未命名')}」`
    : `编辑学期「${String(details.value.name ?? '当前学期')}」`
))
const summary = computed(() => proposal.value?.preview.summary ?? '')
const startDate = computed(() => String(details.value.start_date ?? ''))
const weekCount = computed(() => (
  details.value.week_count == null ? '' : `${String(details.value.week_count)} 周`
))
const stateLabel = computed(() => {
  if (!proposal.value) return ''
  if (proposal.value.status === 'pending') return '待确认'
  if (proposal.value.status === 'applied') return '已应用'
  if (proposal.value.status === 'rejected') return '已拒绝'
  return '已过期'
})

async function load() {
  if (!props.proposalId) return
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
  <section class="term-proposal" aria-label="学期提案">
    <div v-if="loading && !proposal" class="term-proposal-loading" role="status">
      <LoaderCircle :size="16" class="spin" aria-hidden="true" />正在读取学期提案…
    </div>
    <template v-else-if="proposal">
      <header>
        <CalendarRange :size="18" aria-hidden="true" />
        <div>
          <strong>{{ title }}</strong>
          <span>{{ stateLabel }}</span>
        </div>
      </header>
      <p v-if="summary" class="term-proposal-summary">{{ summary }}</p>
      <dl class="term-proposal-meta">
        <div v-if="startDate"><dt>开始日期</dt><dd>{{ startDate }}</dd></div>
        <div v-if="weekCount"><dt>周数</dt><dd>{{ weekCount }}</dd></div>
      </dl>
      <div v-if="proposal.status === 'pending'" class="term-proposal-actions">
        <ElButton type="primary" :loading="pending" :disabled="pending" @click="apply">
          <Check :size="16" aria-hidden="true" />确认
        </ElButton>
        <ElButton :disabled="pending" @click="reject">
          <X :size="16" aria-hidden="true" />取消
        </ElButton>
      </div>
      <p v-else-if="proposal.status === 'applied'" class="term-proposal-done">
        <Check :size="15" aria-hidden="true" />已写入学期
      </p>
      <StatusMessage v-if="error" :error="{ code: 'term_proposal_error', message: error, request_id: null, saved: 'unknown', retryable: false }" />
      <span class="term-proposal-icon" aria-hidden="true">
        <Pencil v-if="!isCreate" :size="14" />
      </span>
    </template>
  </section>
</template>

<style scoped>
.term-proposal {
  margin-top: 10px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: var(--surface);
}
.term-proposal header {
  display: flex;
  align-items: flex-start;
  gap: 8px;
}
.term-proposal header > svg { color: var(--accent); flex-shrink: 0; margin-top: 1px; }
.term-proposal header > div { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.term-proposal header strong { font-size: 13px; font-weight: 650; }
.term-proposal header span { font-size: 11px; color: var(--muted); }
.term-proposal-summary { margin: 8px 0 0; font-size: 12px; line-height: 1.55; color: var(--text); }
.term-proposal-meta { display: flex; flex-wrap: wrap; gap: 12px; margin: 9px 0 0; }
.term-proposal-meta div { display: flex; gap: 4px; font-size: 12px; }
.term-proposal-meta dt { color: var(--muted); }
.term-proposal-meta dd { margin: 0; font-weight: 600; }
.term-proposal-actions { display: flex; gap: 8px; margin-top: 12px; }
.term-proposal-done { display: flex; align-items: center; gap: 5px; margin: 10px 0 0; font-size: 12px; color: var(--success, #18794e); }
.term-proposal-loading { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); }
.term-proposal-icon { display: none; }
</style>
