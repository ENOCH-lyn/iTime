<script setup lang="ts">
import { CalendarDays, CheckSquare2, Settings } from 'lucide-vue-next'

defineProps<{ current: 'tasks' | 'calendar' | 'settings' }>()

const links = [
  { key: 'tasks', to: '/tasks', label: '任务', icon: CheckSquare2 },
  { key: 'calendar', to: '/calendar', label: '日历', icon: CalendarDays },
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
