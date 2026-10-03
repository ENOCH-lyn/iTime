<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Bot, ClipboardCheck, ExternalLink, FileKey2, FileSearch, KeyRound, LogOut, MailPlus, Palette, ScrollText, ShieldCheck, Users } from 'lucide-vue-next'
import AdminAuditEvents from '../components/AdminAuditEvents.vue'
import AdminAISettings from '../components/AdminAISettings.vue'
import AdminBodyLogs from '../components/AdminBodyLogs.vue'
import AppearanceControls from '../components/AppearanceControls.vue'
import AdminPasswordSettings from '../components/AdminPasswordSettings.vue'
import AdminQualitySamples from '../components/AdminQualitySamples.vue'
import AdminRecoverySettings from '../components/AdminRecoverySettings.vue'
import AdminRegistrationSettings from '../components/AdminRegistrationSettings.vue'
import AdminUserManagement from '../components/AdminUserManagement.vue'
import StatusMessage from '../components/StatusMessage.vue'
import { adminAuthError, adminAuthLoading, adminIdentity, clearAdminSession, notifyAdminSessionChange, refreshAdminSession } from '../lib/admin'
import { api, failure, type Failure } from '../lib/api'

const route = useRoute()
const router = useRouter()
const section = computed(() => String(route.params.section ?? 'security'))
const titles: Record<string, string> = {
  users: '用户管理', registration: '注册与邀请', security: '账号安全',
  ai: 'AI 配置', logs: 'AI 日志', quality: '质量样本', audit: '审计记录',
  password: '修改密码', recovery: '恢复码', appearance: '外观',
}
const pending = ref(false)
const booted = ref(false)
const error = ref<Failure | null>(null)
watch(section, () => { document.title = `${titles[section.value]} · iTime 管理后台` }, { immediate: true })
watch(adminAuthError, value => {
  if (value?.code === 'admin_unauthenticated') void router.replace('/admin/login')
})
onMounted(async () => {
  await refreshAdminSession()
  booted.value = true
  if (!adminIdentity.value) await router.replace('/admin/login')
  window.addEventListener('focus', refresh)
})
function refresh() { void refreshAdminSession() }
onUnmounted(() => window.removeEventListener('focus', refresh))
async function logout() {
  pending.value = true; error.value = null
  try {
    await api('/admin/auth/logout', {})
    clearAdminSession(); notifyAdminSessionChange()
    await router.replace('/admin/login')
  } catch (reason) { error.value = failure(reason) }
  finally { pending.value = false }
}
const links = [
  { to: '/admin/users', title: '用户管理', icon: Users },
  { to: '/admin/registration', title: '注册与邀请', icon: MailPlus },
  { to: '/admin/ai', title: 'AI 配置', icon: Bot },
  { to: '/admin/logs', title: 'AI 日志', icon: FileSearch },
  { to: '/admin/quality', title: '质量样本', icon: ClipboardCheck },
  { to: '/admin/audit', title: '审计记录', icon: ScrollText },
  { to: '/admin/security', title: '账号安全', icon: ShieldCheck },
  { to: '/admin/password', title: '修改密码', icon: KeyRound },
  { to: '/admin/recovery', title: '恢复码', icon: FileKey2 },
  { to: '/admin/appearance', title: '外观', icon: Palette },
]
</script>

<template>
  <div class="settings-layout admin-layout">
    <aside class="settings-sidebar">
      <RouterLink to="/admin/security" class="admin-wordmark">iTime <span>管理后台</span></RouterLink>
      <p class="nav-heading">管理设置</p>
      <nav aria-label="管理设置">
        <RouterLink v-for="link in links" :key="link.to" :to="link.to" class="nav-link"
          :class="{ selected: route.path === link.to }" :aria-current="route.path === link.to ? 'page' : undefined">
          <component :is="link.icon" :size="19" :stroke-width="1.75" aria-hidden="true" />{{ link.title }}
        </RouterLink>
      </nav>
      <div class="sidebar-bottom">
        <RouterLink to="/login" class="nav-link"><ExternalLink :size="19" aria-hidden="true" />返回用户端</RouterLink>
        <button v-if="adminIdentity" class="nav-link logout-button" :disabled="pending" @click="logout">
          <LogOut :size="19" aria-hidden="true" />{{ pending ? '正在退出…' : '退出管理后台' }}
        </button>
      </div>
    </aside>
    <main id="main-content" class="settings-main">
      <header class="settings-heading"><span class="muted">管理后台</span><h1 tabindex="-1">{{ titles[section] }}</h1></header>
      <StatusMessage :error="error" />
      <StatusMessage :error="adminAuthError" :retry-label="adminAuthError?.retryable ? '重新连接' : undefined" @retry="refresh" />
      <p v-if="!booted || (!adminIdentity && adminAuthLoading)" role="status" class="muted">正在验证管理身份…</p>
      <template v-else-if="adminIdentity">
        <AdminUserManagement v-if="section === 'users'" />
        <AdminRegistrationSettings v-else-if="section === 'registration'" />
        <AdminAISettings v-else-if="section === 'ai'" />
        <AdminBodyLogs v-else-if="section === 'logs'" />
        <AdminQualitySamples v-else-if="section === 'quality'" />
        <AdminAuditEvents v-else-if="section === 'audit'" />
        <template v-else-if="section === 'security'">
          <p class="page-description">管理会话与普通用户登录相互独立。</p>
          <dl class="account-details">
            <div><dt>管理员邮箱</dt><dd class="wrap-anywhere">{{ adminIdentity.admin.email }}</dd></div>
            <div><dt>第二重验证</dt><dd>已启用 TOTP<small>本次使用{{ adminIdentity.auth_method === 'totp' ? '身份验证器' : '恢复码' }}登录</small></dd></div>
            <div><dt>当前会话到期</dt><dd>{{ new Date(adminIdentity.expires_at).toLocaleString('zh-CN') }}<small>按当前设备时区显示</small></dd></div>
          </dl>
          <div class="setting-links">
            <RouterLink to="/admin/password"><div><strong>修改管理密码</strong><p>更新后撤销其他所有登录会话。</p></div><ExternalLink :size="20" aria-hidden="true" /></RouterLink>
            <RouterLink to="/admin/recovery"><div><strong>重新生成恢复码</strong><p>需要当前身份验证器验证码。</p></div><ExternalLink :size="20" aria-hidden="true" /></RouterLink>
          </div>
        </template>
        <AdminPasswordSettings v-else-if="section === 'password'" />
        <AdminRecoverySettings v-else-if="section === 'recovery'" />
        <template v-else-if="section === 'appearance'">
          <p class="page-description">选择管理后台的显示方式。</p>
          <AppearanceControls />
        </template>
      </template>
    </main>
  </div>
</template>
