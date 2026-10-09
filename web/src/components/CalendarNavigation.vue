<script setup lang="ts">
import {
  CalendarClock,
  FileUp,
  Plus,
  SlidersHorizontal,
  Trash2,
} from 'lucide-vue-next'

defineProps<{
  seriesCount: number
  termCount: number
}>()

const emit = defineEmits<{
  createSeries: []
  openImport: []
  openTrash: []
  openSettings: []
}>()
</script>

<template>
  <nav class="secondary-nav calendar-navigation" aria-label="日历管理">
    <p class="nav-heading">日历管理</p>
    <button type="button" class="nav-link" @click="emit('createSeries')">
      <Plus :size="18" aria-hidden="true" />新建安排
    </button>
    <button type="button" class="nav-link" data-calendar-import-trigger @click="emit('openImport')">
      <FileUp :size="18" aria-hidden="true" />导入日历
    </button>
    <button type="button" class="nav-link" data-calendar-trash-trigger @click="emit('openTrash')">
      <Trash2 :size="18" aria-hidden="true" />日历回收站
    </button>
    <button type="button" class="nav-link" @click="emit('openSettings')">
      <SlidersHorizontal :size="18" aria-hidden="true" />时间与学期
    </button>
    <div class="calendar-navigation-summary">
      <CalendarClock :size="18" aria-hidden="true" />
      <p><strong>{{ seriesCount }}</strong> 个系列</p>
      <p><strong>{{ termCount }}</strong> 个学期</p>
    </div>
  </nav>
</template>
