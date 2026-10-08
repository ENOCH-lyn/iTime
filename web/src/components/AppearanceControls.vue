<script setup lang="ts">
import { theme, fontSize } from '../lib/appearance'
defineProps<{ compact?: boolean }>()
</script>

<template>
  <div :class="compact ? 'appearance-compact' : 'appearance-settings'">
    <label :class="{ 'sr-only': compact }" for="theme">外观</label>
    <ElSelect id="theme" v-model="theme" aria-label="外观主题">
      <ElOption value="system" label="跟随系统" />
      <ElOption value="light" label="浅色" />
      <ElOption value="dark" label="深色" />
    </ElSelect>
    <template v-if="!compact">
      <label for="font-size">字号</label>
      <ElSelect id="font-size" v-model="fontSize" aria-label="字号">
        <ElOption value="normal" label="标准" />
        <ElOption value="large" label="大" />
        <ElOption value="larger" label="更大" />
      </ElSelect>
      <p class="muted">外观与字号保存在当前浏览器。</p>
    </template>
  </div>
</template>
