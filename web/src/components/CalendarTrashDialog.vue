<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import {
  CalendarDays,
  CalendarSync,
  Clock3,
  Link2,
  RefreshCw,
  RotateCcw,
  Trash2,
  X,
} from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import { failure, type Failure } from '../lib/api'
import {
  getSeries,
  listSeries,
  restoreCalendarSeries,
  type CalendarSeries,
} from '../lib/calendar'
import {
  getCalendarSubscription,
  listCalendarSubscriptions,
  restoreCalendarSubscription,
  type CalendarSubscription,
} from '../lib/calendar-subscriptions'

const props = defineProps<{ open: boolean }>()

const emit = defineEmits<{
  close: []
  changed: [message: string]
}>()

type Scope = 'series' | 'subscriptions'
type RetryAction = 'load' | 'restore' | 'reread' | null
type Operation = { id: string; fingerprint: string }

const dialog = ref<HTMLDialogElement | null>(null)
const detailPanel = ref<HTMLElement | null>(null)
const scope = ref<Scope>('series')
const series = ref<CalendarSeries[]>([])
const subscriptions = ref<CalendarSubscription[]>([])
const selectedSeriesId = ref('')
const selectedSubscriptionId = ref('')
const loading = ref(false)
const loaded = ref(false)
const pendingKey = ref('')
const error = ref<Failure | null>(null)
const success = ref('')
const retryAction = ref<RetryAction>(null)
let loadRevision = 0
let restoreOperation: Operation | null = null

const currentSeries = computed(() => (
  series.value.find(item => item.id === selectedSeriesId.value) ?? null
))
const currentSubscription = computed(() => (
  subscriptions.value.find(item => item.id === selectedSubscriptionId.value) ?? null
))
const currentItems = computed(() => (
  scope.value === 'series' ? series.value : subscriptions.value
))
const currentSelection = computed(() => (
  scope.value === 'series' ? currentSeries.value : currentSubscription.value
))

watch(() => props.open, async open => {
  if (!open) {
    loadRevision += 1
    dialog.value?.close()
    return
  }
  scope.value = 'series'
  error.value = null
  success.value = ''
  retryAction.value = null
  restoreOperation = null
  await nextTick()
  dialog.value?.showModal()
  await refresh()
})

watch(scope, value => {
  if (value === 'series' && !currentSeries.value) {
    selectedSeriesId.value = series.value[0]?.id ?? ''
  }
  if (value === 'subscriptions' && !currentSubscription.value) {
    selectedSubscriptionId.value = subscriptions.value[0]?.id ?? ''
  }
  restoreOperation = null
  void resetDetailScroll()
})

function operationId(fingerprint: string) {
  if (restoreOperation?.fingerprint !== fingerprint) {
    restoreOperation = { id: crypto.randomUUID(), fingerprint }
  }
  return restoreOperation.id
}

function selectDefaultItems() {
  if (!series.value.some(item => item.id === selectedSeriesId.value)) {
    selectedSeriesId.value = series.value[0]?.id ?? ''
  }
  if (!subscriptions.value.some(item => item.id === selectedSubscriptionId.value)) {
    selectedSubscriptionId.value = subscriptions.value[0]?.id ?? ''
  }
}

async function focusFeedback(selector: '.error-status' | '.success-status') {
  await nextTick()
  const target = dialog.value?.querySelector<HTMLElement>(selector)
  if (!target) return
  target.tabIndex = -1
  target.focus()
}

async function refresh(silent = false) {
  if (!props.open || loading.value) return
  const revision = ++loadRevision
  loading.value = true
  if (!silent) {
    error.value = null
    success.value = ''
  }
  try {
    const [seriesItems, subscriptionItems] = await Promise.all([
      listSeries('trashed'),
      listCalendarSubscriptions('trashed'),
    ])
    if (revision !== loadRevision) return
    series.value = seriesItems
    subscriptions.value = subscriptionItems
    selectDefaultItems()
    loaded.value = true
    error.value = null
    retryAction.value = null
  } catch (reason) {
    if (revision !== loadRevision) return
    error.value = failure(reason)
    retryAction.value = 'load'
    await focusFeedback('.error-status')
  } finally {
    if (revision === loadRevision) loading.value = false
  }
}

