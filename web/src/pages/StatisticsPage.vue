<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RefreshCcw } from 'lucide-vue-next'
import { useRoute, useRouter } from 'vue-router'
import StatisticsNavigation from '../components/StatisticsNavigation.vue'
import StatisticsOverview from '../components/StatisticsOverview.vue'
import StatisticsTrend from '../components/StatisticsTrend.vue'
import StatusMessage from '../components/StatusMessage.vue'
import AppShell from '../shell/AppShell.vue'
import { useAppShell } from '../shell/useAppShell'
import { failure, type Failure } from '../lib/api'
import { identity, refreshSession } from '../lib/auth'
import { getTimeSettings, saveTimeSettings } from '../lib/calendar'
import {
  getStatistics,
  type StatisticsResult,
  type StatisticsScope,
} from '../lib/statistics'
import { useSyncRefresh } from '../lib/use-sync-refresh'
import '../statistics.css'

const route = useRoute()
const router = useRouter()
const scope = ref<StatisticsScope>('week')
const anchorDate = ref('')
const initialActivityDate = ref('')
const result = ref<StatisticsResult | null>(null)
const loading = ref(true)
const { pendingLogout, logoutError, logout } = useAppShell()
const pageError = ref<Failure | null>(null)
let ready = false
let requestSequence = 0

const rangeLabel = computed(() => {
  if (!result.value) return anchorDate.value || '正在读取'
  if (result.value.scope === 'day') {
    return formatDate(result.value.period_start_date, {
      year: 'numeric',
      month: 'long',
      day: 'numeric',
      weekday: 'short',
    })
  }
  if (result.value.scope === 'month') {
    return formatDate(result.value.period_start_date, {
      year: 'numeric',
      month: 'long',
    })
  }
  return `${formatDate(result.value.period_start_date, {
    month: 'short',
    day: 'numeric',
  })} - ${formatDate(result.value.period_end_date, {
    month: 'short',
    day: 'numeric',
  })}`
})

const pageTitle = computed(() => ({
  day: '日统计',
  week: '周统计',
  month: '月统计',
})[scope.value])

const hasData = computed(() => {
  if (!result.value) return false
  const { planning, tasks, focus } = result.value.summary
  return planning.block_count > 0
    || tasks.completed_count > 0
    || tasks.skipped_occurrence_count > 0
    || focus.session_count > 0
})

const canMoveNext = computed(() => {
  if (!result.value) return false
  return nextRangeAnchor(result.value) <= result.value.current_activity_date
})

function validDate(value: unknown): value is string {
  return typeof value === 'string'
    && /^\d{4}-\d{2}-\d{2}$/.test(value)
    && !Number.isNaN(Date.parse(`${value}T00:00:00Z`))
}

function parseDate(value: string) {
  return new Date(`${value}T00:00:00Z`)
}

function dateString(value: Date) {
  return value.toISOString().slice(0, 10)
}

function addDays(value: string, days: number) {
  const parsed = parseDate(value)
  parsed.setUTCDate(parsed.getUTCDate() + days)
  return dateString(parsed)
}

function addMonths(value: string, months: number) {
  const parsed = parseDate(value)
  parsed.setUTCDate(1)
  parsed.setUTCMonth(parsed.getUTCMonth() + months)
  return dateString(parsed)
}

function formatDate(
  value: string,
  options: Intl.DateTimeFormatOptions,
) {
  return new Intl.DateTimeFormat('zh-CN', {
    ...options,
    timeZone: 'UTC',
  }).format(parseDate(value))
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
  return dateString(localDate)
}

function nextRangeAnchor(value: StatisticsResult) {
  if (value.scope === 'day') return addDays(value.period_start_date, 1)
  if (value.scope === 'week') return addDays(value.period_start_date, 7)
  return addMonths(value.period_start_date, 1)
}

function previousRangeAnchor(value: StatisticsResult) {
  if (value.scope === 'day') return addDays(value.period_start_date, -1)
  if (value.scope === 'week') return addDays(value.period_start_date, -7)
  return addMonths(value.period_start_date, -1)
}

