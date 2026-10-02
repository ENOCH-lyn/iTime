<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowRight, ArrowLeft, MailCheck } from 'lucide-vue-next'
import FormField from '../components/FormField.vue'
import StatusMessage from '../components/StatusMessage.vue'
import AppearanceControls from '../components/AppearanceControls.vue'
import { api, failure, localFailure, passwordError, type Failure, type Policy } from '../lib/api'
import { acceptSession, notifySessionChange, type Session } from '../lib/auth'

const props = defineProps<{ mode: string }>()
const route = useRoute()
const router = useRouter()
const copy: Record<string, { title: string; description: string; action: string }> = {
  login: { title: '欢迎回来', description: '登录 iTime，继续整理你的时间。', action: '登录' },
  register: { title: '创建你的账号', description: '从一件想完成的事开始。', action: '创建账号' },
  'verify-email': { title: '验证邮箱', description: '请输入注册时设置的密码，确认这是你的账号。', action: '验证邮箱' },
  'resend-verification': { title: '重新发送验证邮件', description: '输入注册邮箱，获取新的验证链接。', action: '发送验证邮件' },
  'forgot-password': { title: '找回密码', description: '我们会向你的邮箱发送重设密码的链接。', action: '发送重设邮件' },
  'reset-password': { title: '设置新密码', description: '重设后，所有登录设备将退出。', action: '重设密码' },
}
const info = computed(() => copy[props.mode]!)
const needsEmail = computed(() => !['verify-email', 'reset-password'].includes(props.mode))
const needsPassword = computed(() => ['login', 'register', 'verify-email', 'reset-password'].includes(props.mode))
const needsConfirmation = computed(() => ['register', 'reset-password'].includes(props.mode))
const needsToken = computed(() => ['verify-email', 'reset-password'].includes(props.mode))
const values = reactive({ email: '', password: '', confirmation: '' })
const errors = reactive<Record<string, string>>({})
const error = ref<Failure | null>(null)
const success = ref('')
const policy = ref<Policy | null>(null)
const loadingPolicy = ref(true)
const pending = ref(false)
const finished = ref(false)
const token = ref('')
const invitationToken = ref('')
const cooldown = ref(0)
let cooldownUntil = 0
let timer: number | undefined
const passwordHint = computed(() => policy.value
  ? `${policy.value.password_min_length}—${policy.value.password_max_length} 个字符，可使用短语。`
  : '')
const passwordLabel = computed(() => props.mode === 'reset-password' ? '新密码' : '密码')
const mailFlow = computed(() => ['register', 'resend-verification', 'forgot-password'].includes(props.mode))
const invitationRequired = computed(() =>
  props.mode === 'register' && policy.value?.registration_mode === 'invite')

watch(() => route.hash, hash => {
  if (!hash || (!needsToken.value && props.mode !== 'register')) return
  const fragment = new URLSearchParams(hash.slice(1))
  token.value = fragment.get('token') ?? ''
  invitationToken.value = fragment.get('invite') ?? ''
  if (!/^[A-Za-z0-9_-]{43}$/.test(token.value)) token.value = ''
  if (!/^[A-Za-z0-9_-]{43}$/.test(invitationToken.value)) invitationToken.value = ''
  // Keep secrets out of history, storage, referrers, and later navigations.
  void router.replace({ path: route.path, query: route.query, hash: '' })
}, { immediate: true })

async function loadPolicy() {
  loadingPolicy.value = true
  error.value = null
  try { policy.value = await api<Policy>('/auth/policy') }
  catch (err) { error.value = failure(err) }
  finally { loadingPolicy.value = false }
}
onMounted(() => {
  document.title = `${info.value.title} · iTime`
  void loadPolicy()
  timer = window.setInterval(() => { cooldown.value = Math.max(0, Math.ceil((cooldownUntil - Date.now()) / 1000)) }, 500)
})
onUnmounted(() => {
  clearInterval(timer)
  token.value = ''
  invitationToken.value = ''
  values.password = ''
  values.confirmation = ''
})

