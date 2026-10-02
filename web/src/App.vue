<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
const route = useRoute()
const offline = ref(!navigator.onLine)
function updateNetwork() { offline.value = !navigator.onLine }
onMounted(() => {
  window.addEventListener('online', updateNetwork)
  window.addEventListener('offline', updateNetwork)
})
onUnmounted(() => {
  window.removeEventListener('online', updateNetwork)
  window.removeEventListener('offline', updateNetwork)
})
watch(() => route.path, async () => {
  await nextTick()
  document.querySelector<HTMLElement>('h1')?.focus()
})
</script>

<template>
  <a class="skip-link" href="#main-content">跳到主要内容</a>
  <div v-if="offline" class="network-banner" role="status">当前离线。账号操作需要联网；已填写内容保留在本页。</div>
  <RouterView :key="route.path" />
</template>
