<script setup lang="ts">
import {
  CalendarDays,
  CalendarRange,
  CircleAlert,
  CircleCheck,
  Clock3,
  LogOut,
  Sparkles,
  XCircle,
} from 'lucide-vue-next'
import type {
  PlanProposalSummary,
  PlanScope,
  ProposalEffectiveStatus,
} from '../lib/plans'
import ProductNavigation from './ProductNavigation.vue'

defineProps<{
  scope: PlanScope
  proposals: PlanProposalSummary[]
  selectedProposalId: string | null
  loading: boolean
  email: string
  pendingLogout: boolean
}>()

const emit = defineEmits<{
  scope: [scope: PlanScope]
  proposal: [proposal: PlanProposalSummary]
  logout: []
}>()

const statusLabels: Record<ProposalEffectiveStatus, string> = {
  pending: '待确认',
  applied: '已应用',
  rejected: '已拒绝',
  expired: '已过期',
  conflicted: '需重生成',
}

function statusIcon(status: ProposalEffectiveStatus) {
  if (status === 'applied') return CircleCheck
  if (status === 'rejected' || status === 'expired') return XCircle
  if (status === 'conflicted') return CircleAlert
  return Clock3
}

function readableDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
  }).format(new Date(value))
}
</script>

<template>
  <aside class="plan-navigation" aria-label="iTime 导航">
    <div class="plan-navigation-top">
      <RouterLink to="/plans" class="wordmark" aria-label="iTime 规划">
        iTime<span aria-hidden="true">.</span>
      </RouterLink>
      <ProductNavigation current="plans" />
    </div>

    <section class="plan-navigation-modes" aria-labelledby="plan-view-heading">
      <p id="plan-view-heading" class="nav-heading">规划范围</p>
      <button
        type="button"
        class="nav-link"
        :class="{ selected: scope === 'day' }"
        :aria-pressed="scope === 'day'"
        @click="emit('scope', 'day')"
      >
        <CalendarDays :size="18" aria-hidden="true" />单日规划
      </button>
      <button
        type="button"
        class="nav-link"
        :class="{ selected: scope === 'week' }"
        :aria-pressed="scope === 'week'"
        @click="emit('scope', 'week')"
      >
        <CalendarRange :size="18" aria-hidden="true" />整周规划
      </button>
    </section>

    <section class="plan-proposal-navigation" aria-labelledby="proposal-list-heading">
      <div class="plan-navigation-heading">
        <p id="proposal-list-heading" class="nav-heading">最近提案</p>
        <Sparkles :size="16" aria-hidden="true" />
      </div>
      <p v-if="loading && !proposals.length" class="navigation-empty" role="status">
        正在读取提案…
      </p>
      <p v-else-if="!proposals.length" class="navigation-empty">
        还没有规划提案。
      </p>
      <ol v-else class="plan-proposal-list">
        <li v-for="proposal in proposals" :key="proposal.id">
          <button
            type="button"
            :class="{ selected: selectedProposalId === proposal.id }"
            :aria-pressed="selectedProposalId === proposal.id"
            @click="emit('proposal', proposal)"
          >
            <component :is="statusIcon(proposal.effective_status)" :size="16" aria-hidden="true" />
            <span>
              <strong>{{ proposal.title }}</strong>
              <small>
                {{ statusLabels[proposal.effective_status] }} · {{ readableDate(proposal.created_at) }}
              </small>
            </span>
          </button>
        </li>
      </ol>
    </section>

    <div class="task-account">
      <RouterLink to="/settings" class="task-account-email">
        {{ email }}
      </RouterLink>
      <button
        type="button"
        class="nav-link logout-button"
        :disabled="pendingLogout"
        @click="emit('logout')"
      >
        <LogOut :size="18" aria-hidden="true" />
        {{ pendingLogout ? '正在退出…' : '退出登录' }}
      </button>
    </div>
  </aside>
</template>