function validate(field: 'email' | 'password' | 'confirmation') {
  if (field === 'email') errors.email = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(values.email.trim()) ? '' : '请输入完整的邮箱地址。'
  if (field === 'password' && policy.value) errors.password = passwordError(values.password, policy.value)
  if (field === 'confirmation') errors.confirmation = values.password === values.confirmation && values.confirmation
    ? '' : '两次输入的密码不一致。'
}
function startCooldown() {
  cooldown.value = policy.value!.mail_cooldown_seconds
  cooldownUntil = Date.now() + cooldown.value * 1000
}
async function submit() {
  if (pending.value || !policy.value) return
  error.value = null
  if (needsEmail.value) validate('email')
  if (needsPassword.value) validate('password')
  if (needsConfirmation.value) validate('confirmation')
  const first = Object.keys(errors).find(key => errors[key])
  if (first) { await nextTick(); document.getElementById(first)?.focus(); return }
  if (needsToken.value && !token.value) {
    error.value = localFailure('此页面没有有效的邮件链接。请重新打开邮件，或重新发送。')
    return
  }
  if (invitationRequired.value && !invitationToken.value) {
    error.value = localFailure('当前仅接受邀请注册，请打开管理员提供的邀请链接。')
    return
  }
  pending.value = true
  try {
    const email = values.email.trim()
    if (props.mode === 'login') {
      const session = await api<Session>('/auth/login', {
        email, password: values.password,
        device_name: `网页浏览器 · ${navigator.userAgent.includes('Mobile') ? '手机' : '电脑/平板'}`,
      })
      acceptSession(session)
      notifySessionChange()
      await router.replace('/tasks')
    } else {
      const body = needsToken.value
        ? { token: token.value, [props.mode === 'reset-password' ? 'new_password' : 'password']: values.password }
        : {
            email,
            ...(props.mode === 'register'
              ? { password: values.password, invitation_token: invitationToken.value || undefined }
              : {}),
          }
      const result = await api<{ message: string }>(`/auth/${props.mode}`, body)
      success.value = result.message
      finished.value = true
      if (mailFlow.value) startCooldown()
      else { token.value = ''; invitationToken.value = ''; notifySessionChange() }
    }
    values.password = ''; values.confirmation = ''
  } catch (err) {
    error.value = failure(err)
    for (const field of error.value.details?.fields ?? []) {
      const name = field.path.split('.').pop()!
      errors[name === 'new_password' ? 'password' : name] = '请检查此字段的格式。'
    }
    await nextTick()
    document.querySelector<HTMLElement>('.error-status')?.focus()
  } finally { pending.value = false }
}
async function resend() {
  if (pending.value || cooldown.value || !policy.value) return
  pending.value = true; error.value = null
  try {
    await api(`/auth/${props.mode === 'forgot-password' ? 'forgot-password' : 'resend-verification'}`, { email: values.email.trim() })
    startCooldown()
    success.value = '已受理重新发送请求。请检查收件箱和垃圾邮件，使用最新的链接。'
  } catch (err) { error.value = failure(err) }
  finally { pending.value = false }
}
</script>

