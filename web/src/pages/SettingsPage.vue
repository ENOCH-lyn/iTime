<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, CirclePause, ExternalLink, X } from 'lucide-vue-next'
import AccountDeletionSettings from '../components/AccountDeletionSettings.vue'
import AppearanceControls from '../components/AppearanceControls.vue'
import DataExportSettings from '../components/DataExportSettings.vue'
import DeviceSettings from '../components/DeviceSettings.vue'
import PasswordSettings from '../components/PasswordSettings.vue'
import PrivacySettings from '../components/PrivacySettings.vue'
import QuotaOverview from '../components/QuotaOverview.vue'
import StatusMessage from '../components/StatusMessage.vue'
import SyncSettings from '../components/SyncSettings.vue'
import AppShell from '../shell/AppShell.vue'
import { useAppShell } from '../shell/useAppShell'
import {
  authError,
  authLoading,
  identity,
  refreshSession,
} from '../lib/auth'
import '../ai-workspace.css'

const route = useRoute()
const router = useRouter()
const booted = ref(false)
const { pendingLogout, logoutError, logout } = useAppShell()
const passwordOpen = ref(false)
const devicesOpen = ref(false)
const quotaOpen = ref(false)
const syncOpen = ref(false)
let identityRedirectTimer = 0

const overlayByHash = {
  '#password': () => { passwordOpen.value = true },
  '#devices': () => { devicesOpen.value = true },
  '#quota': () => { quotaOpen.value = true },
  '#sync': () => { syncOpen.value = true },
} as const

watch(authError, error => {
  if (error?.code === 'unauthenticated') void router.replace('/login')
})
watch(identity, current => {
  window.clearTimeout(identityRedirectTimer)
  if (!booted.value || current) return
  identityRedirectTimer = window.setTimeout(() => {
    if (!identity.value && route.path === '/settings') {
      void router.replace('/login')
    }
  }, 0)
})

async function revealHash() {
  await nextTick()
  passwordOpen.value = route.hash === '#password'
  devicesOpen.value = route.hash === '#devices'
  quotaOpen.value = route.hash === '#quota'
  syncOpen.value = route.hash === '#sync'
  const action = overlayByHash[route.hash as keyof typeof overlayByHash]
  if (action) {
    action()
    return
  }
  if (!route.hash) return
  document.querySelector<HTMLElement>(route.hash)?.scrollIntoView({
    block: 'start',
  })
}

watch(() => route.hash, () => void revealHash(), { immediate: true })

onMounted(async () => {
  document.title = '设置 · iTime'
  await refreshSession()
  booted.value = true
  if (!identity.value) {
    await router.replace('/login')
    return
  }
  await revealHash()
  window.addEventListener('focus', refresh)
})

onUnmounted(() => {
  window.clearTimeout(identityRedirectTimer)
  window.removeEventListener('focus', refresh)
})

function refresh() {
  void refreshSession()
}

async function closeOverlay(hash: string) {
  if (route.hash === `#${hash}`) await router.replace('/settings')
}
</script>

