<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { CirclePause, RefreshCcw } from 'lucide-vue-next'
import AccountDeletionSettings from '../components/AccountDeletionSettings.vue'
import AppearanceControls from '../components/AppearanceControls.vue'
import DataExportSettings from '../components/DataExportSettings.vue'
import DeviceSettings from '../components/DeviceSettings.vue'
import PasswordSettings from '../components/PasswordSettings.vue'
import PrivacySettings from '../components/PrivacySettings.vue'
import QuotaOverview from '../components/QuotaOverview.vue'
import StatisticsOverview from '../components/StatisticsOverview.vue'
import StatisticsTrend from '../components/StatisticsTrend.vue'
import StatusMessage from '../components/StatusMessage.vue'
import SyncSettings from '../components/SyncSettings.vue'
import AppShell from '../shell/AppShell.vue'
import { useAppShell } from '../shell/useAppShell'
import { failure, type Failure } from '../lib/api'
import { authError, authLoading, identity, refreshSession } from '../lib/auth'
import { getTimeSettings, saveTimeSettings } from '../lib/calendar'
import {
  getStatistics,
  type StatisticsResult,
  type StatisticsScope,
} from '../lib/statistics'
import { useSyncRefresh } from '../lib/use-sync-refresh'
import '../statistics.css'

type TabKey =
  | 'stats'
  | 'security'
  | 'preferences'
  | 'privacy'
  | 'data'
  | 'account'

const route = useRoute()
const router = useRouter()
const booted = ref(false)
const { pendingLogout, logoutError, logout } = useAppShell()
const activeTab = ref<TabKey>('stats')
const scope = ref<StatisticsScope>('week')
const anchorDate = ref('')
const result = ref<StatisticsResult | null>(null)
const statsLoading = ref(false)
const pageError = ref<Failure | null>(null)
let statsReady = false
let requestSequence = 0

const rangeLabel = computed(() => result.value
  ? `${result.value.period_start_date} ~ ${result.value.period_end_date}`
  : anchorDate.value || '正在读取')

const hasStatsData = computed(() => {
  if (!result.value) return false
  const { planning, tasks, focus } = result.value.summary
  return planning.block_count > 0
    || tasks.completed_count > 0
    || tasks.skipped_occurrence_count > 0
    || focus.session_count > 0
})

function tabFromRoute(value: unknown): TabKey {
  const list: TabKey[] = ['stats', 'security', 'preferences', 'privacy', 'data', 'account']
  return list.includes(value as TabKey) ? value as TabKey : 'stats'
}

function currentActivityDate(timeZone: string) {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(new Date()).map(part => [part.type, part.value]),
  )
  const localDate = new Date(Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
  ))
  if (Number(parts.hour) < 4) localDate.setUTCDate(localDate.getUTCDate() - 1)
  return localDate.toISOString().slice(0, 10)
}

function setError(reason: unknown) {
  pageError.value = failure(reason)
  void nextTick(() => {
    document.querySelector<HTMLElement>('.me-status .error-status')?.focus()
  })
}

async function loadStatistics() {
  if (!statsReady) return
  const sequence = ++requestSequence
  statsLoading.value = true
  pageError.value = null
  try {
    const loaded = await getStatistics(scope.value, anchorDate.value)
    if (sequence !== requestSequence) return
    result.value = loaded
    anchorDate.value = loaded.current_activity_date
  } catch (reason) {
    if (sequence !== requestSequence) return
    setError(reason)
  } finally {
    if (sequence === requestSequence) statsLoading.value = false
  }
}

async function selectTab(value: TabKey) {
  if (activeTab.value === value) return
  activeTab.value = value
  await router.replace({ path: `/me/${value}` })
  if (value === 'stats') await loadStatistics()
}

watch(() => route.params.tab, value => {
  activeTab.value = tabFromRoute(value)
})

watch(authError, value => {
  if (value?.code === 'unauthenticated') void router.replace('/login')
})

onMounted(async () => {
  document.title = '我的 · iTime'
  await refreshSession()
  booted.value = true
  if (!identity.value) {
    await router.replace('/login')
    return
  }
  activeTab.value = tabFromRoute(route.params.tab)
  try {
    let settings = await getTimeSettings()
    if (!settings.initialized) {
      const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
      settings = await saveTimeSettings(settings, zone)
    }
    anchorDate.value = currentActivityDate(settings.time_zone!)
    statsReady = true
    if (activeTab.value === 'stats') await loadStatistics()
  } catch (reason) {
    setError(reason)
  }
})

useSyncRefresh(
  ['task', 'task_occurrence', 'plan', 'focus_session'],
  async () => {
    if (statsReady && activeTab.value === 'stats') await loadStatistics()
  },
)
</script>

