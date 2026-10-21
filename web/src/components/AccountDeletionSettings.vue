<script setup lang="ts">
import { nextTick, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Archive, ShieldAlert, Trash2 } from 'lucide-vue-next'
import FormField from './FormField.vue'
import StatusMessage from './StatusMessage.vue'
import { requestAccountDeletion } from '../lib/account-deletion'
import { failure, type Failure } from '../lib/api'
import {
  clearSession,
  identity,
  notifyAccountDiscard,
  pendingLocalOperationCount,
} from '../lib/auth'

const router = useRouter()
const root = ref<HTMLElement | null>(null)
const dialog = ref<HTMLDialogElement | null>(null)
const values = reactive({
  email: '',
  password: '',
})
const errors = reactive({
  email: '',
  password: '',
})
const pending = ref(false)
const error = ref<Failure | null>(null)
const operationId = ref('')
const pendingCount = ref(0)

function openDialog() {
  values.email = ''
  values.password = ''
  errors.email = ''
  errors.password = ''
  error.value = null
  operationId.value = ''
  pendingCount.value = pendingLocalOperationCount()
  dialog.value?.showModal()
  void nextTick(() => document.getElementById('deletion-email')?.focus())
}

function closeDialog() {
  if (pending.value) return
  dialog.value?.close()
}

function resetDialog() {
  if (pending.value) return
  values.password = ''
  errors.email = ''
  errors.password = ''
  error.value = null
}

function validate() {
  const expected = identity.value?.user.email ?? ''
  errors.email = values.email.trim().toLowerCase() === expected
    ? ''
    : '邮箱与当前账号不一致。'
  const length = Array.from(values.password).length
  errors.password = length >= 12 && length <= 128
    ? ''
    : '请输入当前密码。'
  return !errors.email && !errors.password
}

async function focusError() {
  await nextTick()
  root.value?.querySelector<HTMLElement>('.error-status')?.focus()
}

async function submit() {
  if (pending.value || !identity.value || !validate()) {
    await nextTick()
    if (errors.email) document.getElementById('deletion-email')?.focus()
    else if (errors.password) document.getElementById('deletion-password')?.focus()
    return
  }
  const count = pendingLocalOperationCount()
  pendingCount.value = count
  if (
    count > 0
    && !window.confirm(
      `本设备还有 ${count} 项操作未同步。申请注销后这些操作不会保存，确定继续吗？`,
    )
  ) return

  pending.value = true
  error.value = null
  if (!operationId.value) operationId.value = crypto.randomUUID()
  const userId = identity.value.user.id
  try {
    const result = await requestAccountDeletion({
      operation_id: operationId.value,
      email_confirmation: values.email,
      current_password: values.password,
    })
    values.password = ''
    operationId.value = ''
    dialog.value?.close()
    await router.replace({
      path: '/login',
      state: {
        deletionRequested: true,
        purgeAfter: result.deletion.purge_after,
      },
    })
    clearSession({ discardLocal: true })
    notifyAccountDiscard(userId)
  } catch (reason) {
    const current = failure(reason)
    error.value = current.saved === 'unknown'
      ? {
          ...current,
          message: (
            '请返回登录并使用正确密码核对账号状态，不要重复创建新的申请。'
          ),
        }
      : current
    if (current.code === 'account_confirmation_mismatch') {
      errors.email = '邮箱与当前账号不一致。'
      await nextTick()
      document.getElementById('deletion-email')?.focus()
    } else {
      await focusError()
    }
  } finally {
    pending.value = false
  }
}
</script>

<template>
  <section
    ref="root"
    class="account-deletion-settings"
    aria-labelledby="account-deletion-title"
  >
    <div class="account-deletion-heading">
      <ShieldAlert :size="22" aria-hidden="true" />
      <div>
        <h2 id="account-deletion-title">注销账号</h2>
        <p>
          申请后会立即退出所有设备并停止临时工作。你可以在 7 天内使用邮箱和密码撤销；
          到期后个人数据将被清理且无法恢复。
        </p>
      </div>
    </div>
    <div class="account-deletion-actions">
      <a href="#data-export-settings" class="button secondary">
        <Archive :size="18" aria-hidden="true" />
        先生成数据包
      </a>
      <button type="button" class="button danger" @click="openDialog">
        <Trash2 :size="18" aria-hidden="true" />
        注销账号
      </button>
    </div>

    <dialog
      ref="dialog"
      class="account-deletion-dialog"
      aria-labelledby="account-deletion-dialog-title"
      aria-describedby="account-deletion-dialog-description"
      @cancel="pending && $event.preventDefault()"
      @close="resetDialog"
    >
      <div class="dialog-heading">
        <div>
          <span class="muted">不可立即撤回的高风险操作</span>
          <h2 id="account-deletion-dialog-title">注销账号？</h2>
        </div>
        <Trash2 :size="22" aria-hidden="true" />
      </div>
      <p id="account-deletion-dialog-description">
        提交后当前设备和其他设备都会退出，AI、导出、订阅刷新与开放专注将停止。
        已保存的数据保留到 7 天撤销期结束。
      </p>
      <p v-if="pendingCount" class="pending-operations-warning">
        当前设备有 {{ pendingCount }} 项操作尚未同步，继续后这些操作不会保存。
      </p>
      <StatusMessage :error="error" />
      <form novalidate :aria-busy="pending" @submit.prevent="submit">
        <FormField
          id="deletion-email"
          v-model="values.email"
          label="完整注册邮箱"
          type="email"
          autocomplete="username"
          :maxlength="254"
          :error="errors.email"
          :disabled="pending"
          @blur="validate"
        />
        <FormField
          id="deletion-password"
          v-model="values.password"
          label="当前密码"
          type="password"
          autocomplete="current-password"
          :maxlength="128"
          :error="errors.password"
          :disabled="pending"
          @blur="validate"
        />
        <div class="dialog-actions">
          <button
            type="button"
            class="button secondary"
            :disabled="pending"
            @click="closeDialog"
          >
            取消
          </button>
          <button type="submit" class="button danger" :disabled="pending">
            <Trash2 v-if="!pending" :size="18" aria-hidden="true" />
            {{ pending ? '正在提交…' : '确认申请注销' }}
          </button>
        </div>
      </form>
    </dialog>
  </section>
</template>
