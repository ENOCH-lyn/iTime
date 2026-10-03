<script setup lang="ts">
import {
  ChevronLeft,
  ChevronRight,
  LocateFixed,
  LogOut,
} from 'lucide-vue-next'
import type { StatisticsScope } from '../lib/statistics'
import ProductNavigation from './ProductNavigation.vue'

defineProps<{
  email: string
  scope: StatisticsScope
  rangeLabel: string
  canMoveNext: boolean
  loading: boolean
  pendingLogout: boolean
}>()

const emit = defineEmits<{
  scope: [value: StatisticsScope]
  previous: []
  current: []
  next: []
  logout: []
}>()
</script>

<template>
  <aside class="statistics-sidebar" aria-label="iTime 导航">
    <div class="statistics-sidebar-top">
      <RouterLink to="/stats" class="wordmark" aria-label="iTime 统计">
        iTime<span aria-hidden="true">.</span>
      </RouterLink>
      <ProductNavigation current="stats" />
    </div>

    <section class="statistics-range-controls" aria-labelledby="statistics-range-title">
      <h2 id="statistics-range-title">统计范围</h2>
      <div class="statistics-scope-switch" role="tablist" aria-label="统计周期">
        <button
          v-for="item in ([
            ['day', '日'],
            ['week', '周'],
            ['month', '月'],
          ] as const)"
          :key="item[0]"
          type="button"
          role="tab"
          :aria-selected="scope === item[0]"
          :class="{ selected: scope === item[0] }"
          :disabled="loading"
          @click="emit('scope', item[0])"
        >
          {{ item[1] }}
        </button>
      </div>
      <p class="statistics-range-label">{{ rangeLabel }}</p>
      <div class="statistics-range-actions">
        <button
          type="button"
          class="icon-button"
          title="上一范围"
          aria-label="上一范围"
          :disabled="loading"
          @click="emit('previous')"
        >
          <ChevronLeft :size="19" aria-hidden="true" />
        </button>
        <button
          type="button"
          class="icon-button"
          title="回到当前"
          aria-label="回到当前"
          :disabled="loading"
          @click="emit('current')"
        >
          <LocateFixed :size="18" aria-hidden="true" />
        </button>
        <button
          type="button"
          class="icon-button"
          title="下一范围"
          aria-label="下一范围"
          :disabled="loading || !canMoveNext"
          @click="emit('next')"
        >
          <ChevronRight :size="19" aria-hidden="true" />
        </button>
      </div>
    </section>

    <div class="statistics-account">
      <RouterLink to="/account" class="nav-link" :title="email">{{ email }}</RouterLink>
      <button
        type="button"
        class="icon-button"
        title="退出登录"
        aria-label="退出登录"
        :disabled="pendingLogout"
        @click="emit('logout')"
      >
        <LogOut :size="18" aria-hidden="true" />
      </button>
    </div>
  </aside>
</template>
