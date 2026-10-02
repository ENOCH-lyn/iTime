<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { RefreshCw, Search, ShieldAlert, UserCheck } from 'lucide-vue-next'
import FormField from './FormField.vue'
import StatusMessage from './StatusMessage.vue'
import { api, failure, type Failure } from '../lib/api'
import { refreshAdminSession } from '../lib/admin'
import {
  localDate,
  type AIAccess,
  type AccountStatus,
  type ManagedUser,
  type Page,
} from '../lib/admin-management'

type ManagedAction = {
  user: ManagedUser
  kind: 'status' | 'ai'
  next: AccountStatus | AIAccess
}

const users = ref<ManagedUser[]>([])
const cursor = ref<Page<ManagedUser>['next_cursor']>(null)
const search = ref('')
const status = ref<'all' | AccountStatus>('all')
const aiAccess = ref<'all' | AIAccess>('all')
const role = ref<'all' | 'user' | 'admin'>('all')
const loading = ref(false)
const loaded = ref(false)
const pendingUserId = ref('')
const error = ref<Failure | null>(null)
const success = ref('')
const selected = ref<ManagedAction | null>(null)
const dialog = ref<HTMLDialogElement | null>(null)

async function handleAdminError(reason: unknown) {
  const result = failure(reason)
  if (['admin_unauthenticated', 'invalid_csrf'].includes(result.code)) {
    await refreshAdminSession()
  }
  await nextTick()
  document.querySelector<HTMLElement>('.error-status')?.focus()
  return result
}

async function load(more = false, keepError = false) {
  if (loading.value) return
  loading.value = true
  if (!keepError) error.value = null
  try {
    const params = new URLSearchParams({
      status: status.value,
      ai_access: aiAccess.value,
      role: role.value,
    })
    if (search.value.trim()) params.set('search', search.value.trim())
    if (more && cursor.value) {
      params.set('before_time', cursor.value.before_time)
      params.set('before_id', cursor.value.before_id)
    }
    const page = await api<Page<ManagedUser>>(`/admin/users?${params}`)
    users.value = more ? [...users.value, ...page.items] : page.items
    cursor.value = page.next_cursor
    loaded.value = true
  } catch (reason) {
    error.value = await handleAdminError(reason)
  } finally {
    loading.value = false
  }
}

function choose(user: ManagedUser, kind: ManagedAction['kind']) {
  selected.value = {
    user,
    kind,
    next: kind === 'status'
      ? (user.status === 'active' ? 'suspended' : 'active')
      : (user.ai_access === 'allowed' ? 'frozen' : 'allowed'),
  }
  dialog.value?.showModal()
}

async function applyAction() {
  if (!selected.value || pendingUserId.value) return
  const action = selected.value
  pendingUserId.value = action.user.id
  error.value = null
  success.value = ''
  try {
    const updated = action.kind === 'status'
      ? await api<ManagedUser>(
        `/admin/users/${action.user.id}/status`,
        { status: action.next, expected_version: action.user.version },
        'PUT',
      )
      : await api<ManagedUser>(
        `/admin/users/${action.user.id}/ai-access`,
        { ai_access: action.next, expected_version: action.user.version },
        'PUT',
      )
    users.value = users.value.map(user => user.id === updated.id ? updated : user)
    success.value = action.kind === 'status'
      ? (updated.status === 'active'
          ? '账号已恢复；用户需要重新登录。'
          : '账号已停用，现有登录和未使用邮件链接已撤销。')
      : (updated.ai_access === 'allowed'
          ? 'AI 访问已恢复。'
          : 'AI 访问已冻结，手动功能和普通登录保持可用。')
  } catch (reason) {
    error.value = await handleAdminError(reason)
    if (error.value.code === 'user_version_conflict') await load(false, true)
  } finally {
    pendingUserId.value = ''
    dialog.value?.close()
    selected.value = null
  }
}

function actionTitle() {
  if (!selected.value) return ''
  if (selected.value.kind === 'status') {
    return selected.value.next === 'suspended' ? '停用这个账号？' : '恢复这个账号？'
  }
  return selected.value.next === 'frozen' ? '冻结这个账号的 AI？' : '恢复这个账号的 AI？'
}

