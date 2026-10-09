<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import { Save, ShieldCheck } from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import { failure, type Failure } from '../lib/api'
import { refreshSession } from '../lib/auth'
import {
  getPrivacySettings,
  updatePrivacySettings,
  type PrivacySettings,
} from '../lib/privacy-settings'
import { useSyncRefresh } from '../lib/use-sync-refresh'

defineProps<{ compact?: boolean }>()
const settings = ref<PrivacySettings | null>(null)
const qualityEnabled = ref(true)
const loading = ref(false)
const saving = ref(false)
const error = ref<Failure | null>(null)
const success = ref('')
const pendingOperationId = ref('')
const pendingFingerprint = ref('')
const hasUnsavedChoice = computed(() => (
  settings.value !== null
  && qualityEnabled.value !== settings.value.quality_improvement_enabled
))

async function focusError() {
  await nextTick()
  document.querySelector<HTMLElement>('.privacy-settings .error-status')?.focus()
}

async function load(options: { preserveChoice?: boolean; keepStatus?: boolean } = {}) {
  if (loading.value) return null
  loading.value = true
  if (!options.keepStatus) {
    error.value = null
    success.value = ''
  }
  try {
    const current = await getPrivacySettings()
    settings.value = current
    if (!options.preserveChoice) qualityEnabled.value = current.quality_improvement_enabled
    return current
  } catch (reason) {
    error.value = failure(reason)
    if (['unauthenticated', 'invalid_csrf'].includes(error.value.code)) {
      await refreshSession()
    }
    await focusError()
    return null
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!settings.value || saving.value) return
  saving.value = true
  error.value = null
  success.value = ''
  const desired = qualityEnabled.value
  const fingerprint = `${settings.value.version}:${desired}`
  if (pendingFingerprint.value !== fingerprint) {
    pendingFingerprint.value = fingerprint
    pendingOperationId.value = crypto.randomUUID()
  }
  try {
    const result = await updatePrivacySettings({
      operation_id: pendingOperationId.value,
      expected_version: settings.value.version,
      quality_improvement_enabled: desired,
    })
    settings.value = result.settings
    qualityEnabled.value = result.settings.quality_improvement_enabled
    pendingOperationId.value = ''
    pendingFingerprint.value = ''
    success.value = desired
      ? '已参与质量改进；后续符合条件的 AI 内容可以进入质量样本。'
      : '已退出质量改进；后续 AI 内容不会新增质量样本。'
  } catch (reason) {
    const currentError = failure(reason)
    error.value = currentError
    if (['unauthenticated', 'invalid_csrf'].includes(currentError.code)) {
      await refreshSession()
    }
    if (
      currentError.code === 'privacy_settings_version_conflict'
      || currentError.saved === 'unknown'
    ) {
      const current = await load({ preserveChoice: true, keepStatus: true })
      if (current?.quality_improvement_enabled === desired) {
        qualityEnabled.value = desired
        pendingOperationId.value = ''
        pendingFingerprint.value = ''
        error.value = null
        success.value = '已从服务器确认隐私设置已保存。'
      } else if (currentError.code === 'privacy_settings_version_conflict') {
        pendingOperationId.value = ''
        pendingFingerprint.value = ''
      }
    }
    if (error.value) await focusError()
  } finally {
    saving.value = false
  }
}

onMounted(() => void load())
useSyncRefresh(
  ['privacy_settings'],
  async () => {
    await load({ preserveChoice: saving.value || hasUnsavedChoice.value })
  },
)
</script>

<template>
  <div
    class="privacy-settings"
    :class="{ 'narrow-content': !compact, 'privacy-settings-compact': compact }"
  >
    <div v-if="!compact" class="section-intro">
      <div>
        <p class="page-description">
          管理 AI 内容是否可以进入短期质量样本。技术排障日志与此设置分开。
        </p>
      </div>
      <ShieldCheck :size="24" aria-hidden="true" />
    </div>
    <StatusMessage :error="error" :success="success"
      :retry-label="error?.retryable ? '重新读取' : undefined" @retry="load()" />
    <p v-if="loading && !settings" role="status" class="muted">正在读取隐私设置…</p>
    <template v-else-if="settings">
      <section class="privacy-setting-row" aria-labelledby="quality-improvement-title">
        <div>
          <h2 id="quality-improvement-title">参与质量改进</h2>
          <p>用于人工质检、提示词优化和效果评测，不用于训练或微调。</p>
          <p>关闭后不再新增样本；已有样本最长保留 30 天。</p>
        </div>
        <label class="switch-control">
          <span class="sr-only">参与质量改进</span>
          <input v-model="qualityEnabled" type="checkbox" role="switch"
            :disabled="loading || saving" @change="success = ''" />
          <span aria-hidden="true"></span>
        </label>
      </section>
      <section class="privacy-setting-row" aria-labelledby="technical-log-title">
        <div>
          <h2 id="technical-log-title">AI 技术正文日志</h2>
          <p>请求与响应正文默认保留 7 天，用于技术排障。</p>
          <p>只有经过独立管理认证的超级管理员可以按审计流程查看。</p>
        </div>
        <span class="state-label" data-state="active">7 天</span>
      </section>
      <div class="privacy-actions">
        <button type="button" class="button" :disabled="loading || saving"
          @click="save">
          <Save :size="18" aria-hidden="true" />
          {{ saving ? '正在保存…' : '保存隐私设置' }}
        </button>
        <p class="field-hint">
          当前服务器状态：{{ settings.quality_improvement_enabled ? '参与' : '不参与' }}
        </p>
      </div>
    </template>
  </div>
</template>
