<script setup lang="ts">
import { computed, ref } from 'vue'
import { LogOut, Menu } from 'lucide-vue-next'
import ProductNavigation from '../components/ProductNavigation.vue'
import { identity } from '../lib/auth'
import type { ProductArea } from '../lib/product-navigation'
import './app-shell.css'

const props = defineProps<{
  current: ProductArea | null
  hasSecondary?: boolean
  secondaryLabel?: string
  pendingLogout: boolean
  title?: string
}>()

const emit = defineEmits<{ logout: [] }>()
const drawerOpen = ref(false)
const avatarLetter = computed(() => (
  identity.value?.user.email?.slice(0, 1).toUpperCase() ?? 'i'
))

function closeDrawer() {
  drawerOpen.value = false
}

function logoutFromDrawer() {
  drawerOpen.value = false
  emit('logout')
}
</script>

<template>
  <div class="app-shell" :class="{ 'has-secondary': hasSecondary }">
    <header class="app-shell-topbar">
      <ElButton
        text
        class="app-shell-mobile-trigger"
        aria-label="打开导航"
        @click="drawerOpen = true"
      >
        <Menu :size="20" aria-hidden="true" />
      </ElButton>
      <RouterLink to="/timetable" class="app-shell-brand">
        iTime<span aria-hidden="true">.</span>
      </RouterLink>
      <div class="app-shell-nav">
        <ProductNavigation :current="(current ?? 'ai')" />
      </div>
      <div class="app-shell-actions">
        <RouterLink to="/ai" class="app-shell-ai">AI</RouterLink>
        <RouterLink
          to="/me"
          class="app-shell-avatar"
          :aria-label="`账号 ${identity?.user.email ?? ''}`"
        >
          {{ avatarLetter }}
        </RouterLink>
        <ElButton
          text
          class="app-shell-desktop-logout"
          :loading="pendingLogout"
          :disabled="pendingLogout"
          aria-label="退出登录"
          @click="emit('logout')"
        >
          <LogOut :size="17" aria-hidden="true" />
        </ElButton>
      </div>
    </header>

    <div class="app-shell-body">
      <aside
        v-if="hasSecondary"
        class="app-shell-secondary"
        :aria-label="secondaryLabel"
      >
        <slot name="secondary" />
      </aside>
      <main id="main-content" class="app-shell-main">
        <slot />
      </main>
    </div>

    <ElDrawer
      v-model="drawerOpen"
      direction="ltr"
      size="min(320px, 92vw)"
      :with-header="false"
      aria-label="导航"
    >
      <div class="app-shell-drawer-body">
        <div class="app-shell-drawer-brand">
          i<span aria-hidden="true">Time</span>
        </div>
        <nav class="app-shell-drawer-primary" aria-label="主要功能">
          <ProductNavigation :current="(current ?? 'ai')" />
        </nav>
        <div
          v-if="hasSecondary && $slots['drawer-secondary']"
          class="app-shell-drawer-secondary"
        >
          <slot name="drawer-secondary" :close="closeDrawer" />
        </div>
        <div class="app-shell-drawer-account">
          <RouterLink to="/settings" class="nav-link" @click="closeDrawer">
            设置
          </RouterLink>
          <RouterLink to="/help" class="nav-link" @click="closeDrawer">
            帮助
          </RouterLink>
          <ElButton
            text
            :loading="pendingLogout"
            :disabled="pendingLogout"
            @click="logoutFromDrawer"
          >
            <LogOut :size="18" aria-hidden="true" />
            {{ pendingLogout ? '正在退出…' : '退出登录' }}
          </ElButton>
        </div>
      </div>
    </ElDrawer>
  </div>
</template>
