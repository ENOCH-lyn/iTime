<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { LogOut, Settings as SettingsIcon } from 'lucide-vue-next'
import ProductNavigation from '../components/ProductNavigation.vue'
import { identity } from '../lib/auth'
import type { ProductArea } from '../lib/product-navigation'
import './app-shell.css'

const props = defineProps<{
  current: ProductArea | null
  hasSecondary?: boolean
  secondaryLabel?: string
  pendingLogout: boolean
  title: string
}>()

const emit = defineEmits<{ logout: [] }>()
const router = useRouter()
const drawerOpen = ref(false)
const avatarLetter = computed(() => (
  identity.value?.user.email?.slice(0, 1).toUpperCase() ?? 'i'
))
const accountEmail = computed(() => identity.value?.user.email ?? '')
const drawerLabel = computed(() => `打开${props.secondaryLabel ?? '导航'}`)

function closeDrawer() {
  drawerOpen.value = false
}

function logoutFromDrawer() {
  drawerOpen.value = false
  emit('logout')
}

function onAccountCommand(command: string | number | object) {
  if (command === 'settings') void router.push('/settings')
  if (command === 'logout') emit('logout')
}
</script>

<template>
  <div class="app-shell" :class="{ 'has-secondary': hasSecondary }">
    <aside class="m3-navigation-rail app-shell-rail" aria-label="产品导航">
      <RouterLink to="/calendar" class="app-shell-brand" aria-label="iTime 日历首页">
        iTime<span aria-hidden="true">.</span>
      </RouterLink>
      <ProductNavigation :current="(current ?? 'ai')" />
    </aside>

    <div class="app-shell-column">
      <header class="app-shell-topbar">
        <button
          v-if="hasSecondary"
          type="button"
          class="m3-icon-button app-shell-menu"
          :aria-label="drawerLabel"
          :title="drawerLabel"
          @click="drawerOpen = true"
        >
          <span class="material-symbols-rounded" aria-hidden="true">menu</span>
        </button>
        <h1 class="app-shell-title" tabindex="-1">{{ title }}</h1>
        <div class="app-shell-actions">
          <slot name="actions" />
          <ElDropdown
            trigger="click"
            placement="bottom-end"
            :hide-on-click="true"
            @command="onAccountCommand"
          >
            <button
              type="button"
              class="app-shell-avatar"
              :aria-label="`账号菜单：${accountEmail}`"
              :title="accountEmail"
            >
              {{ avatarLetter }}
            </button>
            <template #dropdown>
              <ElDropdownMenu class="app-shell-account-menu">
                <ElDropdownItem disabled class="app-shell-account-email">
                  {{ accountEmail }}
                </ElDropdownItem>
                <ElDropdownItem command="settings">
                  <SettingsIcon :size="18" aria-hidden="true" />
                  <span class="app-shell-menu-label">设置</span>
                </ElDropdownItem>
                <ElDropdownItem command="logout" :disabled="pendingLogout">
                  <LogOut :size="18" aria-hidden="true" />
                  <span class="app-shell-menu-label">
                    {{ pendingLogout ? '正在退出…' : '退出登录' }}
                  </span>
                </ElDropdownItem>
              </ElDropdownMenu>
            </template>
          </ElDropdown>
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
        <main id="main-content" class="app-shell-main" tabindex="-1">
          <slot />
        </main>
      </div>
    </div>

    <nav class="m3-navigation-bar app-shell-bottom-nav" aria-label="主导航">
      <ProductNavigation :current="(current ?? 'ai')" />
    </nav>

    <ElDrawer
      v-model="drawerOpen"
      direction="ltr"
      size="min(320px, 92vw)"
      :with-header="false"
      class="app-shell-nav-drawer"
      aria-label="导航"
    >
      <div class="app-shell-drawer-body">
        <div class="app-shell-drawer-top">
          <span class="app-shell-drawer-brand">iTime<span aria-hidden="true">.</span></span>
          <button
            type="button"
            class="m3-icon-button"
            aria-label="关闭导航"
            title="关闭导航"
            @click="closeDrawer"
          >
            <span class="material-symbols-rounded" aria-hidden="true">close</span>
          </button>
        </div>
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
