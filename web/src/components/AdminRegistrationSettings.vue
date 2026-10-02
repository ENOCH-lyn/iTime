<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { Ban, Copy, Link2, RefreshCw } from 'lucide-vue-next'
import FormField from './FormField.vue'
import StatusMessage from './StatusMessage.vue'
import { api, failure, localFailure, type Failure } from '../lib/api'
import { refreshAdminSession } from '../lib/admin'
import {
  localDate,
  type Invitation,
  type InvitationStatus,
  type Page,
  type RegistrationMode,
  type RegistrationPolicy,
} from '../lib/admin-management'

const policy = ref<RegistrationPolicy | null>(null)
const selectedMode = ref<RegistrationMode>('open')
const policyLoading = ref(false)
const policyPending = ref(false)
const policyError = ref<Failure | null>(null)
const policySuccess = ref('')

const email = ref('')
const hours = ref('168')
const emailError = ref('')
const hoursError = ref('')
const invitePending = ref(false)
const inviteError = ref<Failure | null>(null)
const inviteSuccess = ref('')
const inviteLink = ref('')
const invitations = ref<Invitation[]>([])
const invitationLoading = ref(false)
const invitationLoaded = ref(false)
const invitationStatus = ref<'all' | InvitationStatus>('all')
const cursor = ref<Page<Invitation>['next_cursor']>(null)
const selectedInvitation = ref<Invitation | null>(null)
const dialog = ref<HTMLDialogElement | null>(null)

const policyChanged = computed(() => policy.value?.mode !== selectedMode.value)
const statusLabels: Record<InvitationStatus, string> = {
  pending: '待使用',
  consumed: '已使用',
  expired: '已过期',
  revoked: '已撤销',
}

async function handleAdminError(reason: unknown) {
  const result = failure(reason)
  if (['admin_unauthenticated', 'invalid_csrf'].includes(result.code)) {
    await refreshAdminSession()
  }
  await nextTick()
  document.querySelector<HTMLElement>('.error-status')?.focus()
  return result
}

async function loadPolicy(keepError = false) {
  if (policyLoading.value) return
  policyLoading.value = true
  if (!keepError) policyError.value = null
  try {
    policy.value = await api<RegistrationPolicy>('/admin/registration-policy')
    selectedMode.value = policy.value.mode
  } catch (reason) {
    policyError.value = await handleAdminError(reason)
  } finally {
    policyLoading.value = false
  }
}

async function savePolicy() {
  if (!policy.value || !policyChanged.value || policyPending.value) return
  policyPending.value = true
  policyError.value = null
  policySuccess.value = ''
  try {
    policy.value = await api<RegistrationPolicy>(
      '/admin/registration-policy',
      { mode: selectedMode.value, expected_version: policy.value.version },
      'PUT',
    )
    policySuccess.value = policy.value.mode === 'open'
      ? '开放注册已生效。'
      : '邀请注册已生效；已有账号仍可登录和找回。'
  } catch (reason) {
    policyError.value = await handleAdminError(reason)
    if (policyError.value.code === 'registration_policy_version_conflict') {
      await loadPolicy(true)
    }
  } finally {
    policyPending.value = false
  }
}

function validateInvitation() {
  emailError.value = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value.trim())
    ? '' : '请输入完整的邮箱地址。'
  const duration = Number(hours.value)
  hoursError.value = Number.isInteger(duration) && duration >= 1 && duration <= 720
    ? '' : '请输入 1—720 之间的整数小时。'
  return !emailError.value && !hoursError.value
}

async function createInvitation() {
  if (invitePending.value) return
  if (!validateInvitation()) {
    await nextTick()
    document.getElementById(emailError.value ? 'invitation-email' : 'invitation-hours')?.focus()
    return
  }
  invitePending.value = true
  inviteError.value = null
  inviteSuccess.value = ''
  try {
    const invitation = await api<Invitation>(
      '/admin/invitations',
      { email: email.value.trim(), expires_in_hours: Number(hours.value) },
    )
    inviteLink.value = invitation.invite_url ?? ''
    invitations.value = [invitation, ...invitations.value]
    email.value = ''
    inviteSuccess.value = '邀请已创建。链接关闭后无法再次查看。'
  } catch (reason) {
    inviteError.value = await handleAdminError(reason)
  } finally {
    invitePending.value = false
  }
}

