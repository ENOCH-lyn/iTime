<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import QrcodeVue from 'qrcode.vue'
import { ArrowRight, Check, Copy, KeyRound, ShieldCheck } from 'lucide-vue-next'
import AppearanceControls from '../components/AppearanceControls.vue'
import FormField from '../components/FormField.vue'
import RecoveryCodesPanel from '../components/RecoveryCodesPanel.vue'
import StatusMessage from '../components/StatusMessage.vue'
import { acceptAdminSession, notifyAdminSessionChange, type AdminSession } from '../lib/admin'
import { api, failure, localFailure, passwordError, type Failure, type Policy as PasswordPolicy } from '../lib/api'

const props = defineProps<{ mode: 'login' | 'setup' }>()
const router = useRouter()
interface AdminPolicy { session_hours: number; setup_minutes: number; recovery_code_count: number }
interface SetupResult { email: string; secret: string; provisioning_uri: string; expires_at: string }
interface ConfirmResult extends AdminSession { recovery_codes: string[] }
const values = reactive({ email: '', password: '', code: '', method: 'totp' as 'totp' | 'recovery' })
const errors = reactive<Record<string, string>>({})
const error = ref<Failure | null>(null)
const pending = ref(false)
const loading = ref(true)
const token = ref('')
const setup = ref<SetupResult | null>(null)
const recoveryCodes = ref<string[]>([])
const adminPolicy = ref<AdminPolicy | null>(null)
const passwordPolicy = ref<PasswordPolicy | null>(null)
const secretCopied = ref(false)
const pageTitle = computed(() => props.mode === 'setup' ? '设置双重验证' : '管理后台登录')
const groupedSecret = computed(() => setup.value?.secret.match(/.{1,4}/g)?.join(' ') ?? '')

function validate(field: 'email' | 'password' | 'code') {
  if (field === 'email') errors.email = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email.trim())
    ? '' : '请输入完整的管理员邮箱。'
  if (field === 'password' && passwordPolicy.value) {
    errors.password = passwordError(values.password, passwordPolicy.value)
  }
  if (field === 'code') {
    errors.code = values.method === 'totp'
      ? (/^\d{6}$/.test(values.code) ? '' : '请输入身份验证器显示的 6 位数字。')
      : (/^[A-Fa-f0-9 -]{20,32}$/.test(values.code) ? '' : '请输入完整的恢复码。')
  }
}
async function loadPolicy() {
  loading.value = true
  try {
    [adminPolicy.value, passwordPolicy.value] = await Promise.all([
      api<AdminPolicy>('/admin/auth/policy'),
      api<PasswordPolicy>('/auth/policy'),
    ])
  } catch (reason) { error.value = failure(reason) }
  finally { loading.value = false }
}
onMounted(async () => {
  document.title = `${pageTitle.value} · iTime`
  if (props.mode === 'setup') {
    token.value = new URLSearchParams(location.hash.slice(1)).get('token') ?? ''
    await router.replace({ path: '/admin/setup', hash: '' })
    if (!/^[A-Za-z0-9_-]{43}$/.test(token.value)) token.value = ''
  } else {
    const result = await api<AdminSession>('/admin/auth/me').catch(() => null)
    if (result) {
      acceptAdminSession(result)
      await router.replace('/admin/security')
      return
    }
  }
  await loadPolicy()
})
onUnmounted(() => {
  token.value = ''
  values.password = ''
  values.code = ''
  setup.value = null
  recoveryCodes.value = []
})

