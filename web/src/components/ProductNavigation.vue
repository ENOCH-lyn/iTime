<script setup lang="ts">
import {
  CalendarDays,
  CheckSquare2,
  ListChecks,
  MessageCircleMore,
  NotebookPen,
  Settings,
  TimerReset,
} from 'lucide-vue-next'

defineProps<{
  current: 'ai' | 'tasks' | 'plans' | 'calendar' | 'focus' | 'reviews' | 'settings'
}>()

const links = [
  { key: 'ai', to: '/ai', label: 'AI', icon: MessageCircleMore },
  { key: 'tasks', to: '/tasks', label: '任务', icon: CheckSquare2 },
  { key: 'plans', to: '/plans', label: '规划', icon: ListChecks },
  { key: 'calendar', to: '/calendar', label: '日历', icon: CalendarDays },
  { key: 'focus', to: '/focus', label: '专注', icon: TimerReset },
  { key: 'reviews', to: '/reviews', label: '复盘', icon: NotebookPen },
  { key: 'settings', to: '/account', label: '设置', icon: Settings },
] as const
</script>

<template>
  <nav class="product-navigation" aria-label="产品">
    <RouterLink
      v-for="link in links"
      :key="link.key"
      :to="link.to"
      class="nav-link"
      :class="{ selected: current === link.key }"
      :aria-current="current === link.key ? 'page' : undefined"
      :aria-label="link.label"
    >
      <component :is="link.icon" :size="18" :stroke-width="1.8" aria-hidden="true" />
      <span class="product-label">{{ link.label }}</span>
    </RouterLink>
  </nav>
</template>
