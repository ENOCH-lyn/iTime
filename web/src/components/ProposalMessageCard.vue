<script setup lang="ts">
import {
  CalendarClock,
  CircleAlert,
  CircleCheck,
  LoaderCircle,
  Sparkles,
  XCircle,
} from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'
import { failure, type Failure } from '../lib/api'
import {
  getPlanProposal,
  type PlanProposal,
  type ProposalEffectiveStatus,
} from '../lib/plans'

const props = defineProps<{ proposalId: string }>()
const proposal = ref<PlanProposal | null>(null)
const loading = ref(false)
const error = ref<Failure | null>(null)

const labels: Record<ProposalEffectiveStatus, string> = {
  pending: '尚未写入计划',
  applied: '已写入计划',
  rejected: '已拒绝',
  expired: '已过期',
  conflicted: '输入已变化',
}

const destination = computed(() => ({
  path: '/plans',
  query: {
    scope: proposal.value?.input_snapshot.scope ?? 'week',
    date: proposal.value?.input_snapshot.target_date,
    proposal: props.proposalId,
  },
}))
const taskTitles = computed(() => (
  proposal.value?.changes
    .filter(change => change.entity_type === 'scheduled_block')
    .map(change => String(change.after_data?.task_title ?? ''))
    .filter(Boolean)
    .slice(0, 3)
  ?? []
))

async function load() {
  loading.value = true
  error.value = null
  try {
    proposal.value = await getPlanProposal(props.proposalId)
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    loading.value = false
  }
}

watch(() => props.proposalId, load)
onMounted(load)
</script>

<template>
  <section class="message-proposal" aria-label="规划提案">
    <div v-if="loading && !proposal" class="message-proposal-loading" role="status">
      <LoaderCircle :size="16" class="spin" aria-hidden="true" />正在读取提案…
    </div>
    <template v-else-if="proposal">
      <div class="message-proposal-heading">
        <Sparkles v-if="proposal.effective_status === 'pending'" :size="18" aria-hidden="true" />
        <CircleCheck v-else-if="proposal.effective_status === 'applied'" :size="18" aria-hidden="true" />
        <CircleAlert v-else-if="proposal.effective_status === 'conflicted'" :size="18" aria-hidden="true" />
        <XCircle v-else :size="18" aria-hidden="true" />
        <div>
          <strong>{{ proposal.title }}</strong>
          <span :data-state="proposal.effective_status">
            {{ labels[proposal.effective_status] }}
          </span>
        </div>
      </div>
      <p>{{ proposal.summary }}</p>
      <p v-if="taskTitles.length" class="message-proposal-tasks">
        {{ taskTitles.join('、') }}
      </p>
      <dl>
        <div>
          <dt>计划时段</dt>
          <dd>{{ proposal.payload.blocks.length }}</dd>
        </div>
        <div>
          <dt>暂未排入</dt>
          <dd>{{ proposal.payload.unscheduled_items.length }}</dd>
        </div>
      </dl>
      <RouterLink :to="destination" class="button secondary compact-button">
        <CalendarClock :size="16" aria-hidden="true" />
        {{ proposal.effective_status === 'pending' ? '查看并确认' : '在规划页查看' }}
      </RouterLink>
    </template>
    <div v-else-if="error" class="message-proposal-error" role="alert">
      <p>{{ error.message }}</p>
      <button v-if="error.retryable" type="button" class="text-button" @click="load">
        重新读取
      </button>
    </div>
  </section>
</template>