function close() {
  if (pendingKey.value) return
  emit('close')
}

async function resetDetailScroll() {
  await nextTick()
  detailPanel.value?.scrollTo({ top: 0 })
}

function selectSeries(item: CalendarSeries) {
  selectedSeriesId.value = item.id
  error.value = null
  success.value = ''
  retryAction.value = null
  restoreOperation = null
  void resetDetailScroll()
}

function selectSubscription(item: CalendarSubscription) {
  selectedSubscriptionId.value = item.id
  error.value = null
  success.value = ''
  retryAction.value = null
  restoreOperation = null
  void resetDetailScroll()
}

function removeSelection(type: Scope, id: string) {
  if (type === 'series') {
    series.value = series.value.filter(item => item.id !== id)
    selectedSeriesId.value = series.value[0]?.id ?? ''
  } else {
    subscriptions.value = subscriptions.value.filter(item => item.id !== id)
    selectedSubscriptionId.value = subscriptions.value[0]?.id ?? ''
  }
  void resetDetailScroll()
}

async function restoreSelected() {
  const item = currentSelection.value
  if (!item || pendingKey.value) return
  const type = scope.value
  const fingerprint = JSON.stringify({
    action: 'restore',
    type,
    id: item.id,
    version: item.version,
  })
  const id = operationId(fingerprint)
  pendingKey.value = `${type}:${item.id}`
  error.value = null
  success.value = ''
  retryAction.value = null
  try {
    if (type === 'series') {
      await restoreCalendarSeries(item as CalendarSeries, id)
      success.value = '安排已恢复到日历。'
    } else {
      const restored = await restoreCalendarSubscription(
        item as CalendarSubscription,
        id,
      )
      success.value = restored.lifecycle_status === 'paused'
        ? '订阅已恢复，仍保持暂停。'
        : '订阅已恢复并重新进入刷新队列。'
    }
    restoreOperation = null
    removeSelection(type, item.id)
    emit('changed', success.value)
    await focusFeedback('.success-status')
  } catch (reason) {
    error.value = failure(reason)
    retryAction.value = [
      'calendar_series_version_conflict',
      'calendar_subscription_version_conflict',
      'calendar_item_not_trashed',
    ].includes(error.value.code)
      ? 'reread'
      : 'restore'
    await focusFeedback('.error-status')
  } finally {
    pendingKey.value = ''
  }
}

async function rereadSelected() {
  const item = currentSelection.value
  if (!item || pendingKey.value) return
  const type = scope.value
  pendingKey.value = `${type}:${item.id}`
  error.value = null
  success.value = ''
  try {
    if (type === 'series') {
      const latest = await getSeries(item.id)
      if (latest.lifecycle !== 'trashed') {
        removeSelection(type, item.id)
        success.value = '此安排已在其他页面恢复，回收站已更新。'
        emit('changed', success.value)
      } else {
        series.value = series.value.map(candidate => (
          candidate.id === latest.id ? latest : candidate
        ))
        success.value = '已读取服务器最新版，可以再次恢复。'
      }
    } else {
      const latest = (await getCalendarSubscription(item.id, null, 1)).subscription
      if (latest.lifecycle !== 'trashed') {
        removeSelection(type, item.id)
        success.value = '此订阅已在其他页面恢复，回收站已更新。'
        emit('changed', success.value)
      } else {
        subscriptions.value = subscriptions.value.map(candidate => (
          candidate.id === latest.id ? { ...candidate, ...latest } : candidate
        ))
        success.value = '已读取服务器最新版，可以再次恢复。'
      }
    }
    restoreOperation = null
    retryAction.value = null
    await focusFeedback('.success-status')
  } catch (reason) {
    error.value = failure(reason)
    retryAction.value = 'reread'
    await focusFeedback('.error-status')
  } finally {
    pendingKey.value = ''
  }
}