function actionDescription() {
  if (!selected.value) return ''
  if (selected.value.kind === 'status') {
    return selected.value.next === 'suspended'
      ? '现有登录和未使用邮件链接会立即失效。已保存的数据不会删除。'
      : '账号将重新允许登录，但先前撤销的设备仍需重新认证。'
  }
  return selected.value.next === 'frozen'
    ? '用户仍可登录和使用手动功能，新的 AI 请求会被拒绝。'
    : '用户可以重新发起新的 AI 请求。'
}

onMounted(() => load())
</script>

<template>
  <div class="section-intro">
    <p class="page-description">账号停用与 AI 冻结分别管理，修改按最新版本提交。</p>
    <button class="button secondary" :disabled="loading || !!pendingUserId" @click="load()">
      <RefreshCw :size="16" aria-hidden="true" />刷新列表
    </button>
  </div>
  <StatusMessage :error="error" :success="success" />
  <form class="management-filters" role="search" @submit.prevent="load()">
    <FormField id="user-search" v-model="search" label="搜索邮箱" type="text"
      autocomplete="off" :maxlength="254" :disabled="loading" />
    <div class="field">
      <label for="user-status">账号状态</label>
      <select id="user-status" v-model="status" :disabled="loading">
        <option value="all">全部</option>
        <option value="active">启用</option>
        <option value="suspended">已停用</option>
      </select>
    </div>
    <div class="field">
      <label for="user-ai-access">AI 状态</label>
      <select id="user-ai-access" v-model="aiAccess" :disabled="loading">
        <option value="all">全部</option>
        <option value="allowed">可用</option>
        <option value="frozen">已冻结</option>
      </select>
    </div>
    <div class="field">
      <label for="user-role">角色</label>
      <select id="user-role" v-model="role" :disabled="loading">
        <option value="all">全部</option>
        <option value="user">普通用户</option>
        <option value="admin">管理员</option>
      </select>
    </div>
    <button class="button filter-submit" type="submit" :disabled="loading">
      <Search :size="17" aria-hidden="true" />筛选
    </button>
  </form>

  <ul class="management-list user-management-list" aria-label="用户列表">
    <li v-for="user in users" :key="user.id">
      <div class="management-primary">
        <div class="identity-line">
          <strong class="wrap-anywhere">{{ user.email }}</strong>
          <span v-if="user.is_admin" class="state-label" data-state="admin">管理员</span>
          <span class="state-label" :data-state="user.status">{{ user.status === 'active' ? '启用' : '已停用' }}</span>
          <span class="state-label" :data-state="user.ai_access">{{ user.ai_access === 'allowed' ? 'AI 可用' : 'AI 已冻结' }}</span>
        </div>
        <p>{{ user.email_verified ? '邮箱已验证' : '等待邮箱验证' }} · 注册于 {{ localDate(user.created_at) }}</p>
      </div>
      <div v-if="!user.is_admin" class="row-actions">
        <button class="button secondary compact-button" :disabled="!!pendingUserId"
          @click="choose(user, 'ai')">
          <ShieldAlert :size="16" aria-hidden="true" />
          {{ user.ai_access === 'allowed' ? '冻结 AI' : '恢复 AI' }}
        </button>
        <button class="button secondary compact-button" :disabled="!!pendingUserId"
          @click="choose(user, 'status')">
          <UserCheck :size="16" aria-hidden="true" />
          {{ user.status === 'active' ? '停用账号' : '恢复账号' }}
        </button>
      </div>
      <p v-else class="field-hint protected-admin">通过服务器交接流程管理</p>
    </li>
  </ul>
  <p v-if="loading" role="status" class="muted">正在读取用户…</p>
  <p v-else-if="loaded && !users.length" class="empty-state">没有符合筛选条件的用户。</p>
  <button v-if="cursor" class="button secondary" :disabled="loading" @click="load(true)">加载更多</button>

  <dialog ref="dialog" aria-labelledby="manage-user-title" aria-describedby="manage-user-description"
    @cancel="pendingUserId && $event.preventDefault()">
    <h2 id="manage-user-title">{{ actionTitle() }}</h2>
    <p v-if="selected" class="dialog-target wrap-anywhere">{{ selected.user.email }}</p>
    <p id="manage-user-description">{{ actionDescription() }}</p>
    <div class="dialog-actions">
      <button class="button secondary" :disabled="!!pendingUserId" autofocus @click="dialog?.close()">取消</button>
      <button class="button" :disabled="!!pendingUserId" @click="applyAction">
        {{ pendingUserId ? '正在保存…' : '确认修改' }}
      </button>
    </div>
  </dialog>
</template>