<template>
  <AppShell
    current="me"
    title="我的"
    :pending-logout="pendingLogout"
    @logout="logout"
  >
    <div class="me-page">
      <header class="me-page-header">
        <div>
          <span class="statistics-eyebrow">账户与数据</span>
          <h1 tabindex="-1">我的</h1>
          <p v-if="identity">{{ identity.user.email }}</p>
        </div>
        <div
          v-if="identity?.user.ai_access === 'frozen'"
          class="status restriction-status"
          role="status"
        >
          <CirclePause :size="18" aria-hidden="true" />
          <span>此账号的 AI 功能已暂停，手动查看和编辑仍可使用。</span>
        </div>
      </header>

      <div class="me-status">
        <StatusMessage :error="logoutError" />
        <StatusMessage
          :error="authError"
          :retry-label="authError?.retryable ? '重新连接' : undefined"
          @retry="refreshSession"
        />
        <StatusMessage
          :error="pageError"
          retry-label="重新读取"
          @retry="loadStatistics"
        />
      </div>

      <p
        v-if="!booted || (!identity && authLoading)"
        role="status"
        class="muted"
      >
        正在读取账号信息…
      </p>

      <ElTabs
        v-else-if="identity"
        v-model="activeTab"
        class="me-tabs"
        @tab-change="selectTab($event as TabKey)"
      >
        <ElTabPane label="统计" name="stats">
          <section class="me-stats" aria-labelledby="me-stats-title">
            <header class="me-stats-heading">
              <div>
                <h2 id="me-stats-title">
                  {{ { day: '日', week: '周', month: '月' }[scope] }}统计
                </h2>
                <p>{{ rangeLabel }}</p>
              </div>
              <div class="me-stats-tools">
                <ElSelect v-model="scope" size="small" @change="loadStatistics">
                  <ElOption label="日" value="day" />
                  <ElOption label="周" value="week" />
                  <ElOption label="月" value="month" />
                </ElSelect>
                <button
                  type="button"
                  class="icon-button"
                  title="刷新统计"
                  aria-label="刷新统计"
                  :disabled="statsLoading"
                  @click="loadStatistics"
                >
                  <RefreshCcw :size="18" aria-hidden="true" />
                </button>
              </div>
            </header>

            <p
              v-if="statsLoading && !result"
              class="me-stats-loading"
              role="status"
            >
              正在汇总当前事实…
            </p>
            <div v-else-if="result" class="statistics-content">
              <StatisticsOverview :summary="result.summary" />
              <section v-if="!hasStatsData" class="statistics-empty">
                <h3>这个范围还没有记录</h3>
                <p>计划、任务结果和专注事实都会在保存后显示在这里。</p>
              </section>
              <StatisticsTrend :days="result.days" :scope="result.scope" />
            </div>
          </section>
        </ElTabPane>

        <ElTabPane label="账号安全" name="security">
          <div class="me-section">
            <PasswordSettings />
          </div>
          <div class="me-section">
            <DeviceSettings />
          </div>
        </ElTabPane>

        <ElTabPane label="偏好" name="preferences">
          <div class="me-section">
            <AppearanceControls />
          </div>
          <p class="muted">
            时区、周起始日、学期等时间设置请到
            <RouterLink to="/calendar">日历</RouterLink> 调整。
          </p>
        </ElTabPane>

        <ElTabPane label="隐私与额度" name="privacy">
          <div class="me-section">
            <QuotaOverview />
          </div>
          <div class="me-section">
            <PrivacySettings />
          </div>
        </ElTabPane>

        <ElTabPane label="数据与同步" name="data">
          <div class="me-section">
            <SyncSettings />
          </div>
          <div class="me-section">
            <DataExportSettings />
          </div>
        </ElTabPane>

        <ElTabPane label="账号操作" name="account">
          <div class="me-section me-logout">
            <div>
              <strong>退出登录</strong>
              <p>仅退出当前设备。</p>
            </div>
            <ElButton
              :loading="pendingLogout"
              :disabled="pendingLogout"
              @click="logout"
            >
              {{ pendingLogout ? '正在退出…' : '退出登录' }}
            </ElButton>
          </div>
          <div class="me-section">
            <AccountDeletionSettings />
          </div>
        </ElTabPane>
      </ElTabs>
    </div>
  </AppShell>
</template>

<style scoped>
.me-page {
  padding: clamp(16px, 2.4vw, 28px);
  display: flex;
  flex-direction: column;
  gap: 18px;
  overflow: hidden auto;
  min-height: 0;
}

.me-page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
}

.me-page-header h1 {
  margin: 2px 0 6px;
}

.me-page-header p {
  color: var(--muted);
  font-size: .9rem;
}

.me-status {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.me-tabs {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.me-section {
  padding: 12px 0;
  border-top: 1px solid var(--border);
}

.me-section:first-of-type {
  border-top: none;
  padding-top: 4px;
}

.me-logout {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.me-stats-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.me-stats-tools {
  display: flex;
  align-items: center;
  gap: 10px;
}

.me-stats-loading {
  color: var(--muted);
  padding: 20px 0;
}
</style>
