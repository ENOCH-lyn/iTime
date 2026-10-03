<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import {
  BadgeDollarSign,
  Check,
  Coins,
  History,
  RefreshCw,
  Search,
  UserRound,
} from 'lucide-vue-next'
import FormField from './FormField.vue'
import StatusMessage from './StatusMessage.vue'
import { ApiError, failure, localFailure, type Failure } from '../lib/api'
import { refreshAdminSession } from '../lib/admin'
import {
  localDate,
  type ManagedUser,
  type Page,
} from '../lib/admin-management'
import {
  createQuotaAdjustment,
  createQuotaPolicy,
  getAdminUserQuota,
  listAdminQuotaLedger,
  listQuotaPolicies,
  publishQuotaPolicy,
  searchQuotaUsers,
  type AdminQuotaLedgerEntry,
  type AdminQuotaUserSummary,
  type QuotaPolicy,
} from '../lib/admin-quota'
import type { QuotaLedgerKind } from '../lib/quota'

type Tab = 'policies' | 'adjustments' | 'ledger'
type AdjustmentMode = 'credit' | 'debit'

const tab = ref<Tab>('policies')
const policies = ref<QuotaPolicy[]>([])
const policyCursor = ref<Page<QuotaPolicy>['next_cursor']>(null)
const policyLoading = ref(false)
const policyPending = ref(false)
const policyError = ref<Failure | null>(null)
const policySuccess = ref('')
const policyRetry = ref<'load' | 'create' | 'publish'>('load')
const policyCurrency = ref('CNY')
const policyAmount = ref('')
const policyAmountError = ref('')
const selectedPolicy = ref<QuotaPolicy | null>(null)
const policyDialog = ref<HTMLDialogElement | null>(null)
let createPolicyOperation: { id: string; fingerprint: string } | null = null
let publishPolicyOperation: { id: string; fingerprint: string } | null = null

const userSearch = ref('')
const userResults = ref<ManagedUser[]>([])
const userCursor = ref<Page<ManagedUser>['next_cursor']>(null)
const appliedUserSearch = ref('')
const userSearchLoading = ref(false)
const userSearchLoaded = ref(false)
const userSearchError = ref<Failure | null>(null)
const selectedUser = ref<ManagedUser | null>(null)
const userQuota = ref<AdminQuotaUserSummary | null>(null)
const userQuotaLoading = ref(false)
const adjustmentPending = ref(false)
const adjustmentError = ref<Failure | null>(null)
const adjustmentSuccess = ref('')
const adjustmentMode = ref<AdjustmentMode>('credit')
const adjustmentAmount = ref('')
const adjustmentReason = ref('')
const adjustmentAmountError = ref('')
const adjustmentReasonError = ref('')
const adjustmentDialog = ref<HTMLDialogElement | null>(null)
let adjustmentOperation: { id: string; fingerprint: string } | null = null

const ledger = ref<AdminQuotaLedgerEntry[]>([])
const ledgerCursor = ref<Page<AdminQuotaLedgerEntry>['next_cursor']>(null)
const ledgerLoading = ref(false)
const ledgerLoaded = ref(false)
const ledgerError = ref<Failure | null>(null)
const ledgerUserEmail = ref('')
const ledgerActivityDate = ref('')
const ledgerKind = ref<'all' | QuotaLedgerKind>('all')
const ledgerCurrency = ref('')
const appliedLedgerFingerprint = ref('')
const appliedLedgerFilters = ref<{
  userId?: string
  activityDate?: string
  kind?: QuotaLedgerKind
  currency?: string
}>({})

const statusLabels = {
  draft: '草稿',
  published: '当前发布',
  retired: '历史版本',
} as const
const kindLabels: Record<QuotaLedgerKind, string> = {
  usage_charge: 'AI 使用',
  admin_credit: '管理员增加',
  admin_debit: '管理员扣减',
}
const usageLabels = {
  provider: '供应商上报',
  estimated: '平台估算',
} as const
const capabilityLabels = {
  conversation: '全局对话',
  calendar_screenshot: '截图识别',
} as const
const tabOrder: Tab[] = ['policies', 'adjustments', 'ledger']
const tabIds: Record<Tab, string> = {
  policies: 'quota-policy-tab',
  adjustments: 'quota-adjustment-tab',
  ledger: 'quota-ledger-tab',
}

