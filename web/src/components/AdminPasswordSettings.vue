<script setup lang="ts">
import { nextTick, onMounted, reactive, ref } from 'vue'
import FormField from './FormField.vue'
import StatusMessage from './StatusMessage.vue'
import { acceptAdminSession, notifyAdminSessionChange, refreshAdminSession, type AdminSession } from '../lib/admin'
import { api, failure, passwordError, type Failure, type Policy } from '../lib/api'

const policy = ref<Policy | null>(null)
const values = reactive({ current: '', password: '', confirmation: '' })
const errors = reactive<Record<string, string>>({})
const error = ref<Failure | null>(null)
const success = ref('')
const pending = ref(false)
onMounted(async () => {
  try { policy.value = await api<Policy>('/auth/policy') }
  catch (reason) { error.value = failure(reason) }
})
function validate(field: 'current' | 'password' | 'confirmation') {
  if (!policy.value) return
  errors[field] = field === 'confirmation'
    ? (values.confirmation === values.password && values.confirmation ? '' : '两次输入的密码不一致。')
    : passwordError(values[field], policy.value)
}
async function submit() {
  if (!policy.value || pending.value) return
  error.value = null; success.value = ''
  for (const field of ['current', 'password', 'confirmation'] as const) validate(field)
  const first = ['current', 'password', 'confirmation'].find(field => errors[field])
  if (first) { await nextTick(); document.getElementById(`admin-${first}`)?.focus(); return }
  pending.value = true
  try {
    const session = await api<AdminSession>('/admin/auth/change-password', {
      current_password: values.current, new_password: values.password,
    })
    acceptAdminSession(session)
    notifyAdminSessionChange()
    success.value = '密码已更新，其他普通和管理会话均已退出。'
    values.current = ''; values.password = ''; values.confirmation = ''
  } catch (reason) {
    error.value = failure(reason)
    if (['admin_unauthenticated', 'invalid_csrf'].includes(error.value.code)) {
      await refreshAdminSession()
    }
    await nextTick(); document.querySelector<HTMLElement>('.error-status')?.focus()
  } finally { pending.value = false }
}
</script>

<template>
  <p class="page-description">修改后，其他所有登录会话会立即退出。</p>
  <div class="narrow-content">
    <StatusMessage :error="error" :success="success" />
    <form v-if="policy" novalidate :aria-busy="pending" @submit.prevent="submit">
      <FormField id="admin-current" v-model="values.current" label="当前密码" type="password"
        autocomplete="current-password" :error="errors.current" :disabled="pending"
        @blur="validate('current')" />
      <FormField id="admin-password" v-model="values.password" label="新密码" type="password"
        autocomplete="new-password" :hint="`${policy.password_min_length}—${policy.password_max_length} 个字符。`"
        :error="errors.password" :disabled="pending" @blur="validate('password')" />
      <FormField id="admin-confirmation" v-model="values.confirmation" label="确认新密码"
        type="password" autocomplete="new-password" :error="errors.confirmation"
        :disabled="pending" @blur="validate('confirmation')" />
      <p class="field-hint">TOTP 和未使用的恢复码保持有效；旧的找回密码链接失效。</p>
      <button class="button" :disabled="pending">{{ pending ? '正在更新…' : '更新管理密码' }}</button>
    </form>
  </div>
</template>