async function copyInvitation() {
  try {
    await navigator.clipboard.writeText(inviteLink.value)
    inviteSuccess.value = '邀请链接已复制。'
  } catch {
    inviteError.value = localFailure('浏览器未允许复制，请选中链接后手动复制。')
  }
}

async function loadInvitations(more = false) {
  if (invitationLoading.value) return
  invitationLoading.value = true
  inviteError.value = null
  try {
    const params = new URLSearchParams({ status: invitationStatus.value })
    if (more && cursor.value) {
      params.set('before_time', cursor.value.before_time)
      params.set('before_id', cursor.value.before_id)
    }
    const page = await api<Page<Invitation>>(`/admin/invitations?${params}`)
    invitations.value = more ? [...invitations.value, ...page.items] : page.items
    cursor.value = page.next_cursor
    invitationLoaded.value = true
  } catch (reason) {
    inviteError.value = await handleAdminError(reason)
  } finally {
    invitationLoading.value = false
  }
}

function confirmRevoke(invitation: Invitation) {
  selectedInvitation.value = invitation
  dialog.value?.showModal()
}

async function revokeInvitation() {
  if (!selectedInvitation.value || invitePending.value) return
  invitePending.value = true
  inviteError.value = null
  try {
    const updated = await api<Invitation>(
      `/admin/invitations/${selectedInvitation.value.id}`,
      undefined,
      'DELETE',
    )
    invitations.value = invitations.value.map(item => item.id === updated.id ? updated : item)
    inviteSuccess.value = '邀请已撤销，原链接不能再用于注册。'
  } catch (reason) {
    inviteError.value = await handleAdminError(reason)
  } finally {
    invitePending.value = false
    dialog.value?.close()
    selectedInvitation.value = null
  }
}

onMounted(() => {
  void loadPolicy()
  void loadInvitations()
})
</script>