const currentPolicy = computed(
  () => policies.value.find(policy => policy.current) ?? null,
)
const adjustmentPreview = computed(() => {
  const daily = userQuota.value?.current
  const amount = parseNanos(adjustmentAmount.value)
  if (!daily || amount === null || amount <= 0n) return null
  const before = parseNanos(daily.available)
  if (before === null) return null
  const after = adjustmentMode.value === 'credit'
    ? before + amount
    : before - amount
  return {
    currency: daily.currency,
    before: formatNanos(before),
    change: `${adjustmentMode.value === 'credit' ? '+' : '-'}${formatNanos(amount)}`,
    after: formatNanos(after),
    valid: after >= 0n,
  }
})
const adjustmentReady = computed(() => (
  adjustmentPreview.value?.valid === true
  && adjustmentReason.value.trim().length >= 1
  && adjustmentReason.value.trim().length <= 500
))
const draftLedgerFingerprint = computed(() => JSON.stringify({
  email: ledgerUserEmail.value.trim().toLowerCase(),
  activityDate: ledgerActivityDate.value,
  kind: ledgerKind.value,
  currency: ledgerCurrency.value.trim().toUpperCase(),
}))

function parseNanos(value: string) {
  const normalized = value.trim()
  if (!/^\d+(?:\.\d{1,9})?$/.test(normalized)) return null
  const [whole, fraction = ''] = normalized.split('.')
  return BigInt(whole) * 1_000_000_000n
    + BigInt(fraction.padEnd(9, '0'))
}

function formatNanos(value: bigint) {
  const negative = value < 0n
  const absolute = negative ? -value : value
  const whole = absolute / 1_000_000_000n
  const fraction = (absolute % 1_000_000_000n)
    .toString()
    .padStart(9, '0')
    .replace(/0+$/, '')
  return `${negative ? '-' : ''}${whole}${fraction ? `.${fraction}` : ''}`
}

function operation(
  current: { id: string; fingerprint: string } | null,
  fingerprint: string,
) {
  return current?.fingerprint === fingerprint
    ? current
    : { id: crypto.randomUUID(), fingerprint }
}

async function handleAdminError(
  reason: unknown,
  selector = '.admin-quota-management .error-status',
) {
  const current = failure(reason)
  if (['admin_unauthenticated', 'invalid_csrf'].includes(current.code)) {
    await refreshAdminSession()
  }
  await nextTick()
  document.querySelector<HTMLElement>(selector)?.focus()
  return current
}

function selectTab(next: Tab) {
  tab.value = next
  if (next === 'policies' && !policies.value.length) void loadPolicies()
  if (next === 'ledger' && !ledgerLoaded.value) void loadLedger()
}

async function onTabKeydown(event: KeyboardEvent, currentTab: Tab) {
  const currentIndex = tabOrder.indexOf(currentTab)
  let nextIndex = currentIndex
  if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % tabOrder.length
  else if (event.key === 'ArrowLeft') {
    nextIndex = (currentIndex - 1 + tabOrder.length) % tabOrder.length
  } else if (event.key === 'Home') nextIndex = 0
  else if (event.key === 'End') nextIndex = tabOrder.length - 1
  else return
  event.preventDefault()
  const nextTab = tabOrder[nextIndex]
  selectTab(nextTab)
  await nextTick()
  document.getElementById(tabIds[nextTab])?.focus()
}

async function loadPolicies(more = false, keepStatus = false) {
  if (policyLoading.value) return
  policyLoading.value = true
  if (!keepStatus) {
    policyError.value = null
    policySuccess.value = ''
  }
  try {
    const result = await listQuotaPolicies(more ? policyCursor.value : null)
    policies.value = more
      ? [...policies.value, ...result.items]
      : result.items
    policyCursor.value = result.next_cursor
    const currencySource = currentPolicy.value ?? policies.value.at(-1)
    if (currencySource) {
      policyCurrency.value = currencySource.currency
    }
  } catch (reason) {
    policyRetry.value = 'load'
    policyError.value = await handleAdminError(reason)
  } finally {
    policyLoading.value = false
  }
}

function validatePolicy() {
  const amount = parseNanos(policyAmount.value)
  policyAmountError.value = amount === null
    ? '请输入最多 9 位小数的非负金额。'
    : ''
  return !policyAmountError.value
}