async function beginSetup() {
  if (pending.value || !passwordPolicy.value) return
  error.value = null
  validate('password')
  if (errors.password) { await nextTick(); document.getElementById('admin-password')?.focus(); return }
  if (!token.value) {
    error.value = localFailure('此页面没有有效的管理员设置链接，请在服务器重新生成。')
    return
  }
  pending.value = true
  try {
    setup.value = await api<SetupResult>('/admin/auth/setup', {
      token: token.value, password: values.password,
    })
    values.email = setup.value.email
  } catch (reason) {
    error.value = failure(reason)
    await nextTick(); document.querySelector<HTMLElement>('.error-status')?.focus()
  } finally { pending.value = false }
}
async function confirmSetup() {
  if (pending.value || !setup.value) return
  error.value = null
  values.method = 'totp'
  validate('code')
  if (errors.code) { await nextTick(); document.getElementById('admin-code')?.focus(); return }
  pending.value = true
  try {
    const result = await api<ConfirmResult>('/admin/auth/setup/confirm', {
      token: token.value, password: values.password, code: values.code,
      device_name: `管理网页 · ${navigator.userAgent.includes('Mobile') ? '手机' : '电脑/平板'}`,
    })
    acceptAdminSession(result)
    notifyAdminSessionChange()
    recoveryCodes.value = result.recovery_codes
    values.password = ''; values.code = ''; token.value = ''; setup.value = null
    await nextTick()
    document.getElementById('recovery-title')?.focus()
  } catch (reason) {
    error.value = failure(reason)
    await nextTick(); document.querySelector<HTMLElement>('.error-status')?.focus()
  } finally { pending.value = false }
}
async function login() {
  if (pending.value || !passwordPolicy.value) return
  error.value = null
  validate('email'); validate('password'); validate('code')
  const first = ['email', 'password', 'code'].find(field => errors[field])
  if (first) { await nextTick(); document.getElementById(`admin-${first}`)?.focus(); return }
  pending.value = true
  try {
    const result = await api<AdminSession>('/admin/auth/login', {
      email: values.email.trim(), password: values.password, method: values.method,
      code: values.code,
      device_name: `管理网页 · ${navigator.userAgent.includes('Mobile') ? '手机' : '电脑/平板'}`,
    })
    acceptAdminSession(result)
    notifyAdminSessionChange()
    values.password = ''; values.code = ''
    await router.replace('/admin/security')
  } catch (reason) {
    error.value = failure(reason)
    values.code = ''
    await nextTick(); document.querySelector<HTMLElement>('.error-status')?.focus()
  } finally { pending.value = false }
}
async function copySecret() {
  secretCopied.value = false
  try {
    await navigator.clipboard.writeText(setup.value?.secret ?? '')
    secretCopied.value = true
  } catch {
    error.value = localFailure('浏览器未允许复制，请手动选择设置密钥。')
  }
}
</script>

