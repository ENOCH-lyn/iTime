<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ExternalLink, LogOut } from 'lucide-vue-next'
import AIUsageBar from '../components/AIUsageBar.vue'
import PasswordSettings from '../components/PasswordSettings.vue'
import StatusMessage from '../components/StatusMessage.vue'
import AppShell from '../shell/AppShell.vue'
import { useAppShell } from '../shell/useAppShell'
import { authError, identity, refreshSession } from '../lib/auth'
import { failure, type Failure } from '../lib/api'
import { getTimeSettings, saveTimeSettings, type TimeSettings } from '../lib/calendar'
import { fontSize, theme } from '../lib/appearance'

const router = useRouter()
const { pendingLogout, logoutError, logout } = useAppShell()
const passwordOpen = ref(false)
const booted = ref(false)
const timeSettings = ref<TimeSettings | null>(null)
const weatherLocation = ref('')
const locationPending = ref(false)
const locationError = ref<Failure | null>(null)
const locationSaved = ref('')
const avatarLetter = computed(() => (
  identity.value?.user.email?.slice(0, 1).toUpperCase() ?? 'i'
))

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
    <div class="st-page">
      <StatusMessage :error="logoutError" />
      <StatusMessage
        :error="authError"
        :retry-label="authError?.retryable ? '重新连接' : undefined"
        @retry="refreshSession"
      />
      <p v-if="!booted" role="status" class="st-muted">正在读取账号…</p>

      <template v-else-if="identity">
        <section class="st-section" aria-labelledby="st-account-title">
          <h2 id="st-account-title" class="st-heading">账号</h2>
          <div class="st-card">
            <div class="st-profile">
              <span class="st-avatar" aria-hidden="true">{{ avatarLetter }}</span>
              <div class="st-profile-copy">
                <strong>{{ identity.user.email }}</strong>
                <p>个人账号</p>
              </div>
            </div>
            <div class="st-row">
              <div>
                <strong>密码</strong>
                <p>定期更新登录密码。</p>
              </div>
              <button type="button" class="m3-button m3-button--outlined" @click="passwordOpen = true">
                修改密码
              </button>
            </div>
            <div class="st-row">
              <div>
                <strong>退出登录</strong>
                <p>结束此设备上的登录会话。</p>
              </div>
              <button type="button" class="m3-button m3-button--text st-danger" :disabled="pendingLogout" @click="logout">
                <LogOut :size="17" aria-hidden="true" />
                {{ pendingLogout ? '正在退出…' : '退出登录' }}
              </button>
            </div>
          </div>
        </section>

        <section class="st-section" aria-labelledby="st-usage-title">
          <h2 id="st-usage-title" class="st-heading">AI 用量</h2>
          <div class="st-card st-usage">
            <AIUsageBar />
          </div>
        </section>

        <section class="st-section" aria-labelledby="st-prefs-title">
          <h2 id="st-prefs-title" class="st-heading">偏好</h2>
          <div class="st-card">
            <div class="st-grid">
              <div class="st-field">
                <label for="theme">外观</label>
                <ElSelect id="theme" v-model="theme" class="st-select" aria-label="外观主题">
                  <ElOption value="system" label="跟随系统" />
                  <ElOption value="light" label="浅色" />
                  <ElOption value="dark" label="深色" />
                </ElSelect>
              </div>
              <div class="st-field">
                <label for="font-size">字号</label>
                <ElSelect id="font-size" v-model="fontSize" class="st-select" aria-label="字号">
                  <ElOption value="normal" label="标准" />
                  <ElOption value="large" label="大" />
                  <ElOption value="larger" label="更大" />
                </ElSelect>
              </div>
            </div>
            <p class="st-hint st-grid-hint">外观与字号保存在当前浏览器。</p>
            <div class="st-row">
              <div>
                <strong>学期与节次</strong>
                <p>在日历中管理学期、课表和节次。</p>
              </div>
              <RouterLink to="/calendar?view=month" class="m3-button m3-button--text">
                打开日历
                <ExternalLink :size="16" aria-hidden="true" />
              </RouterLink>
            </div>
          </div>
        </section>

        <section class="st-section" aria-labelledby="st-weather-title">
          <h2 id="st-weather-title" class="st-heading">天气地点</h2>
          <div class="st-card">
            <form class="st-form" @submit.prevent="saveWeatherLocation">
              <div class="st-field st-form-field">
                <label for="weather-location">城市</label>
                <input
                  id="weather-location"
                  v-model="weatherLocation"
                  class="st-input"
                  maxlength="120"
                  placeholder="例如 上海"
                  autocomplete="address-level2"
                  @input="locationSaved = ''; locationError = null"
                >
                <p class="st-hint">AI 查天气时使用这个地点。</p>
              </div>
              <button type="submit" class="m3-button m3-button--filled" :disabled="locationPending">
                {{ locationPending ? '保存中…' : '保存' }}
              </button>
            </form>
            <StatusMessage :error="locationError" />
            <p v-if="locationSaved" class="st-hint st-hint--success" role="status">{{ locationSaved }}</p>
          </div>
        </section>
      </template>
    </div>

    <ElDialog
      v-model="passwordOpen"
      title="修改密码"
      width="min(520px, calc(100vw - 24px))"
      :show-close="false"
      class="st-password-dialog"
    >
      <PasswordSettings v-if="passwordOpen" />
      <template #footer>
        <button type="button" class="m3-button m3-button--text" @click="passwordOpen = false">
          关闭
        </button>
      </template>
    </ElDialog>
  </AppShell>
