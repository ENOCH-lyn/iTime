<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import {
  CalendarDays,
  ChartNoAxesCombined,
  CheckSquare2,
  ListChecks,
  MessageCircleMore,
  NotebookPen,
  Settings,
  TimerReset,
} from 'lucide-vue-next'
import SyncStatusIndicator from './SyncStatusIndicator.vue'

const props = defineProps<{
  current: 'ai' | 'tasks' | 'plans' | 'calendar' | 'focus' | 'reviews' | 'stats' | 'settings'
}>()

const navigation = ref<HTMLElement | null>(null)

const links = [
  { key: 'ai', to: '/ai', label: 'AI', icon: MessageCircleMore },
  { key: 'tasks', to: '/tasks', label: '任务', icon: CheckSquare2 },
  { key: 'plans', to: '/plans', label: '规划', icon: ListChecks },
  { key: 'calendar', to: '/calendar', label: '日历', icon: CalendarDays },
  { key: 'focus', to: '/focus', label: '专注', icon: TimerReset },
  { key: 'reviews', to: '/reviews', label: '复盘', icon: NotebookPen },
  { key: 'stats', to: '/stats', label: '统计', icon: ChartNoAxesCombined },
  { key: 'settings', to: '/account', label: '设置', icon: Settings },
] as const

async function revealCurrent() {
  await nextTick()
  const container = navigation.value
  const current = container?.querySelector<HTMLElement>('[aria-current="page"]')
  if (!container || !current || container.scrollWidth <= container.clientWidth) return
  const containerBox = container.getBoundingClientRect()
  const currentBox = current.getBoundingClientRect()
  container.scrollTo({
    left: Math.max(
      0,
      container.scrollLeft
        + currentBox.left
        - containerBox.left
        - (container.clientWidth - currentBox.width) / 2,
    ),
    behavior: 'auto',
  })
}

onMounted(revealCurrent)
watch(() => props.current, revealCurrent)
</script>

<template>
  <nav ref="navigation" class="product-navigation" aria-label="产品">
    <ElMenu class="product-menu" :default-active="links.find(link => link.key === current)?.to" router>
      <ElMenuItem
        v-for="link in links"
        :key="link.key"
        :index="link.to"
        class="nav-link"
        :class="{ selected: current === link.key }"
        :aria-current="current === link.key ? 'page' : undefined"
        :aria-label="link.label"
      >
        <component :is="link.icon" :size="18" :stroke-width="1.8" aria-hidden="true" />
        <span class="product-label">{{ link.label }}</span>
      </ElMenuItem>
    </ElMenu>
    <SyncStatusIndicator />
  </nav>
</template>
