<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { CalendarDays, LoaderCircle, Pencil, Trash2, X } from 'lucide-vue-next'
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
const courseTitle = computed(() => String(details.value.series_title ?? '此课程'))
const summary = computed(() => proposal.value?.preview.summary ?? '')
const isUpdate = computed(() => proposal.value?.tool_name === 'update_calendar_series')
const isException = computed(() => proposal.value?.tool_name === 'create_calendar_exception')
const exceptionAction = computed(() => String(details.value.action ?? ''))
const exceptionTitle = computed(() => (
  exceptionAction.value === 'cancelled'
    ? `取消本次「${courseTitle.value}」`
    : `调整本次「${courseTitle.value}」`
))
const changes = computed(() => {
  if (isException.value) {
    const rows = [`原安排：${String(details.value.occurrence_date ?? '')}`]
    if (exceptionAction.value === 'moved') {
      if (details.value.moved_start_date) rows.push(`改到 ${String(details.value.moved_start_date)}`)
      const time = (value: unknown) => String(value).slice(0, 5)
      if (details.value.moved_starts_at) rows.push(`开始 ${time(details.value.moved_starts_at)}`)
      if (details.value.moved_ends_at) rows.push(`结束 ${time(details.value.moved_ends_at)}`)
    } else {
      rows.push('这一次不再上课')
    }
    return rows
  }
  if (!isUpdate.value) return []
  const rows: string[] = []
  if (details.value.name != null) rows.push(`课程名改为「${String(details.value.name)}」`)
  if (details.value.location != null) {
    rows.push(details.value.location ? `地点改为 ${String(details.value.location)}` : '清空地点')
  }
  const time = (value: unknown) => String(value).slice(0, 5)
  if (details.value.starts_at != null) rows.push(`开始时间改为 ${time(details.value.starts_at)}`)
  if (details.value.ends_at != null) rows.push(`结束时间改为 ${time(details.value.ends_at)}`)
  return rows
})
const statusLabel = computed(() => {
  if (!proposal.value) return ''
  if (proposal.value.status === 'pending') return '待确认'
  if (proposal.value.status === 'applied') return '已应用'
  if (proposal.value.status === 'rejected') return '已拒绝'
  return '已过期'
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
  <section class="course-proposal" aria-label="课程删除提案">
    <div v-if="loading && !proposal" class="course-proposal-loading" role="status">
      <LoaderCircle :size="15" class="spin" /> 读取课程提案
    </div>
    <template v-else-if="proposal">
      <header>
        <component :is="isException ? CalendarDays : (isUpdate ? Pencil : Trash2)" :size="17" aria-hidden="true" />
        <div>
          <strong>{{ isException ? exceptionTitle : (isUpdate ? `修改课程「${courseTitle}」` : `删除课程「${courseTitle}」`) }}</strong>
          <span>{{ statusLabel }}</span>
        </div>
      </header>
      <p v-if="summary" class="course-proposal-summary">{{ summary }}</p>
      <ul v-if="changes.length" class="course-proposal-changes">
        <li v-for="change in changes" :key="change">{{ change }}</li>
      </ul>
      <p v-if="isException" class="course-proposal-hint">只影响这一次，不改整组重复安排。</p>
      <p v-else-if="!isUpdate" class="course-proposal-hint">确认后整门课程（含重复安排）移入回收站。</p>
      <div v-if="proposal.status === 'pending'" class="course-proposal-actions">
        <ElButton :type="isUpdate ? 'primary' : 'danger'" :loading="pending" @click="apply">
          <component :is="isException ? CalendarDays : (isUpdate ? Pencil : Trash2)" :size="15" aria-hidden="true" />
          {{ isException ? (exceptionAction === 'cancelled' ? '确认取消' : '确认调整') : (isUpdate ? '确认修改' : '确认删除') }}
        </ElButton>
        <ElButton :disabled="pending" @click="reject">
          <X :size="15" aria-hidden="true" /> 拒绝
        </ElButton>
      </div>
      <p v-if="error" class="course-proposal-error" role="alert">{{ error }}</p>
    </template>
  </section>
</template>

<style scoped>
.course-proposal {
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--surface);
  color: var(--text);
}
.course-proposal header { display: flex; align-items: center; gap: 8px; }
.course-proposal header > svg { color: var(--accent); flex-shrink: 0; }
.course-proposal header > div { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.course-proposal header strong { font-size: 13px; font-weight: 650; line-height: 1.35; }
.course-proposal header span { font-size: 11px; color: var(--muted); }
.course-proposal-summary {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.55;
  color: var(--muted);
}
.course-proposal-changes {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: 12px;
  line-height: 1.6;
}
.course-proposal-hint {
  margin: 8px 0 0;
  font-size: 11.5px;
  color: var(--error);
}
.course-proposal-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.course-proposal-error { margin: 10px 0 0; font-size: 12px; color: var(--error); }
.course-proposal-loading {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