</template>

<style scoped>
.st-page {
  width: 100%;
  max-width: 880px;
  margin: 0 auto;
  padding: 24px 24px 64px;
}

.st-section {
  margin-bottom: 32px;
  scroll-margin-top: 80px;
}

.st-heading {
  margin: 0 0 12px;
  padding-left: 4px;
  color: var(--m3-on-surface);
  font: 500 16px/24px var(--font);
  letter-spacing: 0.15px;
}

.st-card {
  overflow: hidden;
  border: 1px solid var(--m3-outline-variant);
  border-radius: var(--m3-shape-lg);
  background: var(--m3-surface-container-lowest);
}

.st-profile {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 20px;
  border-bottom: 1px solid var(--m3-outline-variant);
}

.st-avatar {
  display: grid;
  flex: 0 0 48px;
  place-items: center;
  width: 48px;
  height: 48px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-primary-container);
  color: var(--m3-on-primary-container);
  font: 500 18px/24px var(--font);
}

.st-profile-copy {
  min-width: 0;
}
.st-profile-copy strong {
  display: block;
  overflow-wrap: anywhere;
  color: var(--m3-on-surface);
  font: 500 16px/24px var(--font);
}
.st-profile-copy p {
  margin: 2px 0 0;
  color: var(--m3-on-surface-variant);
  font: 400 12px/16px var(--font);
}

.st-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: center;
  padding: 16px 20px;
  border-top: 1px solid var(--m3-outline-variant);
}
.st-profile + .st-row {
  border-top: 0;
}
.st-row strong {
  display: block;
  color: var(--m3-on-surface);
  font: 500 14px/20px var(--font);
  letter-spacing: 0.1px;
}
.st-row p {
  margin: 2px 0 0;
  color: var(--m3-on-surface-variant);
  font: 400 12px/16px var(--font);
}
.st-row .m3-button {
  flex: 0 0 auto;
}

.st-danger {
  color: var(--m3-error);
}

.st-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
  padding: 20px 20px 8px;
}

.st-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}
.st-field label {
  color: var(--m3-on-surface-variant);
  font: 500 12px/16px var(--font);
  letter-spacing: 0.5px;
}

.st-select {
  width: 100%;
}
:deep(.st-select .el-select__wrapper) {
  min-height: 48px;
  border-radius: var(--m3-shape-xs);
  background: var(--m3-surface-container-high);
  box-shadow: none;
  font: 400 14px/20px var(--font);
}
:deep(.st-select .el-select__wrapper.is-focused) {
  box-shadow: inset 0 0 0 2px var(--m3-primary);
}
:deep(.st-select .el-select__placeholder) {
  color: var(--m3-on-surface);
}

.st-hint {
  margin: 0;
  color: var(--m3-on-surface-variant);
  font: 400 12px/16px var(--font);
}
.st-grid-hint {
  padding: 0 20px 16px;
}
.st-hint--success {
  padding: 0 20px 16px;
  color: var(--m3-primary);
}

.st-form {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
  padding: 20px;
}
.st-form-field {
  flex: 1 1 240px;
}
.st-input {
  width: 100%;
  min-height: 48px;
  padding: 0 14px;
  border: 1px solid var(--m3-outline);
  border-radius: var(--m3-shape-xs);
  background: var(--m3-surface-container-high);
  color: var(--m3-on-surface);
  font: 400 14px/20px var(--font);
  outline: 0;
}
.st-input:focus {
  border-color: var(--m3-primary);
  box-shadow: inset 0 0 0 1px var(--m3-primary);
}

.st-muted {
  color: var(--m3-on-surface-variant);
  font: 400 14px/20px var(--font);
}

@media (max-width: 600px) {
  .st-page {
    padding: 16px 16px 48px;
  }
  .st-section {
    margin-bottom: 28px;
  }
  .st-row {
    grid-template-columns: minmax(0, 1fr);
    align-items: start;
    padding: 16px;
  }
  .st-row .m3-button {
    justify-self: start;
    margin-top: 4px;
  }
  .st-grid {
    grid-template-columns: minmax(0, 1fr);
    gap: 14px;
    padding: 16px 16px 4px;
  }
  .st-grid-hint {
    padding: 4px 16px 16px;
  }
  .st-form {
    flex-direction: column;
    align-items: stretch;
    padding: 16px;
  }
  .st-form .m3-button {
    align-self: flex-start;
  }
  .st-profile {
    padding: 16px;
  }
}
</style>
