<script setup lang="ts">
import { nextTick, onMounted, reactive, ref } from 'vue'
import FormField from './FormField.vue'
import StatusMessage from './StatusMessage.vue'
import { api, failure, passwordError, type Failure, type Policy } from '../lib/api'
import { acceptSession, notifySessionChange, refreshSession, type Session } from '../lib/auth'

const policy = ref<Policy | null>(null)
const values = reactive({ current: '', password: '', confirmation: '', revoke: true })
const errors = reactive<Record<string, string>>({})
const error = ref<Failure | null>(null)
const success = ref('')
const pending = ref(false)
async function load() {
  error.value = null
  try { policy.value = await api<Policy>('/auth/policy') }
  catch (err) { error.value = failure(err) }
}
onMounted(load)
function validate(name: 'current' | 'password' | 'confirmation') {
  if (!policy.value) return
  errors[name] = name === 'confirmation'
    ? (values.confirmation === values.password && values.confirmation ? '' : '两次输入的密码不一致。')
    : passwordError(values[name], policy.value)
}
async function submit() {
  if (pending.value || !policy.value) return
  error.value = null; success.value = ''
  for (const name of ['current', 'password', 'confirmation'] as const) validate(name)
  const first = Object.keys(errors).find(key => errors[key])
  if (first) { await nextTick(); document.getElementById(first)?.focus(); return }
  pending.value = true
  try {
    acceptSession(await api<Session>('/auth/change-password', {
      current_password: values.current, new_password: values.password, revoke_other_sessions: values.revoke,
    }))
    notifySessionChange()
    success.value = values.revoke ? '密码已更新，其他设备已退出。当前浏览器可继续使用。' : '密码已更新，其他设备的登录已保留。'
    values.current = ''; values.password = ''; values.confirmation = ''
  } catch (err) {
    error.value = failure(err)
    if (['unauthenticated', 'invalid_csrf', 'account_suspended'].includes(error.value.code)) await refreshSession()
    await nextTick(); document.querySelector<HTMLElement>('.error-status')?.focus()
  } finally { pending.value = false }
}
</script>

<template>
  <p class="page-description">使用一段容易记住、难以猜到的密码。</p>
  <div class="narrow-content">
    <StatusMessage :error="error" :success="success" :retry-label="!policy && error ? '重新连接' : undefined" @retry="load" />
    <form v-if="policy" novalidate :aria-busy="pending" @submit.prevent="submit">
      <FormField id="current" v-model="values.current" label="当前密码" type="password" autocomplete="current-password"
        :error="errors.current" :disabled="pending" @blur="validate('current')" />
      <FormField id="password" v-model="values.password" label="新密码" type="password" autocomplete="new-password"
        :hint="`至少 ${policy.password_min_length} 位。`"
        :error="errors.password" :disabled="pending" @blur="validate('password')" />
      <FormField id="confirmation" v-model="values.confirmation" label="确认新密码" type="password" autocomplete="new-password"
        :error="errors.confirmation" :disabled="pending" @blur="validate('confirmation')" />
      <label class="checkbox-label"><input v-model="values.revoke" type="checkbox" :disabled="pending" />同时退出其他登录设备</label>
      <p class="field-hint">当前浏览器会保持登录；旧的找回密码链接将失效。</p>
      <button class="button" :disabled="pending">{{ pending ? '正在更新…' : '更新密码' }}</button>
    </form>
    <p v-else-if="!error" role="status" class="muted">正在读取密码要求…</p>
  </div>
</template>
