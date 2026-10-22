<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowLeft, ExternalLink } from 'lucide-vue-next'
import AIUsageBar from '../components/AIUsageBar.vue'
import AppearanceControls from '../components/AppearanceControls.vue'
import PasswordSettings from '../components/PasswordSettings.vue'
import StatusMessage from '../components/StatusMessage.vue'
import AppShell from '../shell/AppShell.vue'
import { useAppShell } from '../shell/useAppShell'
import { authError, identity, refreshSession } from '../lib/auth'
import { failure, type Failure } from '../lib/api'
import { getTimeSettings, saveTimeSettings, type TimeSettings } from '../lib/calendar'

const router = useRouter()
const { pendingLogout, logoutError, logout } = useAppShell()
const passwordOpen = ref(false)
const booted = ref(false)
const timeSettings = ref<TimeSettings | null>(null)
const weatherLocation = ref('')
const locationPending = ref(false)
const locationError = ref<Failure | null>(null)
const locationSaved = ref('')

async function loadTimeSettings() {
  try {
    const settings = await getTimeSettings()
    timeSettings.value = settings
    weatherLocation.value = settings.weather_location ?? ''
  } catch (reason) {
    locationError.value = failure(reason)
  }
}

async function saveWeatherLocation() {
  if (!timeSettings.value || locationPending.value) return
  locationPending.value = true
  locationError.value = null
  locationSaved.value = ''
  try {
    timeSettings.value = await saveTimeSettings(
      timeSettings.value,
      timeSettings.value.time_zone || Intl.DateTimeFormat().resolvedOptions().timeZone,
      crypto.randomUUID(),
      weatherLocation.value.trim() || null,
    )
    locationSaved.value = '已保存'
  } catch (reason) {
    locationError.value = failure(reason)
  } finally {
    locationPending.value = false
  }
}

onMounted(async () => {
  document.title = '设置 · iTime'
  await refreshSession()
  booted.value = true
  if (!identity.value) await router.replace('/login')
  else await loadTimeSettings()
})
</script>

<template>
  <AppShell
    :current="null"
    title="设置"
    :pending-logout="pendingLogout"
    @logout="logout"
  >
    <div class="settings-page-wrapper">
      <nav class="settings-return-nav" aria-label="设置导航">
        <RouterLink to="/ai" class="icon-button settings-return-link" aria-label="返回 AI 对话">
          <ArrowLeft :size="20" aria-hidden="true" />
          <span>返回 AI 对话</span>
        </RouterLink>
      </nav>

      <div id="main-content" class="settings-page">
        <header class="settings-page-header">
          <h1 tabindex="-1">设置</h1>
        </header>

        <StatusMessage :error="logoutError" />
        <StatusMessage
          :error="authError"
          :retry-label="authError?.retryable ? '重新连接' : undefined"
          @retry="refreshSession"
        />
        <p v-if="!booted" role="status" class="muted">正在读取账号…</p>

        <template v-else-if="identity">
          <section id="account" class="settings-section" aria-labelledby="account-title">
            <h2 id="account-title">账号</h2>
            <div class="settings-row">
              <div>
                <strong>{{ identity.user.email }}</strong>
              </div>
            </div>
            <div class="settings-row">
              <div>
                <strong>密码</strong>
                <p>修改密码。</p>
              </div>
              <ElButton @click="passwordOpen = true">修改密码</ElButton>
            </div>
            <div class="settings-row">
              <div>
                <strong>退出登录</strong>
              </div>
              <ElButton :loading="pendingLogout" :disabled="pendingLogout" @click="logout">
                {{ pendingLogout ? '正在退出…' : '退出登录' }}
              </ElButton>
            </div>
          </section>

          <section class="settings-section" aria-labelledby="usage-title">
            <h2 id="usage-title">AI 用量</h2>
            <AIUsageBar />
          </section>

          <section class="settings-section" aria-labelledby="preferences-title">
            <h2 id="preferences-title">偏好</h2>
            <AppearanceControls />
            <div class="settings-row settings-time-link">
              <div>
                <strong>学期与节次</strong>
                <p>在月视图中管理学期、课表和节次。</p>
              </div>
              <RouterLink to="/timetable/month" class="settings-command-link">
                打开月视图
                <ExternalLink :size="16" aria-hidden="true" />
              </RouterLink>
            </div>
          </section>

          <section class="settings-section" aria-labelledby="weather-location-title">
            <h2 id="weather-location-title">天气地点</h2>
            <form class="settings-row" @submit.prevent="saveWeatherLocation">
              <div>
                <label for="weather-location"><strong>城市</strong></label>
                <p>AI 查天气时使用这个地点。</p>
                <input
                  id="weather-location"
                  v-model="weatherLocation"
                  maxlength="120"
                  placeholder="例如 上海"
                  autocomplete="address-level2"
                >
              </div>
              <ElButton native-type="submit" :loading="locationPending" :disabled="locationPending">
                保存
              </ElButton>
            </form>
            <StatusMessage :error="locationError" />
            <p v-if="locationSaved" class="muted" role="status">{{ locationSaved }}</p>
          </section>
        </template>
      </div>
    </div>

    <ElDialog
      v-model="passwordOpen"
      title="修改密码"
      width="min(520px, calc(100vw - 24px))"
      :show-close="false"
    >
      <PasswordSettings v-if="passwordOpen" />
      <template #footer>
        <ElButton @click="passwordOpen = false">关闭</ElButton>
      </template>
    </ElDialog>
  </AppShell>
</template>