async function retry() {
  if (retryAction.value === 'load') await refresh()
  else if (retryAction.value === 'reread') await rereadSelected()
  else if (retryAction.value === 'restore') await restoreSelected()
}

function readableDateTime(value: string | null) {
  if (!value) return '尚无'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(value))
}

function readableDate(value: string) {
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(new Date(`${value}T12:00:00`))
}

function remainingText(item: CalendarSeries | CalendarSubscription) {
  if (item.trash_remaining_days === null) return ''
  return item.trash_remaining_days
    ? `还剩 ${item.trash_remaining_days} 天`
    : '即将清理'
}

function purgeText(item: CalendarSeries | CalendarSubscription) {
  return `将在 ${readableDateTime(item.purge_after)} 清理（${remainingText(item)}）`
}

function sourceLabel(item: CalendarSeries) {
  return {
    manual: '手工创建',
    import: '文件导入',
    screenshot: '截图识别',
  }[item.source_type]
}

function seriesKind(item: CalendarSeries) {
  return item.kind === 'course' ? '课程' : '固定安排'
}

function recurrenceLabel(item: CalendarSeries) {
  const recurrence = item.recurrence
  if (recurrence.frequency === 'none') return '仅此一次'
  const interval = recurrence.interval > 1 ? `每 ${recurrence.interval} ` : '每'
  const frequency = {
    daily: '天',
    weekdays: '工作日',
    weekly: '周',
    monthly: '月',
    yearly: '年',
    none: '',
  }[recurrence.frequency]
  const ending = recurrence.count
    ? `，共 ${recurrence.count} 次`
    : recurrence.until
      ? `，至 ${readableDate(recurrence.until)}`
      : ''
  return `${interval}${frequency}${ending}`
}

function seriesRange(item: CalendarSeries) {
  if (item.recurrence.until) {
    return `${readableDate(item.start_date)} 至 ${readableDate(item.recurrence.until)}`
  }
  return `自 ${readableDate(item.start_date)}`
}

function timeRange(item: CalendarSeries) {
  const nextDay = item.end_day_offset ? '次日 ' : ''
  return `${item.starts_at.slice(0, 5)} - ${nextDay}${item.ends_at.slice(0, 5)}`
}

function subscriptionStatus(item: CalendarSubscription) {
  return item.lifecycle_status === 'paused' ? '已暂停' : '使用中'
}

function intervalLabel(minutes: number) {
  if (minutes % 1440 === 0) return `${minutes / 1440} 天`
  if (minutes % 60 === 0) return `${minutes / 60} 小时`
  return `${minutes} 分钟`
}

defineExpose({ refresh })
</script>