async function savePolicy() {
  if (!validatePolicy() || policyPending.value) return
  const fingerprint = JSON.stringify({
    currency: policyCurrency.value.trim().toUpperCase(),
    dailyAllowance: policyAmount.value.trim(),
  })
  createPolicyOperation = operation(createPolicyOperation, fingerprint)
  policyPending.value = true
  policyError.value = null
  policySuccess.value = ''
  try {
    const created = await createQuotaPolicy({
      operation_id: createPolicyOperation.id,
      currency: policyCurrency.value.trim(),
      daily_allowance: policyAmount.value.trim(),
    })
    policies.value = [created, ...policies.value]
    policyAmount.value = ''
    createPolicyOperation = null
    policySuccess.value = `额度策略版本 ${created.version} 已保存为草稿。`
  } catch (reason) {
    policyRetry.value = 'create'
    policyError.value = await handleAdminError(reason)
  } finally {
    policyPending.value = false
  }
}

function confirmPublish(policy: QuotaPolicy) {
  selectedPolicy.value = policy
  publishPolicyOperation = operation(
    publishPolicyOperation,
    policy.id,
  )
  const dialog = policyDialog.value
    ?? document.getElementById('quota-policy-confirmation') as HTMLDialogElement | null
  dialog?.showModal()
}

async function publishSelectedPolicy() {
  if (!selectedPolicy.value || !publishPolicyOperation || policyPending.value) {
    return
  }
  policyPending.value = true
  policyError.value = null
  policySuccess.value = ''
  try {
    const published = await publishQuotaPolicy(
      selectedPolicy.value.id,
      publishPolicyOperation.id,
    )
    publishPolicyOperation = null
    selectedPolicy.value = null
    policyDialog.value?.close()
    await loadPolicies(false, true)
    policySuccess.value = `额度策略版本 ${published.version} 已发布，只影响新活动日。`
  } catch (reason) {
    policyDialog.value?.close()
    policyRetry.value = 'publish'
    policyError.value = await handleAdminError(reason)
  } finally {
    policyPending.value = false
  }
}

async function findUsers(more = false) {
  if (userSearchLoading.value) return
  const search = more ? appliedUserSearch.value : userSearch.value.trim()
  if (!search) {
    userSearchError.value = localFailure('请输入用户邮箱关键词。')
    return
  }
  userSearchLoading.value = true
  userSearchError.value = null
  try {
    const result = await searchQuotaUsers(
      search,
      more ? userCursor.value : null,
    )
    userResults.value = more
      ? [...userResults.value, ...result.items]
      : result.items
    userCursor.value = result.next_cursor
    if (!more) appliedUserSearch.value = search
    userSearchLoaded.value = true
  } catch (reason) {
    userSearchError.value = await handleAdminError(reason)
  } finally {
    userSearchLoading.value = false
  }
}

async function chooseUser(user: ManagedUser) {
  selectedUser.value = user
  userQuota.value = null
  adjustmentOperation = null
  adjustmentSuccess.value = ''
  await loadUserQuota()
}

function retryPolicyAction() {
  if (policyRetry.value === 'create') void savePolicy()
  else if (policyRetry.value === 'publish') void publishSelectedPolicy()
  else void loadPolicies()
}

async function loadUserQuota(keepError = false) {
  if (!selectedUser.value || userQuotaLoading.value) return
  userQuotaLoading.value = true
  if (!keepError) adjustmentError.value = null
  try {
    userQuota.value = await getAdminUserQuota(selectedUser.value.id)
  } catch (reason) {
    adjustmentError.value = await handleAdminError(reason)
  } finally {
    userQuotaLoading.value = false
  }
}

function validateAdjustment() {
  const amount = parseNanos(adjustmentAmount.value)
  adjustmentAmountError.value = amount === null || amount <= 0n
    ? '请输入最多 9 位小数且大于零的金额。'
    : ''
  adjustmentReasonError.value = adjustmentReason.value.trim()
    ? ''
    : '请输入调整原因。'
  if (adjustmentReason.value.trim().length > 500) {
    adjustmentReasonError.value = '调整原因不能超过 500 个字符。'
  }
  if (adjustmentPreview.value && !adjustmentPreview.value.valid) {
    adjustmentAmountError.value = '扣减不能超过当前可用额度。'
  }
  return !adjustmentAmountError.value && !adjustmentReasonError.value
}

function showAdjustmentDialog() {
  const dialog = document.getElementById(
    'quota-adjustment-confirmation',
  ) as HTMLDialogElement | null
  dialog?.showModal()
}