function calculatedLabel(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function setError(reason: unknown) {
  pageError.value = failure(reason)
  void nextTick(() => {
    document.querySelector<HTMLElement>('.statistics-status .error-status')?.focus()
  })
}

async function loadStatistics() {
  const sequence = ++requestSequence
  loading.value = true
  pageError.value = null
  try {
    const loaded = await getStatistics(scope.value, anchorDate.value)
    if (sequence !== requestSequence) return
    result.value = loaded
    initialActivityDate.value = loaded.current_activity_date
  } catch (reason) {
    if (sequence !== requestSequence) return
    setError(reason)
  } finally {
    if (sequence === requestSequence) loading.value = false
  }
}

async function synchronizeRoute() {
  const requestedScope = route.query.scope
  const normalizedScope: StatisticsScope = (
    requestedScope === 'day'
    || requestedScope === 'week'
    || requestedScope === 'month'
  ) ? requestedScope : 'week'
  const requestedDate = route.query.date
  const normalizedDate = validDate(requestedDate)
    ? requestedDate
    : initialActivityDate.value
  if (
    route.query.scope !== normalizedScope
    || route.query.date !== normalizedDate
  ) {
    await router.replace({
      path: '/stats',
      query: { scope: normalizedScope, date: normalizedDate },
    })
    return
  }
  scope.value = normalizedScope
  anchorDate.value = normalizedDate
  await loadStatistics()
}

async function selectScope(value: StatisticsScope) {
  await router.push({
    path: '/stats',
    query: { scope: value, date: anchorDate.value },
  })
}

async function movePrevious() {
  if (!result.value) return
  await router.push({
    path: '/stats',
    query: {
      scope: scope.value,
      date: previousRangeAnchor(result.value),
    },
  })
}

async function moveNext() {
  if (!result.value || !canMoveNext.value) return
  await router.push({
    path: '/stats',
    query: {
      scope: scope.value,
      date: nextRangeAnchor(result.value),
    },
  })
}

async function moveCurrent() {
  await router.push({
    path: '/stats',
    query: {
      scope: scope.value,
      date: result.value?.current_activity_date ?? initialActivityDate.value,
    },
  })
}

watch(
  () => [route.query.scope, route.query.date],
  () => {
    if (ready) void synchronizeRoute()
  },
)

onMounted(async () => {
  document.title = '统计 · iTime'
  await refreshSession()
  if (!identity.value) {
    await router.replace('/login')
    return
  }
  try {
    let settings = await getTimeSettings()
    if (!settings.initialized) {
      const zone = Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
      settings = await saveTimeSettings(settings, zone)
    }
    initialActivityDate.value = currentActivityDate(settings.time_zone!)
    ready = true
    await synchronizeRoute()
  } catch (reason) {
    loading.value = false
    setError(reason)
  }
})

useSyncRefresh(
  ['task', 'task_occurrence', 'plan', 'focus_session'],
  async () => {
    if (ready) await loadStatistics()
  },
)
</script>

<template>
  <AppShell
    current="stats"
    has-secondary
    secondary-label="统计"
    title="统计"
    :pending-logout="pendingLogout"
    @logout="logout"
  >
    <template #secondary>
      <StatisticsNavigation
        :scope="scope"
        :range-label="rangeLabel"
        :can-move-next="canMoveNext"
        :loading="loading"
        @scope="selectScope"
        @previous="movePrevious"
        @current="moveCurrent"
        @next="moveNext"
      />
    </template>
    <template #drawer-secondary>
      <StatisticsNavigation
        :scope="scope"
        :range-label="rangeLabel"
        :can-move-next="canMoveNext"
        :loading="loading"
        @scope="selectScope"
        @previous="movePrevious"
        @current="moveCurrent"
        @next="moveNext"
      />
    </template>

    <div class="statistics-main">
      <header class="statistics-page-heading">
        <div>
          <span class="statistics-eyebrow">个人统计</span>
          <h1 tabindex="-1">{{ pageTitle }}</h1>
          <p>{{ rangeLabel }}</p>
        </div>
        <button
          type="button"
          class="icon-button"
          title="刷新统计"
          aria-label="刷新统计"
          :disabled="loading"
          @click="loadStatistics"
        >
          <RefreshCcw :size="19" aria-hidden="true" />
        </button>
      </header>

      <div class="statistics-status">
        <StatusMessage :error="logoutError" />
        <StatusMessage
          :error="pageError"
          retry-label="重新读取"
          @retry="loadStatistics"
        />
        <p v-if="pageError && result" class="statistics-stale-note" role="status">
          仍显示 {{ calculatedLabel(result.calculated_at) }} 计算的结果。
        </p>
      </div>

      <div v-if="loading && !result" class="statistics-loading" role="status">
        <span />
        <span />
        <span />
        <p>正在汇总当前事实...</p>
      </div>

      <div v-else-if="result" class="statistics-content">
        <div class="statistics-context">
          <span>{{ result.time_zone }}</span>
          <span>活动日于当地 04:00 切换</span>
          <span v-if="result.data_end_date !== result.period_end_date">
            当前范围统计至 {{ result.data_end_date }}
          </span>
        </div>

        <StatisticsOverview :summary="result.summary" />

        <section v-if="!hasData" class="statistics-empty">
          <h2>这个范围还没有记录</h2>
          <p>计划、任务结果和专注事实都会在保存后显示在这里。</p>
          <div>
            <RouterLink to="/tasks" class="button secondary">查看任务</RouterLink>
            <RouterLink to="/plans" class="button secondary">打开规划</RouterLink>
            <RouterLink to="/focus" class="button secondary">开始专注</RouterLink>
          </div>
        </section>

        <StatisticsTrend :days="result.days" :scope="result.scope" />

        <details class="statistics-methodology">
          <summary>查看统计口径</summary>
          <div>
            <p>日、周、月按当前时区的 04:00 活动日汇总；当前周和当前月只统计到当前活动日。</p>
            <p>计划只读取当前计划版本，不把旧版本或待确认提案重复计入，也不推断计划块已经完成。</p>
            <p>任务只读取当前仍成立的完成或跳过结果；重新打开后会从完成数中移除。</p>
            <p>专注使用最新有效时长，完成、放弃、补记、修订和进行中分别标记。</p>
            <p v-if="result.summary.focus.proportional_focus_count">
              本范围有 {{ result.summary.focus.proportional_focus_count }} 条补记或修订记录跨活动日，按有效区间比例分配时长。
            </p>
            <p>零值只表示没有对应结构化记录，不表示没有行动。</p>
          </div>
        </details>

        <footer class="statistics-calculated">
          计算于 {{ calculatedLabel(result.calculated_at) }}
        </footer>
      </div>
    </div>
  </AppShell>
</template>
