<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { LoaderCircle, Trash2, X } from 'lucide-vue-next'
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
        <Trash2 :size="17" aria-hidden="true" />
        <div>
          <strong>删除课程「{{ courseTitle }}」</strong>
          <span>{{ statusLabel }}</span>
        </div>
      </header>
      <p v-if="summary" class="course-proposal-summary">{{ summary }}</p>
      <p class="course-proposal-hint">确认后整门课程（含重复安排）移入回收站。</p>
      <div v-if="proposal.status === 'pending'" class="course-proposal-actions">
        <ElButton type="danger" :loading="pending" @click="apply">
          <Trash2 :size="15" aria-hidden="true" /> 确认删除
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
.course-proposal header > svg { color: var(--error); flex-shrink: 0; }
.course-proposal header > div { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.course-proposal header strong { font-size: 13px; font-weight: 650; line-height: 1.35; }
.course-proposal header span { font-size: 11px; color: var(--muted); }
.course-proposal-summary {
  margin: 8px 0 0;
  font-size: 12px;
  line-height: 1.55;
  color: var(--muted);
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
