<script setup lang="ts">
import AppearanceControls from './AppearanceControls.vue'

defineProps<{
  current?: 'product' | 'demo' | 'help'
}>()
</script>

<template>
  <header class="site-header">
    <RouterLink to="/" class="wordmark" aria-label="iTime 首页">
      iTime<span aria-hidden="true">.</span>
    </RouterLink>
    <nav class="site-navigation" aria-label="公开页面">
      <RouterLink to="/" :aria-current="current === 'product' ? 'page' : undefined">
        产品
      </RouterLink>
      <RouterLink to="/demo" :aria-current="current === 'demo' ? 'page' : undefined">
        演示
      </RouterLink>
      <RouterLink to="/help" :aria-current="current === 'help' ? 'page' : undefined">
        帮助
      </RouterLink>
    </nav>
    <div class="site-header-actions">
      <AppearanceControls compact />
      <RouterLink to="/login" class="site-login">登录</RouterLink>
      <RouterLink to="/register" class="button compact-site-button">开始使用</RouterLink>
    </div>
  </header>
</template>