<template>
  <AppShell
    :current="null"
    title="设置"
    :pending-logout="pendingLogout"
    @logout="logout"
  >
    <div class="settings-page-wrapper">
      <nav class="settings-return-nav" aria-label="设置导航">
        <RouterLink
          to="/ai"
          class="icon-button settings-return-link"
          aria-label="返回 AI 对话"
        >
          <ArrowLeft :size="20" aria-hidden="true" />
          <span>返回 AI 对话</span>
        </RouterLink>
      </nav>

      <div id="main-content" class="settings-page">
        <header class="settings-page-header">
          <h1 tabindex="-1">设置</h1>
        </header>

        <StatusMessage :error="logoutError" />
        <StatusMessage
          :error="authError"
          :retry-label="authError?.retryable ? '重新连接' : undefined"
          @retry="refresh"
        />
        <p
          v-if="!booted || (!identity && authLoading)"
          role="status"
          class="muted"
        >
          正在读取账号信息…
        </p>

        <template v-else-if="identity">
          <section
            id="account"
            class="settings-section"
            aria-labelledby="account-title"
          >
            <h2 id="account-title">账号与安全</h2>
            <div
              v-if="identity.user.ai_access === 'frozen'"
              class="status restriction-status"
              role="status"
            >
              <CirclePause :size="20" aria-hidden="true" />
              <p>此账号的 AI 功能已暂停，手动查看和编辑仍可使用。</p>
            </div>
            <div class="settings-row">
              <div>
                <strong>邮箱</strong>
                <p class="wrap-anywhere">{{ identity.user.email }}</p>
              </div>
              <span class="verified-label">已验证</span>
            </div>
            <div class="settings-row">
              <div>
                <strong>密码</strong>
                <p>更新密码或退出其他登录设备。</p>
              </div>
              <ElButton @click="passwordOpen = true">修改密码</ElButton>
            </div>
            <div class="settings-row">
              <div>
                <strong>登录设备</strong>
                <p>查看当前有效会话并远程退出。</p>
              </div>
              <ElButton @click="devicesOpen = true">查看设备</ElButton>
            </div>
          </section>

          <section
            id="appearance"
            class="settings-section"
            aria-labelledby="preference-title"
          >
            <h2 id="preference-title">偏好</h2>
            <AppearanceControls />
            <div class="settings-row settings-time-link">
              <div>
                <strong>时间与学期</strong>
                <p>管理时区、周起始日、活动日和学期。</p>
              </div>
              <RouterLink to="/calendar" class="settings-command-link">
                打开日历
                <ExternalLink :size="16" aria-hidden="true" />
              </RouterLink>
            </div>
          </section>

          <section
            id="privacy"
            class="settings-section"
            aria-labelledby="ai-privacy-title"
          >
            <h2 id="ai-privacy-title">AI 与隐私</h2>
            <QuotaOverview
              v-if="!quotaOpen"
              compact
              @open-details="quotaOpen = true"
            />
            <PrivacySettings compact />
          </section>

          <section
            id="data"
            class="settings-section"
            aria-labelledby="data-title"
          >
            <h2 id="data-title">数据与同步</h2>
            <SyncSettings
              v-if="!syncOpen"
              compact
              @open-details="syncOpen = true"
            />
            <DataExportSettings />
          </section>

          <section
            id="account-actions"
            class="settings-section"
            aria-labelledby="account-actions-title"
          >
            <h2 id="account-actions-title">账号操作</h2>
            <div class="settings-row">
              <div>
                <strong>帮助</strong>
                <p>查看账号、安全和失败恢复说明。</p>
              </div>
              <RouterLink to="/help" class="settings-command-link">
                打开帮助
                <ExternalLink :size="16" aria-hidden="true" />
              </RouterLink>
            </div>
            <div class="settings-row">
              <div>
                <strong>退出登录</strong>
                <p>仅退出当前设备。</p>
              </div>
              <ElButton
                :loading="pendingLogout"
                :disabled="pendingLogout"
                @click="logout"
              >
                {{ pendingLogout ? '正在退出…' : '退出登录' }}
              </ElButton>
            </div>
            <AccountDeletionSettings />
          </section>
        </template>
      </div>
    </div>

    <ElDialog
      v-model="passwordOpen"
      title="修改密码"
      width="min(520px, calc(100vw - 24px))"
      :show-close="false"
      @closed="closeOverlay('password')"
    >
      <PasswordSettings v-if="passwordOpen" />
      <template #footer>
        <ElButton @click="passwordOpen = false">关闭</ElButton>
      </template>
    </ElDialog>

    <ElDrawer
      v-model="devicesOpen"
      direction="rtl"
      size="min(560px, 100vw)"
      :show-close="false"
      :close-on-press-escape="false"
      @closed="closeOverlay('devices')"
    >
      <template #header="{ close, titleId, titleClass }">
        <h2 :id="titleId" :class="titleClass">登录设备</h2>
        <ElButton
          text
          circle
          aria-label="关闭"
          @click="close"
        >
          <X :size="20" aria-hidden="true" />
        </ElButton>
      </template>
      <DeviceSettings v-if="devicesOpen" />
    </ElDrawer>

    <ElDrawer
      v-model="quotaOpen"
      title="AI 额度"
      direction="rtl"
      size="min(760px, 100vw)"
      @closed="closeOverlay('quota')"
    >
      <QuotaOverview v-if="quotaOpen" />
    </ElDrawer>

    <ElDrawer
      v-model="syncOpen"
      title="同步与离线"
      direction="rtl"
      size="min(680px, 100vw)"
      @closed="closeOverlay('sync')"
    >
      <SyncSettings v-if="syncOpen" />
    </ElDrawer>
  </AppShell>
</template>
