<script setup lang="ts">
import { CalendarClock, LogOut, Plus, SlidersHorizontal } from 'lucide-vue-next'
import ProductNavigation from './ProductNavigation.vue'

defineProps<{
  email: string
  pendingLogout: boolean
  seriesCount: number
  termCount: number
}>()

const emit = defineEmits<{
  createSeries: []
  openSettings: []
  logout: []
}>()
</script>

<template>
  <aside class="calendar-navigation" aria-label="iTime 导航">
    <div class="calendar-navigation-top">
      <RouterLink to="/calendar" class="wordmark" aria-label="iTime 日历">iTime<span aria-hidden="true">.</span></RouterLink>
      <ProductNavigation current="calendar" />
    </div>

    <div class="calendar-navigation-actions">
      <p class="nav-heading">日历管理</p>
      <button type="button" class="nav-link" @click="emit('createSeries')">
        <Plus :size="18" aria-hidden="true" />新建安排
      </button>
      <button type="button" class="nav-link" @click="emit('openSettings')">
        <SlidersHorizontal :size="18" aria-hidden="true" />时间与学期
      </button>
      <div class="calendar-navigation-summary">
        <CalendarClock :size="18" aria-hidden="true" />
        <p><strong>{{ seriesCount }}</strong> 个系列</p>
        <p><strong>{{ termCount }}</strong> 个学期</p>
      </div>
    </div>

    <div class="task-account">
      <p class="task-account-email">{{ email }}</p>
      <button type="button" class="nav-link logout-button" :disabled="pendingLogout" @click="emit('logout')">
        <LogOut :size="18" aria-hidden="true" />{{ pendingLogout ? '正在退出…' : '退出登录' }}
      </button>
    </div>
  </aside>
</template>