<template>
  <section class="management-section" aria-labelledby="registration-mode-title">
    <div class="section-intro">
      <div>
        <h2 id="registration-mode-title">注册方式</h2>
        <p class="page-description">切换只影响新账号，已有账号的登录与找回不受影响。</p>
      </div>
      <button class="button secondary" :disabled="policyLoading || policyPending" @click="loadPolicy()">
        <RefreshCw :size="16" aria-hidden="true" />刷新
      </button>
    </div>
    <StatusMessage :error="policyError" :success="policySuccess" />
    <p v-if="policyLoading && !policy" role="status" class="muted">正在读取注册策略…</p>
    <template v-else-if="policy">
      <fieldset class="segmented-field policy-control" :disabled="policyPending">
        <legend>允许的新注册</legend>
        <label :class="{ selected: selectedMode === 'open' }">
          <input v-model="selectedMode" type="radio" value="open" />开放注册
        </label>
        <label :class="{ selected: selectedMode === 'invite' }">
          <input v-model="selectedMode" type="radio" value="invite" />仅限邀请
        </label>
      </fieldset>
      <div class="inline-actions">
        <button class="button" :disabled="!policyChanged || policyPending" @click="savePolicy">
          {{ policyPending ? '正在保存…' : '保存注册方式' }}
        </button>
        <span class="field-hint">最近更新 {{ localDate(policy.updated_at) }}</span>
      </div>
    </template>
  </section>

  <section class="management-section" aria-labelledby="new-invitation-title">
    <h2 id="new-invitation-title">创建邀请</h2>
    <p class="page-description">邀请与邮箱绑定。有效期由本次创建明确指定，链接只显示一次。</p>
    <StatusMessage :error="inviteError" :success="inviteSuccess" />
    <form class="inline-form" novalidate :aria-busy="invitePending" @submit.prevent="createInvitation">
      <FormField id="invitation-email" v-model="email" label="受邀邮箱" type="email"
        autocomplete="off" :maxlength="254" :error="emailError" :disabled="invitePending"
        @blur="validateInvitation" />
      <FormField id="invitation-hours" v-model="hours" label="有效小时数" type="number"
        inputmode="numeric" :min="1" :max="720" :step="1" hint="可填写 1—720 小时。"
        :error="hoursError" :disabled="invitePending" @blur="validateInvitation" />
      <button class="button invite-submit" type="submit" :disabled="invitePending">
        <Link2 :size="18" aria-hidden="true" />{{ invitePending ? '正在创建…' : '创建邀请' }}
      </button>
    </form>
    <div v-if="inviteLink" class="one-time-result">
      <div>
        <strong>一次性邀请链接</strong>
        <p>现在复制并交给对应邮箱的使用者。</p>
      </div>
      <label class="sr-only" for="invitation-link">邀请链接</label>
      <input id="invitation-link" :value="inviteLink" readonly class="readonly-input" />
      <div class="inline-actions">
        <button class="button secondary" @click="copyInvitation">
          <Copy :size="17" aria-hidden="true" />复制链接
        </button>
        <button class="text-button" @click="inviteLink = ''">关闭链接</button>
      </div>
    </div>
  </section>

  <section class="management-section" aria-labelledby="invitation-list-title">
    <div class="section-intro">
      <div>
        <h2 id="invitation-list-title">邀请记录</h2>
        <p class="page-description">历史记录不再显示原始链接。</p>
      </div>
      <div class="list-tools">
        <label for="invitation-status">状态</label>
        <select id="invitation-status" v-model="invitationStatus" :disabled="invitationLoading"
          @change="loadInvitations()">
          <option value="all">全部</option>
          <option value="pending">待使用</option>
          <option value="consumed">已使用</option>
          <option value="expired">已过期</option>
          <option value="revoked">已撤销</option>
        </select>
        <button class="icon-button" title="刷新邀请记录" aria-label="刷新邀请记录"
          :disabled="invitationLoading" @click="loadInvitations()">
          <RefreshCw :size="18" aria-hidden="true" />
        </button>
      </div>
    </div>
    <ul class="management-list" aria-label="邀请记录">
      <li v-for="invitation in invitations" :key="invitation.id">
        <div class="management-primary">
          <strong class="wrap-anywhere">{{ invitation.email }}</strong>
          <span class="state-label" :data-state="invitation.status">{{ statusLabels[invitation.status] }}</span>
          <p>创建于 {{ localDate(invitation.created_at) }} · 到期于 {{ localDate(invitation.expires_at) }}</p>
        </div>
        <button v-if="invitation.status === 'pending'" class="button secondary compact-button"
          :disabled="invitePending" @click="confirmRevoke(invitation)">
          <Ban :size="16" aria-hidden="true" />撤销
        </button>
      </li>
    </ul>
    <p v-if="invitationLoading" role="status" class="muted">正在读取邀请…</p>
    <p v-else-if="invitationLoaded && !invitations.length" class="empty-state">没有符合条件的邀请。</p>
    <button v-if="cursor" class="button secondary" :disabled="invitationLoading" @click="loadInvitations(true)">加载更多</button>
  </section>

  <dialog ref="dialog" aria-labelledby="revoke-invitation-title"
    aria-describedby="revoke-invitation-description" @cancel="invitePending && $event.preventDefault()">
    <h2 id="revoke-invitation-title">撤销这份邀请？</h2>
    <p id="revoke-invitation-description" class="wrap-anywhere">
      {{ selectedInvitation?.email }} 将不能再使用原链接注册。
    </p>
    <div class="dialog-actions">
      <button class="button secondary" :disabled="invitePending" autofocus @click="dialog?.close()">取消</button>
      <button class="button" :disabled="invitePending" @click="revokeInvitation">
        {{ invitePending ? '正在撤销…' : '确认撤销' }}
      </button>
    </div>
  </dialog>
</template>
