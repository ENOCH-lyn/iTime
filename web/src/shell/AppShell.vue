<script setup lang="ts">
import { computed, ref } from 'vue'
import { LogOut } from 'lucide-vue-next'
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
    <header class="m3-top-app-bar app-shell-topbar">
      <button
        v-if="hasSecondary"
        type="button"
        class="m3-icon-button app-shell-mobile-trigger"
        aria-label="打开管理导航"
        @click="drawerOpen = true"
      >
        <span class="material-symbols-rounded" aria-hidden="true">menu</span>
      </button>
      <RouterLink to="/timetable" class="app-shell-brand m3-top-app-bar__title">
        iTime<span aria-hidden="true">.</span>
      </RouterLink>
      <div class="app-shell-actions">
        <RouterLink
          to="/ai"
          class="m3-icon-button app-shell-ai"
          aria-label="AI"
          title="AI"
        >
          <span class="material-symbols-rounded" aria-hidden="true">smart_toy</span>
        </RouterLink>
        <RouterLink
          to="/settings"
          class="app-shell-avatar"
          aria-label="设置"
          title="设置"
        >
          {{ avatarLetter }}
        </RouterLink>
        <button
          type="button"
          class="m3-icon-button app-shell-desktop-logout"
          :disabled="pendingLogout"
          aria-label="退出登录"
          title="退出登录"
          @click="emit('logout')"
        >
          <LogOut :size="20" aria-hidden="true" />
        </button>
      </div>
    </header>

    <div class="app-shell-body">
      <aside class="m3-navigation-rail app-shell-rail" aria-label="主导航">
        <ProductNavigation :current="(current ?? 'ai')" />
      </aside>
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

    <nav class="m3-navigation-bar app-shell-bottom-nav" aria-label="主导航">
      <ProductNavigation :current="(current ?? 'ai')" />
    </nav>

    <ElDrawer
      v-model="drawerOpen"
      direction="ltr"
      size="min(320px, 92vw)"
      :with-header="false"
      aria-label="导航"
    >
      <div class="app-shell-drawer-body">
        <div class="app-shell-drawer-brand">iTime<span aria-hidden="true">.</span></div>
        <div v-if="$slots['drawer-secondary']" class="app-shell-drawer-secondary">
          <slot name="drawer-secondary" :close="closeDrawer" />
        </div>
        <div class="app-shell-drawer-account">
          <RouterLink to="/settings" class="m3-button m3-button--text" @click="closeDrawer">
            设置
          </RouterLink>
          <button
            type="button"
            class="m3-button m3-button--text app-shell-drawer-logout"
            :disabled="pendingLogout"
            @click="logoutFromDrawer"
          >
            退出登录
          </button>
        </div>
      </div>
    </ElDrawer>
  </div>
</template>
