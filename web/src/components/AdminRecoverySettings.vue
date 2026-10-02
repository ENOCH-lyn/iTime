<script setup lang="ts">
import { nextTick, ref } from 'vue'
import FormField from './FormField.vue'
import RecoveryCodesPanel from './RecoveryCodesPanel.vue'
import StatusMessage from './StatusMessage.vue'
import { adminIdentity, refreshAdminSession } from '../lib/admin'
import { api, failure, type Failure } from '../lib/api'

const code = ref('')
const confirmed = ref(false)
const fieldError = ref('')
const error = ref<Failure | null>(null)
const success = ref('')
const pending = ref(false)
const recoveryCodes = ref<string[]>([])
async function submit() {
  if (pending.value) return
  error.value = null; success.value = ''
  fieldError.value = /^\d{6}$/.test(code.value)
    ? '' : '请输入身份验证器显示的 6 位数字。'
  if (fieldError.value) {
    await nextTick(); document.getElementById('admin-recovery-totp')?.focus()
    return
  }
  pending.value = true
  try {
    const result = await api<{ recovery_codes: string[] }>(
      '/admin/auth/recovery-codes', { code: code.value },
    )
    recoveryCodes.value = result.recovery_codes
    code.value = ''; confirmed.value = false
  } catch (reason) {
    error.value = failure(reason)
    if (['admin_unauthenticated', 'invalid_csrf'].includes(error.value.code)) {
      await refreshAdminSession()
    }
    await nextTick(); document.querySelector<HTMLElement>('.error-status')?.focus()
  } finally { pending.value = false }
}
function finish() {
  recoveryCodes.value = []
  success.value = '新的恢复码已生效；旧恢复码已全部失效。'
}
</script>

<template>
  <RecoveryCodesPanel v-if="recoveryCodes.length" :codes="recoveryCodes"
    :email="adminIdentity?.admin.email ?? '当前管理员'" action-label="完成" @done="finish" />
  <template v-else>
    <p class="page-description">重新生成后，所有旧恢复码立即失效。</p>
    <div class="narrow-content">
      <StatusMessage :error="error" :success="success" />
      <form novalidate :aria-busy="pending" @submit.prevent="submit">
        <FormField id="admin-recovery-totp" v-model="code" label="当前 6 位验证码"
          inputmode="numeric" :maxlength="6" autocomplete="one-time-code"
          :error="fieldError" :disabled="pending" />
        <label class="checkbox-label">
          <input v-model="confirmed" type="checkbox" :disabled="pending" />
          我知道所有旧恢复码会立即失效
        </label>
        <button class="button" :disabled="pending || !confirmed">
          {{ pending ? '正在生成…' : '生成新的恢复码' }}
        </button>
      </form>
    </div>
  </template>
</template>