async function reconcileAdjustment(operationId: string) {
  if (!selectedUser.value) return false
  try {
    const result = await listAdminQuotaLedger({
      userId: selectedUser.value.id,
      operationId,
    })
    return result.items.some(
      item => item.adjustment?.operation_id === operationId,
    )
  } catch {
    return false
  }
}

async function submitAdjustment() {
  const daily = userQuota.value?.current
  if (
    !selectedUser.value
    || !daily
    || !validateAdjustment()
    || adjustmentPending.value
  ) {
    return
  }
  const fingerprint = JSON.stringify({
    userId: selectedUser.value.id,
    version: daily.version,
    activityDate: daily.activity_date,
    mode: adjustmentMode.value,
    amount: adjustmentAmount.value.trim(),
    reason: adjustmentReason.value.trim(),
  })
  adjustmentOperation = operation(adjustmentOperation, fingerprint)
  adjustmentPending.value = true
  adjustmentError.value = null
  adjustmentSuccess.value = ''
  try {
    await createQuotaAdjustment(selectedUser.value.id, {
      operation_id: adjustmentOperation.id,
      expected_daily_version: daily.version,
      activity_date: daily.activity_date,
      mode: adjustmentMode.value,
      amount: adjustmentAmount.value.trim(),
      reason: adjustmentReason.value.trim(),
    })
    adjustmentSuccess.value = '额度调整已保存并写入账本。'
    adjustmentOperation = null
    adjustmentAmount.value = ''
    adjustmentReason.value = ''
    adjustmentDialog.value?.close()
    await loadUserQuota(true)
    if (ledgerLoaded.value) await loadLedger()
  } catch (reason) {
    adjustmentDialog.value?.close()
    const currentError = await handleAdminError(reason)
    adjustmentError.value = currentError
    if (
      currentError.saved === 'unknown'
      && adjustmentOperation
      && await reconcileAdjustment(adjustmentOperation.id)
    ) {
      adjustmentError.value = null
      adjustmentSuccess.value = '已从账本确认额度调整保存成功。'
      adjustmentOperation = null
      adjustmentAmount.value = ''
      adjustmentReason.value = ''
      adjustmentDialog.value?.close()
      await loadUserQuota(true)
      if (ledgerLoaded.value) await loadLedger()
    } else if (
      currentError.code === 'ai_quota_daily_version_conflict'
      || currentError.code === 'ai_quota_adjustment_insufficient'
    ) {
      adjustmentOperation = null
      await loadUserQuota(true)
    }
  } finally {
    adjustmentPending.value = false
  }
}

async function resolveLedgerUser(email: string) {
  const normalized = email.trim().toLowerCase()
  if (!normalized) return undefined
  const known = userResults.value.find(
    user => user.email.toLowerCase() === normalized,
  )
  if (known) return known.id
  let cursor: Page<ManagedUser>['next_cursor'] = null
  do {
    const result = await searchQuotaUsers(normalized, cursor)
    const exact = result.items.find(
      user => user.email.toLowerCase() === normalized,
    )
    if (exact) return exact.id
    cursor = result.next_cursor
  } while (cursor)
  throw new ApiError(localFailure('未找到此用户邮箱。'))
}

async function loadLedger(more = false) {
  if (ledgerLoading.value) return
  ledgerLoading.value = true
  ledgerError.value = null
  try {
    let filters: {
      userId?: string
      activityDate?: string
      kind?: QuotaLedgerKind
      currency?: string
    }
    if (more) {
      filters = appliedLedgerFilters.value
    } else {
      filters = {
        userId: await resolveLedgerUser(ledgerUserEmail.value),
        activityDate: ledgerActivityDate.value || undefined,
        kind: ledgerKind.value === 'all' ? undefined : ledgerKind.value,
        currency: ledgerCurrency.value.trim().toUpperCase() || undefined,
      }
    }
    const page = await listAdminQuotaLedger({
      ...filters,
      cursor: more ? ledgerCursor.value : null,
    })
    ledger.value = more ? [...ledger.value, ...page.items] : page.items
    ledgerCursor.value = page.next_cursor
    if (!more) {
      appliedLedgerFilters.value = filters
      appliedLedgerFingerprint.value = draftLedgerFingerprint.value
    }
    ledgerLoaded.value = true
  } catch (reason) {
    ledgerError.value = await handleAdminError(reason)
  } finally {
    ledgerLoading.value = false
  }
}