<template>
  <div class="public-layout">
    <header class="public-header">
      <RouterLink to="/" class="wordmark" aria-label="iTime 首页">iTime<span aria-hidden="true">.</span></RouterLink>
      <AppearanceControls compact />
    </header>
    <main id="main-content" class="auth-layout">
      <aside class="auth-story">
        <div class="story-line" aria-hidden="true"></div>
        <h2>把时间，<br />留给重要的事。</h2>
        <p>整理眼前的任务，<br />也为自己留一点从容。</p>
        <span class="story-foot">一步一步，找到自己的节奏。</span>
      </aside>
      <section class="auth-form-panel">
        <div class="auth-form-content">
          <template v-if="finished">
            <MailCheck v-if="mailFlow" class="success-icon" :size="32" aria-hidden="true" />
            <h1 tabindex="-1">{{ mailFlow ? '去邮箱看看吧' : (mode === 'verify-email' ? '邮箱已验证' : '密码已重设') }}</h1>
            <p v-if="mailFlow" class="page-description wrap-anywhere">发送至 {{ values.email.trim() }}。邮件可能需要一点时间。</p>
            <StatusMessage :error="error" :success="success" />
            <template v-if="mailFlow">
              <p class="muted">没有收到？先检查垃圾邮件。重新发送后，请使用最新链接。</p>
              <button class="button secondary full-width" :disabled="pending || cooldown > 0" @click="resend">
                {{ pending ? '正在发送…' : cooldown > 0 ? `${cooldown} 秒后可重新发送` : '重新发送邮件' }}
              </button>
              <button class="text-button full-width" @click="finished = false; success = ''; error = null">修改邮箱</button>
            </template>
            <RouterLink to="/login" class="button full-width">{{ mailFlow ? '返回登录' : '前往登录' }}<ArrowRight :size="18" aria-hidden="true" /></RouterLink>
          </template>
          <template v-else>
            <RouterLink v-if="!['login', 'register'].includes(mode)" class="back-link" to="/login"><ArrowLeft :size="16" aria-hidden="true" />返回登录</RouterLink>
            <h1 tabindex="-1">{{ info.title }}</h1>
            <p class="page-description">{{ info.description }}</p>
            <p v-if="needsToken && !token" class="status error-status" role="alert">链接不完整或已从地址栏清除。请重新打开邮件中的链接。</p>
            <StatusMessage :error="error" :retry-label="!policy && !loadingPolicy ? '重新连接' : undefined" @retry="loadPolicy" />
            <p v-if="loadingPolicy" role="status" class="muted">正在连接账号服务…</p>
            <form v-if="policy" novalidate :aria-busy="pending" @submit.prevent="submit">
              <p v-if="invitationRequired" class="invitation-notice" :class="{ 'missing-invitation': !invitationToken }">
                {{ invitationToken ? '此注册将使用邮箱专属邀请。' : '当前仅接受邀请注册，请打开管理员提供的邀请链接。' }}
              </p>
              <FormField v-if="needsEmail" id="email" v-model="values.email" label="邮箱" type="email" autocomplete="username"
                :error="errors.email" :disabled="pending" @blur="validate('email')" />
              <FormField v-if="needsPassword" id="password" v-model="values.password" :label="passwordLabel" type="password"
                :autocomplete="needsConfirmation ? 'new-password' : 'current-password'" :hint="needsConfirmation ? passwordHint : undefined"
                :error="errors.password" :disabled="pending" @blur="validate('password')" />
              <FormField v-if="needsConfirmation" id="confirmation" v-model="values.confirmation" label="确认密码" type="password"
                autocomplete="new-password" :error="errors.confirmation" :disabled="pending" @blur="validate('confirmation')" />
              <div v-if="mode === 'login'" class="form-meta"><RouterLink to="/forgot-password">忘记密码？</RouterLink></div>
              <button class="button full-width" type="submit"
                :disabled="pending || (needsToken && !token) || (invitationRequired && !invitationToken)">
                {{ pending ? '正在处理…' : info.action }}<ArrowRight v-if="!pending" :size="18" aria-hidden="true" />
              </button>
              <p v-if="mode === 'login'" class="field-hint">本次登录最长保留 {{ Math.round(policy.session_hours / 24 * 10) / 10 }} 天。公共设备使用后请退出。</p>
            </form>
            <p v-if="mode === 'login' && policy?.registration_mode === 'open'" class="auth-switch">还没有账号？<RouterLink to="/register">创建账号</RouterLink></p>
            <p v-else-if="mode === 'login'" class="auth-switch">当前仅接受邀请注册</p>
            <p v-else-if="mode === 'register'" class="auth-switch">已有账号？<RouterLink to="/login">登录</RouterLink></p>
            <RouterLink v-if="['verify-email', 'login'].includes(mode)" to="/resend-verification" class="secondary-link">重新发送验证邮件</RouterLink>
            <RouterLink v-if="mode === 'reset-password'" to="/forgot-password" class="secondary-link">重新获取重设链接</RouterLink>
            <RouterLink v-if="mode === 'verify-email'" to="/forgot-password" class="secondary-link">忘记注册密码？</RouterLink>
          </template>
        </div>
      </section>
    </main>
    <footer class="public-footer"><span>iTime · 从容安排每一天</span><RouterLink to="/help">账号帮助</RouterLink></footer>
  </div>
</template>
