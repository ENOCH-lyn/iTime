<script setup lang="ts">
import { CalendarClock, Check, LoaderCircle, X } from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink } from 'vue-router'
import {
  applyAgentProposal,
  getAgentProposal,
  rejectAgentProposal,
  type AgentProposal,
} from '../lib/agent-proposals'
import { failure, type Failure } from '../lib/api'

const props = defineProps<{ proposalId: string }>()
const emit = defineEmits<{ applied: [] }>()
const proposal = ref<AgentProposal | null>(null)
const loading = ref(false)
const pending = ref(false)
const error = ref<Failure | null>(null)

const timeLabel = computed(() => {
  if (!proposal.value) return ''
  const value = proposal.value.payload
  return `${value.local_date} ${value.starts_at.slice(0, 5)}–${value.ends_at.slice(0, 5)}`
})
const statusLabel = computed(() => {
  if (proposal.value?.effective_status === 'pending') return '待确认'
  if (proposal.value?.effective_status === 'applied') return '已添加'
  if (proposal.value?.effective_status === 'rejected') return '已拒绝'
  return '已过期'
})
const statusType = computed(() => {
  if (proposal.value?.effective_status === 'applied') return 'success'
  if (proposal.value?.effective_status === 'pending') return 'primary'
  return 'info'
})
const calendarDestination = computed(() => ({
  path: '/timetable/month',
  query: proposal.value ? { date: proposal.value.payload.local_date } : {},
}))

async function load() {
  loading.value = true
  error.value = null
  try {
    proposal.value = await getAgentProposal(props.proposalId)
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    loading.value = false
  }
}

async function apply() {
  if (!proposal.value || pending.value) return
  pending.value = true
  error.value = null
  try {
    proposal.value = (
      await applyAgentProposal(proposal.value, crypto.randomUUID())
    ).proposal
    emit('applied')
  } catch (reason) {
    error.value = failure(reason)
    if (
      error.value.saved === 'unknown'
      || error.value.code.includes('conflict')
    ) await load()
  } finally {
    pending.value = false
  }
}

async function reject() {
  if (!proposal.value || pending.value) return
  pending.value = true
  error.value = null
  try {
    proposal.value = (
      await rejectAgentProposal(proposal.value, crypto.randomUUID())
    ).proposal
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    pending.value = false
  }
}

watch(() => props.proposalId, load)
onMounted(load)
</script>

<template>
  <section class="message-proposal agent-proposal" aria-label="任务计划提案">
    <div v-if="loading && !proposal" class="message-proposal-loading" role="status">
      <LoaderCircle :size="16" class="spin" aria-hidden="true" />读取提案
    </div>
    <template v-else-if="proposal">
      <div class="message-proposal-heading">
        <CalendarClock :size="18" aria-hidden="true" />
        <div>
          <strong>{{ proposal.payload.task_title }}</strong>
          <ElTag :type="statusType" effect="plain" size="small">
            {{ statusLabel }}
          </ElTag>
        </div>
      </div>
      <p>{{ timeLabel }} · {{ proposal.payload.estimated_minutes }} 分钟</p>
      <p v-if="proposal.payload.task_description" class="message-proposal-tasks">
        {{ proposal.payload.task_description }}
      </p>
      <p v-if="proposal.assumptions.length" class="message-proposal-assumption">
        {{ proposal.assumptions.join('；') }}
      </p>
      <div v-if="proposal.effective_status === 'pending'" class="proposal-actions">
        <ElButton type="primary" :loading="pending" @click="apply">
          <Check :size="16" aria-hidden="true" />确认添加
        </ElButton>
        <ElButton :disabled="pending" @click="reject">
          <X :size="16" aria-hidden="true" />拒绝
        </ElButton>
      </div>
      <ElButton
        v-else-if="proposal.effective_status === 'applied'"
        :tag="RouterLink"
        :to="calendarDestination"
        link
        type="primary"
      >
        在日历中查看
      </ElButton>
      <ElAlert
        v-if="error"
        type="error"
        :title="error.message"
        :closable="false"
        show-icon
      />
    </template>
  </section>
</template>
