<script setup lang="ts">
import { onMounted } from 'vue'
onMounted(() => { document.title = '页面未找到 · iTime' })
</script>

<template>
  <main id="main-content" class="help-content">
    <p class="wordmark">iTime<span aria-hidden="true">.</span></p>
    <h1 tabindex="-1">这个页面不在这里</h1>
    <p class="page-description">链接可能有误。你可以返回任务页面继续。</p>
    <RouterLink to="/timetable" class="button">返回 iTime</RouterLink>
  </main>
</template>