<template>
  <dialog
    ref="dialog"
    class="calendar-dialog calendar-trash-dialog"
    aria-labelledby="calendar-trash-title"
    @cancel.prevent="close"
  >
    <header class="calendar-dialog-heading calendar-trash-heading">
      <Trash2 :size="22" aria-hidden="true" />
      <div>
        <h2 id="calendar-trash-title">日历回收站</h2>
        <p>安排与订阅会保留 30 天；恢复不会改写已有计划和复盘。</p>
      </div>
      <button
        type="button"
        class="small-icon-button calendar-trash-close"
        aria-label="关闭日历回收站"
        title="关闭日历回收站"
        :disabled="!!pendingKey"
        @click="close"
      >
        <X :size="18" aria-hidden="true" />
      </button>
    </header>

    <div class="calendar-trash-feedback">
      <StatusMessage
        :error="error"
        :success="success"
        :retry-label="retryAction === 'load'
          ? '重新读取'
          : retryAction === 'reread'
            ? '读取最新版'
            : retryAction === 'restore'
              ? '重试恢复'
              : undefined"
        @retry="retry"
      />
    </div>

    <div class="calendar-trash-toolbar">
      <fieldset class="segmented-field calendar-trash-scope">
        <legend class="sr-only">回收内容类型</legend>
        <label :class="{ selected: scope === 'series' }">
          <input v-model="scope" type="radio" value="series" />
          安排 <span aria-hidden="true">{{ series.length }}</span>
        </label>
        <label :class="{ selected: scope === 'subscriptions' }">
          <input v-model="scope" type="radio" value="subscriptions" />
          订阅 <span aria-hidden="true">{{ subscriptions.length }}</span>
        </label>
      </fieldset>
      <button
        type="button"
        class="small-icon-button"
        aria-label="刷新日历回收站"
        title="刷新日历回收站"
        :disabled="loading || !!pendingKey"
        @click="refresh()"
      >
        <RefreshCw :size="18" :class="{ spinning: loading }" aria-hidden="true" />
      </button>
    </div>

    <p v-if="loading && !loaded" class="calendar-trash-loading" role="status">
      正在读取日历回收站…
    </p>

    <div v-else class="calendar-trash-workspace">
      <aside class="calendar-trash-list-panel" aria-label="回收内容列表">
        <div class="calendar-trash-list-heading">
          <strong>{{ scope === 'series' ? '已删除安排' : '已删除订阅' }}</strong>
          <span>{{ currentItems.length }}</span>
        </div>
        <p v-if="!currentItems.length" class="calendar-trash-empty">
          {{ scope === 'series' ? '回收站中没有安排。' : '回收站中没有订阅。' }}
        </p>
        <ol v-else class="calendar-trash-list" aria-live="polite">
          <li v-for="item in currentItems" :key="item.id">
            <button
              v-if="scope === 'series'"
              type="button"
              :aria-label="`查看安排 ${(item as CalendarSeries).name}`"
              :aria-pressed="selectedSeriesId === item.id"
              :class="{ selected: selectedSeriesId === item.id }"
              :disabled="!!pendingKey"
              @click="selectSeries(item as CalendarSeries)"
            >
              <span class="calendar-trash-list-icon">
                <CalendarDays :size="18" aria-hidden="true" />
              </span>
              <span class="calendar-trash-list-copy">
                <strong>{{ (item as CalendarSeries).name }}</strong>
                <small>
                  {{ seriesKind(item as CalendarSeries) }} ·
                  {{ sourceLabel(item as CalendarSeries) }}
                </small>
                <small>{{ remainingText(item) }}</small>
              </span>
            </button>
            <button
              v-else
              type="button"
              :aria-label="`查看订阅 ${(item as CalendarSubscription).name}`"
              :aria-pressed="selectedSubscriptionId === item.id"
              :class="{ selected: selectedSubscriptionId === item.id }"
              :disabled="!!pendingKey"
              @click="selectSubscription(item as CalendarSubscription)"
            >
              <span class="calendar-trash-list-icon">
                <CalendarSync :size="18" aria-hidden="true" />
              </span>
              <span class="calendar-trash-list-copy">
                <strong>{{ (item as CalendarSubscription).name }}</strong>
                <small>{{ (item as CalendarSubscription).host }}</small>
                <small>{{ remainingText(item) }}</small>
              </span>
            </button>
          </li>
        </ol>
      </aside>

      <section
        v-if="currentSeries"
        ref="detailPanel"
        class="calendar-trash-detail"
        :aria-label="`${currentSeries.name}的回收详情`"
      >
        <header class="calendar-trash-detail-heading">
          <div>
            <span>{{ seriesKind(currentSeries) }} · {{ sourceLabel(currentSeries) }}</span>
            <h3>{{ currentSeries.name }}</h3>
          </div>
          <span class="state-label" data-state="paused">回收中</span>
        </header>
        <p class="calendar-trash-purge">
          <Clock3 :size="16" aria-hidden="true" />
          {{ purgeText(currentSeries) }}
        </p>
        <dl class="calendar-trash-metadata">
          <div>
            <dt>原日期范围</dt>
            <dd>{{ seriesRange(currentSeries) }}</dd>
          </div>
          <div>
            <dt>原时间</dt>
            <dd>{{ timeRange(currentSeries) }} · {{ currentSeries.time_zone }}</dd>
          </div>
          <div>
            <dt>重复</dt>
            <dd>{{ recurrenceLabel(currentSeries) }}</dd>
          </div>
          <div>
            <dt>地点</dt>
            <dd>{{ currentSeries.location || '未填写' }}</dd>
          </div>
          <div>
            <dt>删除时间</dt>
            <dd>{{ readableDateTime(currentSeries.deleted_at) }}</dd>
          </div>
          <div>
            <dt>占用状态</dt>
            <dd>{{ currentSeries.busy ? '占用时间' : '显示为空闲' }}</dd>
          </div>
        </dl>
        <div v-if="currentSeries.description" class="calendar-trash-description">
          <strong>说明</strong>
          <p>{{ currentSeries.description }}</p>
        </div>
        <div class="calendar-trash-restore">
          <p>恢复后会使用原系列、重复规则、单次调整和补课，不会生成新 ID。</p>
          <button
            type="button"
            class="button"
            :disabled="!!pendingKey"
            :aria-label="`恢复${currentSeries.name}`"
            @click="restoreSelected"
          >
            <RotateCcw :size="17" aria-hidden="true" />
            {{ pendingKey ? '正在恢复…' : '恢复安排' }}
          </button>
        </div>
      </section>

      <section
        v-else-if="currentSubscription"
        ref="detailPanel"
        class="calendar-trash-detail"
        :aria-label="`${currentSubscription.name}的回收详情`"
      >
        <header class="calendar-trash-detail-heading">
          <div>
            <span>ICS 订阅 · {{ currentSubscription.host }}</span>
            <h3>{{ currentSubscription.name }}</h3>
          </div>
          <span class="state-label" data-state="paused">回收中</span>
        </header>
        <p class="calendar-trash-purge">
          <Clock3 :size="16" aria-hidden="true" />
          {{ purgeText(currentSubscription) }}
        </p>
        <dl class="calendar-trash-metadata">
          <div>
            <dt>安全来源</dt>
            <dd class="calendar-trash-source">
              <Link2 :size="14" aria-hidden="true" />
              {{ currentSubscription.url_display }}
            </dd>
          </div>
          <div>
            <dt>删除前状态</dt>
            <dd>删除前状态：{{ subscriptionStatus(currentSubscription) }}</dd>
          </div>
          <div>
            <dt>已保存事件</dt>
            <dd>{{ currentSubscription.event_count ?? 0 }} 条</dd>
          </div>
          <div>
            <dt>刷新周期</dt>
            <dd>{{ intervalLabel(currentSubscription.refresh_interval_minutes) }}</dd>
          </div>
          <div>
            <dt>最近成功</dt>
            <dd>{{ readableDateTime(currentSubscription.last_success_at) }}</dd>
          </div>
          <div>
            <dt>删除时间</dt>
            <dd>{{ readableDateTime(currentSubscription.deleted_at) }}</dd>
          </div>
        </dl>
        <div class="calendar-trash-restore">
          <p v-if="currentSubscription.lifecycle_status === 'paused'">
            恢复后仍保持暂停，不会请求来源；已有事件会重新出现在日历。
          </p>
          <p v-else>
            恢复后立即进入刷新队列；刷新完成前保留已保存的来源事件。
          </p>
          <button
            type="button"
            class="button"
            :disabled="!!pendingKey"
            :aria-label="`恢复${currentSubscription.name}`"
            @click="restoreSelected"
          >
            <RotateCcw :size="17" aria-hidden="true" />
            {{ pendingKey ? '正在恢复…' : '恢复订阅' }}
          </button>
        </div>
      </section>

      <div v-else class="calendar-trash-detail-empty">
        <Trash2 :size="28" aria-hidden="true" />
        <strong>{{ scope === 'series' ? '没有可恢复的安排' : '没有可恢复的订阅' }}</strong>
        <p>移入回收站的内容会在这里保留 30 天。</p>
      </div>
    </div>
  </dialog>
</template>