function filterLedgerForSelectedUser() {
  if (!selectedUser.value) return
  ledgerUserEmail.value = selectedUser.value.email
  selectTab('ledger')
  void loadLedger()
}

onMounted(() => {
  void loadPolicies()
  void loadLedger()
})
</script>

<template>
  <div class="admin-quota-management">
    <div class="admin-quota-tabs" role="tablist" aria-label="额度管理视图">
      <button
        id="quota-policy-tab"
        type="button"
        role="tab"
        :aria-selected="tab === 'policies'"
        :tabindex="tab === 'policies' ? 0 : -1"
        aria-controls="quota-policy-panel"
        @click="selectTab('policies')"
        @keydown="onTabKeydown($event, 'policies')"
      >
        <Coins :size="17" aria-hidden="true" />策略
      </button>
      <button
        id="quota-adjustment-tab"
        type="button"
        role="tab"
        :aria-selected="tab === 'adjustments'"
        :tabindex="tab === 'adjustments' ? 0 : -1"
        aria-controls="quota-adjustment-panel"
        @click="selectTab('adjustments')"
        @keydown="onTabKeydown($event, 'adjustments')"
      >
        <UserRound :size="17" aria-hidden="true" />用户调整
      </button>
      <button
        id="quota-ledger-tab"
        type="button"
        role="tab"
        :aria-selected="tab === 'ledger'"
        :tabindex="tab === 'ledger' ? 0 : -1"
        aria-controls="quota-ledger-panel"
        @click="selectTab('ledger')"
        @keydown="onTabKeydown($event, 'ledger')"
      >
        <History :size="17" aria-hidden="true" />账本
      </button>
    </div>

    <div
      v-if="tab === 'policies'"
      id="quota-policy-panel"
      role="tabpanel"
      aria-labelledby="quota-policy-tab"
    >
      <section class="management-section" aria-labelledby="quota-policy-create-title">
        <div class="section-intro">
          <div>
            <h2 id="quota-policy-create-title">每日额度策略</h2>
            <p class="page-description">
              发布后只影响首次创建的新活动日，既有活动日不会改写。
            </p>
          </div>
          <button
            type="button"
            class="button secondary compact-button"
            :disabled="policyLoading || policyPending"
            @click="loadPolicies()"
          >
            <RefreshCw :size="16" aria-hidden="true" />刷新
          </button>
        </div>
        <StatusMessage
          :error="policyError"
          :success="policySuccess"
          :retry-label="policyError?.retryable ? '重试原操作' : undefined"
          @retry="retryPolicyAction"
        />
        <form class="quota-policy-form" novalidate @submit.prevent="savePolicy">
          <FormField
            id="quota-policy-currency"
            v-model="policyCurrency"
            label="币种代码"
            type="text"
            autocomplete="off"
            :maxlength="3"
            :disabled="policyPending || !!policies.length"
            hint="首个策略确定币种，后续版本不能直接更换。"
          />
          <FormField
            id="quota-policy-amount"
            v-model="policyAmount"
            label="每日基础额度"
            type="text"
            inputmode="decimal"
            autocomplete="off"
            :maxlength="32"
            :error="policyAmountError"
            :disabled="policyPending"
            @blur="validatePolicy"
          />
          <button class="button" type="submit" :disabled="policyPending">
            <BadgeDollarSign :size="18" aria-hidden="true" />
            {{ policyPending ? '正在保存…' : '保存新版本' }}
          </button>
        </form>
      </section>

      <section class="management-section" aria-labelledby="quota-policy-history-title">
        <h2 id="quota-policy-history-title">策略历史</h2>
        <p class="page-description">每个版本保留原币种和金额，发布或回滚不会改写旧活动日。</p>
        <p v-if="policyLoading && !policies.length" role="status" class="muted">
          正在读取额度策略…
        </p>
        <ul v-else class="management-list quota-policy-list" aria-label="额度策略历史">
          <li v-for="policy in policies" :key="policy.id">
            <div class="management-primary">
              <div class="identity-line">
                <strong>版本 {{ policy.version }}</strong>
                <span class="state-label" :data-state="policy.status">
                  {{ statusLabels[policy.status] }}
                </span>
              </div>
              <p class="quota-policy-amount">
                {{ policy.currency }} {{ policy.daily_allowance }} / 活动日
              </p>
              <p>创建于 {{ localDate(policy.created_at) }}</p>
            </div>
            <button
              v-if="!policy.current"
              type="button"
              class="button secondary compact-button"
              :disabled="policyPending"
              @click="confirmPublish(policy)"
            >
              <Check :size="16" aria-hidden="true" />
              {{ policy.status === 'retired' ? '回滚' : '发布' }}
            </button>
          </li>
        </ul>
        <button
          v-if="policyCursor"
          type="button"
          class="button secondary"
          :disabled="policyLoading"
          @click="loadPolicies(true)"
        >
          加载更多策略
        </button>
      </section>
    </div>

    <div
      v-else-if="tab === 'adjustments'"
      id="quota-adjustment-panel"
      role="tabpanel"
      aria-labelledby="quota-adjustment-tab"
    >
      <section class="management-section" aria-labelledby="quota-user-search-title">
        <h2 id="quota-user-search-title">选择用户</h2>
        <p class="page-description">按邮箱找到普通用户，再读取其当前活动日额度。</p>
        <StatusMessage :error="userSearchError" />
        <form class="quota-user-search" role="search" @submit.prevent="findUsers()">
          <FormField
            id="quota-user-search"
            v-model="userSearch"
            label="用户邮箱"
            type="email"
            autocomplete="off"
            :maxlength="254"
            :disabled="userSearchLoading"
          />
          <button class="button" type="submit" :disabled="userSearchLoading">
            <Search :size="17" aria-hidden="true" />
            {{ userSearchLoading ? '正在搜索…' : '搜索' }}
          </button>
        </form>
        <ul
          v-if="userResults.length"
          class="management-list quota-user-results"
          aria-label="额度用户搜索结果"
        >
          <li v-for="user in userResults" :key="user.id">
            <div class="management-primary">
              <strong class="wrap-anywhere">{{ user.email }}</strong>
              <p>{{ user.status === 'active' ? '账号启用' : '账号停用' }} ·
                {{ user.ai_access === 'allowed' ? 'AI 可用' : 'AI 已冻结' }}</p>
            </div>
            <button
              type="button"
              class="button secondary compact-button"
              :disabled="userQuotaLoading"
              @click="chooseUser(user)"
            >
              选择
            </button>
          </li>
        </ul>
        <p
          v-else-if="userSearchLoaded && !userSearchLoading"
          class="empty-state"
        >
          没有符合条件的普通用户。
        </p>
        <button
          v-if="userCursor && userSearch.trim() === appliedUserSearch"
          type="button"
          class="button secondary"
          :disabled="userSearchLoading"
          @click="findUsers(true)"
        >
          加载更多用户
        </button>
      </section>

      <section
        v-if="selectedUser"
        class="management-section"
        aria-labelledby="quota-adjustment-title"
      >
        <div class="section-intro">
          <div>
            <h2 id="quota-adjustment-title">调整活动日额度</h2>
            <p class="page-description wrap-anywhere">{{ selectedUser.email }}</p>
          </div>
          <button
            type="button"
            class="button secondary compact-button"
            :disabled="userQuotaLoading || adjustmentPending"
            @click="loadUserQuota()"
          >
            <RefreshCw :size="16" aria-hidden="true" />刷新
          </button>
        </div>
        <StatusMessage
          :error="adjustmentError"
          :success="adjustmentSuccess"
          :retry-label="adjustmentError?.saved === 'unknown' ? '重试原操作' : undefined"
          @retry="submitAdjustment"
        />
        <p v-if="userQuotaLoading && !userQuota" role="status" class="muted">
          正在读取用户额度…
        </p>
        <template v-else-if="userQuota?.current">
          <dl class="quota-metrics admin-quota-metrics" aria-label="用户活动日额度">
            <div><dt>当前可用</dt><dd data-testid="admin-quota-available"><span>{{ userQuota.current.currency }}</span>{{ userQuota.current.available }}</dd></div>
            <div><dt>已扣</dt><dd><span>{{ userQuota.current.currency }}</span>{{ userQuota.current.charged }}</dd></div>
            <div><dt>处理中</dt><dd><span>{{ userQuota.current.currency }}</span>{{ userQuota.current.reserved }}</dd></div>
            <div><dt>每日基础</dt><dd data-testid="admin-quota-base"><span>{{ userQuota.current.currency }}</span>{{ userQuota.current.base }}</dd></div>
          </dl>
          <p class="field-hint quota-user-day">
            活动日 {{ userQuota.current.activity_date }} · {{ userQuota.current.time_zone }}
            · 版本 {{ userQuota.current.version }}
          </p>
          <form class="quota-adjustment-form" novalidate @submit.prevent>
            <fieldset class="segmented-field quota-adjustment-mode" :disabled="adjustmentPending">
              <legend>调整方向</legend>
              <label :class="{ selected: adjustmentMode === 'credit' }">
                <input v-model="adjustmentMode" type="radio" value="credit" />增加
              </label>
              <label :class="{ selected: adjustmentMode === 'debit' }">
                <input v-model="adjustmentMode" type="radio" value="debit" />扣减
              </label>
            </fieldset>
            <FormField
              id="quota-adjustment-amount"
              v-model="adjustmentAmount"
              label="调整金额"
              type="text"
              inputmode="decimal"
              autocomplete="off"
              :maxlength="32"
              :hint="`币种：${userQuota.current.currency}`"
              :error="adjustmentAmountError"
              :disabled="adjustmentPending"
              @blur="validateAdjustment"
            />
            <div class="field">
              <label for="quota-adjustment-reason">调整原因</label>
              <textarea
                id="quota-adjustment-reason"
                v-model="adjustmentReason"
                rows="3"
                maxlength="500"
                :disabled="adjustmentPending"
                :aria-invalid="!!adjustmentReasonError"
                aria-describedby="quota-adjustment-reason-feedback"
                @blur="validateAdjustment"
              ></textarea>
              <p
                id="quota-adjustment-reason-feedback"
                :class="adjustmentReasonError ? 'field-error' : 'field-hint'"
              >
                {{ adjustmentReasonError || '原因会写入不可变调整记录，最多 500 个字符。' }}
              </p>
            </div>
            <div class="inline-actions">
              <button
                class="button"
                type="button"
                :disabled="adjustmentPending || !adjustmentReady"
                @click="showAdjustmentDialog"
              >
                <Coins :size="18" aria-hidden="true" />核对并调整
              </button>
              <button
                type="button"
                class="text-button"
                @click="filterLedgerForSelectedUser"
              >
                查看此用户账本
              </button>
            </div>
          </form>
        </template>
        <p v-else-if="userQuota && !userQuota.configured" class="empty-state">
          尚未发布可用的每日额度策略。
        </p>
      </section>
    </div>

    <div
      v-else
      id="quota-ledger-panel"
      role="tabpanel"
      aria-labelledby="quota-ledger-tab"
    >
      <section class="management-section" aria-labelledby="admin-quota-ledger-title">
        <div class="section-intro">
          <div>
            <h2 id="admin-quota-ledger-title">用户额度账本</h2>
            <p class="page-description">查看成功扣额和管理员增减；供应商失败尝试仍在成本页核对。</p>
          </div>
          <button
            type="button"
            class="button secondary compact-button"
            :disabled="ledgerLoading"
            @click="loadLedger()"
          >
            <RefreshCw :size="16" aria-hidden="true" />刷新
          </button>
        </div>
        <StatusMessage
          :error="ledgerError"
          :retry-label="ledgerError?.retryable ? '重新读取' : undefined"
          @retry="loadLedger()"
        />
        <form class="management-filters quota-ledger-filters" @submit.prevent="loadLedger()">
          <FormField
            id="admin-quota-ledger-user"
            v-model="ledgerUserEmail"
            label="用户邮箱"
            type="email"
            autocomplete="off"
            :maxlength="254"
            :disabled="ledgerLoading"
          />
          <FormField
            id="admin-quota-ledger-date"
            v-model="ledgerActivityDate"
            label="活动日"
            type="date"
            autocomplete="off"
            :disabled="ledgerLoading"
          />
          <div class="field">
            <label for="admin-quota-ledger-kind">类型</label>
            <select id="admin-quota-ledger-kind" v-model="ledgerKind" :disabled="ledgerLoading">
              <option value="all">全部</option>
              <option value="usage_charge">AI 使用</option>
              <option value="admin_credit">管理员增加</option>
              <option value="admin_debit">管理员扣减</option>
            </select>
          </div>
          <FormField
            id="admin-quota-ledger-currency"
            v-model="ledgerCurrency"
            label="币种"
            type="text"
            autocomplete="off"
            :maxlength="3"
            :disabled="ledgerLoading"
          />
          <button class="button filter-submit" type="submit" :disabled="ledgerLoading">
            <Search :size="17" aria-hidden="true" />筛选
          </button>
        </form>
        <ul class="management-list quota-admin-ledger-list" aria-label="用户额度账本">
          <li v-for="entry in ledger" :key="entry.id">
            <div class="management-primary">
              <div class="identity-line">
                <strong class="wrap-anywhere">{{ entry.user.email }}</strong>
                <span class="state-label">{{ kindLabels[entry.kind] }}</span>
                <span
                  v-if="entry.usage_source"
                  class="state-label"
                  :data-state="entry.usage_source"
                >
                  {{ usageLabels[entry.usage_source] }}
                </span>
              </div>
              <p>
                {{ entry.activity_date }} · {{ localDate(entry.created_at) }}
                <template v-if="entry.job">
                  · {{ capabilityLabels[entry.job.capability] }}
                </template>
              </p>
              <p v-if="entry.adjustment" class="wrap-anywhere">
                {{ entry.adjustment.reason }} · 操作 {{ entry.adjustment.operation_id }}
                · 请求 {{ entry.adjustment.request_id }}
              </p>
              <p v-else-if="entry.job" class="wrap-anywhere">作业 {{ entry.job.id }}</p>
            </div>
            <strong class="quota-list-amount">
              {{ entry.currency }} {{ entry.delta }}
            </strong>
          </li>
        </ul>
        <p v-if="ledgerLoading && !ledgerLoaded" role="status" class="muted">
          正在读取额度账本…
        </p>
        <p v-else-if="ledgerLoaded && !ledger.length" class="empty-state">
          没有符合条件的额度记录。
        </p>
        <button
          v-if="ledgerCursor && draftLedgerFingerprint === appliedLedgerFingerprint"
          type="button"
          class="button secondary"
          :disabled="ledgerLoading"
          @click="loadLedger(true)"
        >
          加载更多
        </button>
      </section>
    </div>

    <dialog
      id="quota-policy-confirmation"
      ref="policyDialog"
      aria-labelledby="quota-policy-dialog-title"
      aria-describedby="quota-policy-dialog-description"
      @cancel="policyPending && $event.preventDefault()"
    >
      <h2 id="quota-policy-dialog-title">
        {{ selectedPolicy?.status === 'retired' ? '回滚到此额度策略？' : '发布此额度策略？' }}
      </h2>
      <p id="quota-policy-dialog-description">
        版本 {{ selectedPolicy?.version }} · {{ selectedPolicy?.currency }}
        {{ selectedPolicy?.daily_allowance }} / 活动日。只影响之后首次创建的活动日。
      </p>
      <div class="dialog-actions">
        <button
          type="button"
          class="button secondary"
          :disabled="policyPending"
          autofocus
          @click="policyDialog?.close()"
        >
          取消
        </button>
        <button type="button" class="button" :disabled="policyPending" @click="publishSelectedPolicy">
          {{ policyPending ? '正在发布…' : '确认发布' }}
        </button>
      </div>
    </dialog>

    <dialog
      id="quota-adjustment-confirmation"
      ref="adjustmentDialog"
      aria-labelledby="quota-adjustment-dialog-title"
      aria-describedby="quota-adjustment-dialog-description"
      @cancel="adjustmentPending && $event.preventDefault()"
    >
      <h2 id="quota-adjustment-dialog-title">确认额度调整</h2>
      <p id="quota-adjustment-dialog-description" class="wrap-anywhere">
        {{ selectedUser?.email }} · {{ adjustmentReason.trim() }}
      </p>
      <dl v-if="adjustmentPreview" class="quota-adjustment-preview">
        <div><dt>调整前可用</dt><dd>{{ adjustmentPreview.currency }} {{ adjustmentPreview.before }}</dd></div>
        <div><dt>本次变化</dt><dd>{{ adjustmentPreview.currency }} {{ adjustmentPreview.change }}</dd></div>
        <div><dt>调整后可用</dt><dd>{{ adjustmentPreview.currency }} {{ adjustmentPreview.after }}</dd></div>
      </dl>
      <div class="dialog-actions">
        <button
          type="button"
          class="button secondary"
          :disabled="adjustmentPending"
          autofocus
          @click="adjustmentDialog?.close()"
        >
          返回修改
        </button>
        <button type="button" class="button" :disabled="adjustmentPending" @click="submitAdjustment">
          {{ adjustmentPending ? '正在保存…' : '确认调整' }}
        </button>
      </div>
    </dialog>
  </div>
</template>
