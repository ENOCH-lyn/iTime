<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { identity } from './lib/auth'
import { startSyncCoordinator, stopSyncCoordinator } from './lib/sync'
const route = useRoute()
const offline = ref(!navigator.onLine)
function updateNetwork() { offline.value = !navigator.onLine }
onMounted(() => {
  window.addEventListener('online', updateNetwork)
  window.addEventListener('offline', updateNetwork)
  startSyncCoordinator()
})
onUnmounted(() => {
  window.removeEventListener('online', updateNetwork)
  window.removeEventListener('offline', updateNetwork)
  stopSyncCoordinator()
})
watch(() => route.path, async () => {
  await nextTick()
  document.querySelector<HTMLElement>('h1')?.focus()
})
</script>

<template>
  <ElConfigProvider :locale="zhCn">
    <div class="app-root" data-ui-library="element-plus">
      <a class="skip-link" href="#main-content">跳到主要内容</a>
      <div v-if="offline" class="network-banner" role="status">
        {{ identity ? '当前离线。显示最近同步的数据。' : '当前离线。账号操作需要联网；已填写内容保留在本页。' }}
      </div>
      <RouterView :key="route.path" />
    </div>
  </ElConfigProvider>
</template>