<template>
  <div class="public-layout admin-public">
    <header class="public-header">
      <RouterLink to="/admin/login" class="admin-wordmark">iTime <span>管理后台</span></RouterLink>
      <div class="header-actions"><RouterLink to="/login">返回用户端</RouterLink><AppearanceControls compact /></div>
    </header>
    <main id="main-content" class="auth-layout admin-auth-layout">
      <aside class="auth-story admin-story">
        <ShieldCheck :size="34" :stroke-width="1.5" aria-hidden="true" />
        <h2>管理权限，<br />需要更严格的保护。</h2>
        <p>密码与第二重验证缺一不可。<br />恢复码请保存在离线位置。</p>
      </aside>
      <section class="auth-form-panel admin-auth-panel">
        <RecoveryCodesPanel v-if="recoveryCodes.length" primary :codes="recoveryCodes"
          :email="values.email || '当前管理员'" action-label="进入管理后台"
          @done="router.replace('/admin/security')" />
        <div v-else class="auth-form-content">
          <h1 tabindex="-1">{{ pageTitle }}</h1>
          <StatusMessage :error="error" :retry-label="!adminPolicy && error?.retryable ? '重新连接' : undefined" @retry="loadPolicy" />
          <p v-if="loading" role="status" class="muted">正在连接管理认证服务…</p>
          <template v-else-if="mode === 'setup'">
            <p v-if="!token" class="status error-status" role="alert">链接不完整或已从地址栏清除，请重新运行服务器设置命令。</p>
            <form v-if="!setup" novalidate :aria-busy="pending" @submit.prevent="beginSetup">
              <FormField id="admin-password" v-model="values.password" label="管理员密码"
                type="password" autocomplete="current-password" :error="errors.password"
                :disabled="pending" @blur="validate('password')" />
              <ElButton type="primary" native-type="submit" class="full-width" :loading="pending" :disabled="pending || !token">
                {{ pending ? '正在验证…' : '继续设置' }}<ArrowRight v-if="!pending" :size="18" aria-hidden="true" />
              </ElButton>
            </form>
            <div v-else class="totp-setup">
              <section aria-labelledby="scan-title">
                <span class="step-number">1</span><h2 id="scan-title">添加到身份验证器</h2>
                <p>扫描二维码，或手动输入设置密钥。不要向任何人发送二维码或密钥。</p>
                <div class="qr-frame"><QrcodeVue :value="setup.provisioning_uri" :size="196" level="M" render-as="svg" /></div>
                <div class="secret-row">
                  <code>{{ groupedSecret }}</code>
                  <ElButton text circle class="icon-button" :aria-label="secretCopied ? '设置密钥已复制' : '复制设置密钥'" @click="copySecret">
                    <Check v-if="secretCopied" :size="18" aria-hidden="true" /><Copy v-else :size="18" aria-hidden="true" />
                  </ElButton>
                </div>
              </section>
              <form novalidate :aria-busy="pending" @submit.prevent="confirmSetup">
                <div class="setup-heading"><span class="step-number">2</span><h2>确认验证码</h2></div>
                <FormField id="admin-code" v-model="values.code" label="6 位验证码"
                  inputmode="numeric" :maxlength="6" autocomplete="one-time-code"
                  :error="errors.code" :disabled="pending" @blur="validate('code')" />
                <p class="field-hint">设置链接于 {{ new Date(setup.expires_at).toLocaleString('zh-CN') }} 到期。</p>
                <ElButton type="primary" native-type="submit" class="full-width" :loading="pending" :disabled="pending">
                  {{ pending ? '正在启用…' : '完成设置' }}<KeyRound v-if="!pending" :size="18" aria-hidden="true" />
                </ElButton>
              </form>
            </div>
          </template>
          <form v-else novalidate :aria-busy="pending" @submit.prevent="login">
            <FormField id="admin-email" v-model="values.email" label="管理员邮箱" type="email"
              autocomplete="username" :error="errors.email" :disabled="pending" @blur="validate('email')" />
            <FormField id="admin-password" v-model="values.password" label="密码" type="password"
              autocomplete="current-password" :error="errors.password" :disabled="pending" @blur="validate('password')" />
            <div class="field">
              <label for="admin-method">第二重验证</label>
              <ElSegmented
                id="admin-method"
                v-model="values.method"
                :options="[
                  { label: '身份验证器', value: 'totp' },
                  { label: '恢复码', value: 'recovery' },
                ]"
                @change="values.code = ''; errors.code = ''"
              />
            </div>
            <FormField id="admin-code" v-model="values.code"
              :label="values.method === 'totp' ? '6 位验证码' : '恢复码'"
              :inputmode="values.method === 'totp' ? 'numeric' : 'text'"
              :maxlength="values.method === 'totp' ? 6 : 32"
              :autocomplete="values.method === 'totp' ? 'one-time-code' : 'off'"
              :error="errors.code" :disabled="pending" @blur="validate('code')" />
            <ElButton type="primary" native-type="submit" class="full-width" :loading="pending" :disabled="pending">
              {{ pending ? '正在验证…' : '进入管理后台' }}<ArrowRight v-if="!pending" :size="18" aria-hidden="true" />
            </ElButton>
            <p v-if="adminPolicy" class="field-hint">管理会话最长保留 {{ adminPolicy.session_hours }} 小时。</p>
          </form>
        </div>
      </section>
    </main>
  </div>
</template>
