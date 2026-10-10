<script setup lang="ts">
import { ref } from 'vue'
import { CircleHelp, LogOut, Menu, Settings } from 'lucide-vue-next'
import ProductNavigation from '../components/ProductNavigation.vue'
import { identity } from '../lib/auth'
import type { ProductArea } from '../lib/product-navigation'
import './app-shell.css'

defineProps<{
  current: ProductArea | null
  hasSecondary?: boolean
  secondaryLabel?: string
  pendingLogout: boolean
  title?: string
}>()

const emit = defineEmits<{ logout: [] }>()
const drawerOpen = ref(false)

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
    <header class="app-shell-mobile-topbar">
      <ElButton
        text
        class="app-shell-mobile-trigger"
        :aria-label="'打开导航'"
        @click="drawerOpen = true"
      >
        <Menu :size="20" aria-hidden="true" />
      </ElButton>
      <strong class="app-shell-mobile-title">{{ title || 'iTime' }}</strong>
      <span class="app-shell-mobile-spacer" aria-hidden="true"></span>
    </header>

    <aside class="app-shell-primary" aria-label="主要导航">
      <RouterLink to="/ai" class="app-shell-brand">
        i<span aria-hidden="true">Time</span>
      </RouterLink>
      <ProductNavigation :current="(current ?? 'ai')" />
      <div v-if="hasSecondary" class="app-shell-primary-secondary" :aria-label="secondaryLabel">
        <slot name="secondary" />
      </div>
      <div class="app-shell-primary-footer">
        <RouterLink to="/settings" class="nav-link">
          <Settings :size="18" aria-hidden="true" />
          <span>设置</span>
        </RouterLink>
        <RouterLink to="/help" class="nav-link">
          <CircleHelp :size="18" aria-hidden="true" />
          <span>帮助</span>
        </RouterLink>
        <ElButton
          text
          class="nav-link app-shell-logout"
          :loading="pendingLogout"
          :disabled="pendingLogout"
          @click="emit('logout')"
        >
          <LogOut :size="18" aria-hidden="true" />
          <span>{{ pendingLogout ? '正在退出…' : '退出登录' }}</span>
        </ElButton>
        <p v-if="identity" class="app-shell-identity" aria-hidden="true">
          {{ identity.user.email }}
        </p>
      </div>
    </aside>

    <main id="main-content" class="app-shell-main">
      <slot />
    </main>

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
            <Settings :size="18" aria-hidden="true" />
            设置
          </RouterLink>
          <RouterLink to="/help" class="nav-link" @click="closeDrawer">
            <CircleHelp :size="18" aria-hidden="true" />
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
