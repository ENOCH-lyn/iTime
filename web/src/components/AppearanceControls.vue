<script setup lang="ts">
import { theme, fontSize } from '../lib/appearance'
defineProps<{ compact?: boolean }>()
</script>

<template>
  <div :class="compact ? 'appearance-compact' : 'appearance-settings'">
    <label :class="{ 'sr-only': compact }" for="theme">外观</label>
    <select id="theme" v-model="theme" aria-label="外观主题">
      <option value="system">跟随系统</option><option value="light">浅色</option><option value="dark">深色</option>
    </select>
    <template v-if="!compact">
      <label for="font-size">字号</label>
      <select id="font-size" v-model="fontSize">
        <option value="normal">标准</option><option value="large">大</option><option value="larger">更大</option>
      </select>
      <p class="muted">外观与字号保存在当前浏览器。</p>
    </template>
  </div>
</template>
