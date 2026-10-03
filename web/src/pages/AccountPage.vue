<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowUpRight, CalendarDays, CirclePause, DatabaseBackup, KeyRound, ListChecks, ListTodo, LogOut, MessageCircleMore, Monitor, Palette, ShieldCheck, UserRound, WalletCards } from 'lucide-vue-next'
import { api, failure, type Failure } from '../lib/api'
import { authError, authLoading, identity, refreshSession, clearSession, notifySessionChange } from '../lib/auth'
import AppearanceControls from '../components/AppearanceControls.vue'
import StatusMessage from '../components/StatusMessage.vue'
import PasswordSettings from '../components/PasswordSettings.vue'
import DeviceSettings from '../components/DeviceSettings.vue'
import PrivacySettings from '../components/PrivacySettings.vue'
import QuotaOverview from '../components/QuotaOverview.vue'
import DataExportSettings from '../components/DataExportSettings.vue'

const route = useRoute()
const router = useRouter()
const section = computed(() => String(route.params.section ?? 'overview'))
const titles: Record<string, string> = {
  overview: '账号与安全',
  password: '修改密码',
  devices: '登录设备',
  privacy: '隐私与质量',
  quota: 'AI 额度',
  data: '数据管理',
  appearance: '外观',
}
const pending = ref(false)
const logoutError = ref<Failure | null>(null)
const booted = ref(false)
watch(section, () => { document.title = `${titles[section.value]} · iTime` }, { immediate: true })
watch(authError, error => {
  if (error?.code === 'unauthenticated') void router.replace('/login')
})
onMounted(async () => {
  await refreshSession()
  booted.value = true
  window.addEventListener('focus', refresh)
})
function refresh() { void refreshSession() }
onUnmounted(() => window.removeEventListener('focus', refresh))
async function logout() {
  pending.value = true; logoutError.value = null
  try {
    await api('/auth/logout', {})
    clearSession(); notifySessionChange()
    await router.replace('/login')
  } catch (err) { logoutError.value = failure(err) }
  finally { pending.value = false }
}
const links = [
  { to: '/ai', title: '返回 AI 对话', icon: MessageCircleMore },
  { to: '/tasks', title: '返回任务', icon: ListTodo },
  { to: '/plans', title: '返回规划', icon: ListChecks },
  { to: '/calendar', title: '返回日历', icon: CalendarDays },
  { to: '/account', title: '账号与安全', icon: UserRound },
  { to: '/account/password', title: '修改密码', icon: KeyRound },
  { to: '/account/devices', title: '登录设备', icon: Monitor },
  { to: '/account/privacy', title: '隐私与质量', icon: ShieldCheck },
  { to: '/account/quota', title: 'AI 额度', icon: WalletCards },
  { to: '/account/data', title: '数据管理', icon: DatabaseBackup },
  { to: '/account/appearance', title: '外观', icon: Palette },
]
</script>

<template>
  <div class="settings-layout">
    <aside class="settings-sidebar">
      <RouterLink to="/" class="wordmark" aria-label="iTime 首页">iTime<span aria-hidden="true">.</span></RouterLink>
      <p class="nav-heading">个人设置</p>
      <nav aria-label="个人设置">
        <RouterLink v-for="link in links" :key="link.to" :to="link.to" class="nav-link"
          :class="{ selected: route.path === link.to }" :aria-current="route.path === link.to ? 'page' : undefined">
          <component :is="link.icon" :size="19" :stroke-width="1.75" aria-hidden="true" />{{ link.title }}
        </RouterLink>
      </nav>
      <div class="sidebar-bottom">
        <RouterLink to="/help" class="nav-link"><ArrowUpRight :size="19" aria-hidden="true" />帮助</RouterLink>
        <button v-if="identity" class="nav-link logout-button" :disabled="pending" @click="logout"><LogOut :size="19" aria-hidden="true" />{{ pending ? '正在退出…' : '退出登录' }}</button>
      </div>
    </aside>
    <main id="main-content" class="settings-main">
      <header class="settings-heading"><span class="muted">设置</span><h1 tabindex="-1">{{ titles[section] }}</h1></header>
      <StatusMessage :error="logoutError" />
      <StatusMessage :error="authError" :retry-label="authError?.retryable ? '重新连接' : undefined" @retry="refresh" />
      <p v-if="!booted || (!identity && authLoading)" role="status" class="muted">正在读取账号信息…</p>
      <template v-else-if="identity">
        <template v-if="section === 'overview'">
          <p class="page-description">管理你的账号，以及可以访问它的设备。</p>
          <div v-if="identity.user.ai_access === 'frozen'" class="status restriction-status" role="status">
            <CirclePause :size="20" aria-hidden="true" />
            <p>此账号的 AI 功能已暂停，手动查看和编辑仍可使用。</p>
          </div>
          <dl class="account-details">
            <div><dt>邮箱</dt><dd class="wrap-anywhere">{{ identity.user.email }}<span class="verified-label">已验证</span></dd></div>
            <div><dt>AI 功能</dt><dd>{{ identity.user.ai_access === 'allowed' ? '可用' : '已暂停' }}</dd></div>
            <div><dt>当前会话到期</dt><dd>{{ new Date(identity.expires_at).toLocaleString('zh-CN') }}<small>按当前设备时区显示</small></dd></div>
          </dl>
          <div class="setting-links">
            <RouterLink to="/account/password"><div><strong>修改密码</strong><p>更新密码，并选择是否退出其他设备。</p></div><ArrowUpRight :size="20" aria-hidden="true" /></RouterLink>
            <RouterLink to="/account/devices"><div><strong>查看登录设备</strong><p>发现不认识的设备时，可以远程退出。</p></div><ArrowUpRight :size="20" aria-hidden="true" /></RouterLink>
            <RouterLink to="/account/privacy"><div><strong>隐私与质量</strong><p>管理 AI 质量样本参与状态和查看数据期限。</p></div><ArrowUpRight :size="20" aria-hidden="true" /></RouterLink>
            <RouterLink to="/account/quota"><div><strong>查看 AI 额度</strong><p>核对今日可用、处理中预留和已结算记录。</p></div><ArrowUpRight :size="20" aria-hidden="true" /></RouterLink>
            <RouterLink to="/account/data"><div><strong>导出个人数据</strong><p>生成包含完整 JSON 和便携格式的数据包。</p></div><ArrowUpRight :size="20" aria-hidden="true" /></RouterLink>
          </div>
        </template>
        <PasswordSettings v-else-if="section === 'password'" />
        <DeviceSettings v-else-if="section === 'devices'" />
        <PrivacySettings v-else-if="section === 'privacy'" />
        <QuotaOverview v-else-if="section === 'quota'" />
        <DataExportSettings v-else-if="section === 'data'" />
        <template v-else-if="section === 'appearance'"><p class="page-description">选择让你阅读舒适的外观。</p><AppearanceControls /></template>
      </template>
      <RouterLink v-else-if="booted" to="/login" class="button">返回登录</RouterLink>
    </main>
  </div>
</template>
