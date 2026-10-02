<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { FlaskConical, RotateCcw, Rocket, ServerCog } from 'lucide-vue-next'
import FormField from './FormField.vue'
import StatusMessage from './StatusMessage.vue'
import {
  createAIConfig,
  listAIConfigs,
  publishAIConfig,
  testAIConfig,
  type AIConfigStatus,
  type AIConfigVersion,
} from '../lib/admin-ai'
import { failure, type Failure } from '../lib/api'
import { refreshAdminSession } from '../lib/admin'
import { localDate } from '../lib/admin-management'

const configs = ref<AIConfigVersion[]>([])
const loaded = ref(false)
const loading = ref(false)
const pendingId = ref('')
const pendingAction = ref<'create' | 'test' | 'publish' | ''>('')
const error = ref<Failure | null>(null)
const success = ref('')
const endpoint = ref('')
const model = ref('')
const apiKey = ref('')
const timeout = ref('120')
const maxTokens = ref('4096')
const fieldErrors = ref<Record<string, string>>({})

const statusLabels: Record<AIConfigStatus, string> = {
  draft: '草稿',
  tested: '测试通过',
  published: '当前发布',
  retired: '历史版本',
}

const stageLabels: Record<string, string> = {
  endpoint_policy: '地址策略',
  configuration: '服务配置',
  network: '网络连接',
  authentication: '凭据验证',
  upstream: '上游服务',
  response: '响应格式',
}

async function handleError(reason: unknown) {
  const current = failure(reason)
  if (['admin_unauthenticated', 'invalid_csrf'].includes(current.code)) {
    await refreshAdminSession()
  }
  await nextTick()
  document.querySelector<HTMLElement>('.ai-config-settings .error-status')?.focus()
  return current
}

async function loadConfigs(keepStatus = false) {
  if (loading.value) return
  loading.value = true
  if (!keepStatus) {
    error.value = null
    success.value = ''
  }
  try {
    configs.value = await listAIConfigs()
    loaded.value = true
  } catch (reason) {
    error.value = await handleError(reason)
  } finally {
    loading.value = false
  }
}

function validate() {
  const errors: Record<string, string> = {}
  try {
    const parsed = new URL(endpoint.value.trim())
    if (!['http:', 'https:'].includes(parsed.protocol)
      || parsed.username
      || parsed.password
      || parsed.search
      || parsed.hash) {
      errors.endpoint = '请输入不含凭据、查询或片段的 HTTP(S) 地址。'
    }
  } catch {
    errors.endpoint = '请输入完整的 HTTP(S) 地址。'
  }
  if (!/^[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}$/.test(model.value.trim())) {
    errors.model = '模型名称格式无效。'
  }
  if (!apiKey.value.trim()) errors.apiKey = '请输入服务端 API Key。'
  const timeoutValue = Number(timeout.value)
  if (!Number.isInteger(timeoutValue) || timeoutValue < 1 || timeoutValue > 300) {
    errors.timeout = '请输入 1—300 秒。'
  }
  const tokenValue = Number(maxTokens.value)
  if (!Number.isInteger(tokenValue) || tokenValue < 1 || tokenValue > 32768) {
    errors.maxTokens = '请输入 1—32768。'
  }
  fieldErrors.value = errors
  return Object.keys(errors).length === 0
}

async function createConfig() {
  if (pendingAction.value || !validate()) {
    if (Object.keys(fieldErrors.value).length) {
      await nextTick()
      const first = ['endpoint', 'model', 'apiKey', 'timeout', 'maxTokens']
        .find(key => fieldErrors.value[key])
      if (first) document.getElementById(`ai-${first}`)?.focus()
    }
    return
  }
  pendingAction.value = 'create'
  error.value = null
  success.value = ''
  try {
    const created = await createAIConfig({
      endpoint_url: endpoint.value.trim(),
      model: model.value.trim(),
      api_key: apiKey.value,
      timeout_seconds: Number(timeout.value),
      max_output_tokens: Number(maxTokens.value),
    })
    apiKey.value = ''
    configs.value = [created, ...configs.value]
    success.value = `版本 ${created.version} 已保存为草稿。`
  } catch (reason) {
    error.value = await handleError(reason)
  } finally {
    pendingAction.value = ''
  }
}

async function testConfig(config: AIConfigVersion) {
  if (pendingAction.value) return
  pendingId.value = config.id
  pendingAction.value = 'test'
  error.value = null
  success.value = ''
  try {
    const updated = await testAIConfig(config.id, config.timeout_seconds)
    configs.value = configs.value.map(item => item.id === updated.id ? updated : item)
    success.value = `版本 ${updated.version} 已通过合成图片测试。`
  } catch (reason) {
    error.value = await handleError(reason)
    await loadConfigs(true)
  } finally {
    pendingId.value = ''
    pendingAction.value = ''
  }
}

async function publishConfig(config: AIConfigVersion) {
  if (pendingAction.value) return
  pendingId.value = config.id
  pendingAction.value = 'publish'
  error.value = null
  success.value = ''
  try {
    const updated = await publishAIConfig(config.id)
    await loadConfigs(true)
    success.value = config.status === 'retired'
      ? `已回滚到版本 ${updated.version}。`
      : `版本 ${updated.version} 已发布。`
  } catch (reason) {
    error.value = await handleError(reason)
    await loadConfigs(true)
  } finally {
    pendingId.value = ''
    pendingAction.value = ''
  }
}

onMounted(() => void loadConfigs())
</script>

<template>
  <div class="ai-config-settings">
    <section class="management-section" aria-labelledby="new-ai-config-title">
      <div class="section-intro">
        <div>
          <h2 id="new-ai-config-title">截图识别配置</h2>
          <p class="page-description">新版本先测试，发布后仅影响新提交的作业。</p>
        </div>
        <ServerCog :size="24" aria-hidden="true" />
      </div>
      <StatusMessage :error="error" :success="success"
        :retry-label="error?.retryable ? '重新读取' : undefined"
        @retry="loadConfigs()" />
      <form class="ai-config-form" novalidate @submit.prevent="createConfig">
        <FormField id="ai-endpoint" v-model="endpoint" label="OpenAI 兼容端点"
          autocomplete="url" :maxlength="2048"
          :error="fieldErrors.endpoint" :disabled="!!pendingAction"
          hint="例如 https://provider.example/v1" @blur="validate" />
        <FormField id="ai-model" v-model="model" label="模型"
          autocomplete="off" :maxlength="255" :error="fieldErrors.model"
          :disabled="!!pendingAction" @blur="validate" />
        <FormField id="ai-apiKey" v-model="apiKey" label="API Key" type="password"
          autocomplete="new-password" :maxlength="8192" :error="fieldErrors.apiKey"
          :disabled="!!pendingAction" hint="保存后不再显示原值。" @blur="validate" />
        <div class="ai-config-number-grid">
          <FormField id="ai-timeout" v-model="timeout" label="单次超时（秒）"
            type="number" inputmode="numeric" :min="1" :max="300" :step="1"
            :error="fieldErrors.timeout" :disabled="!!pendingAction" @blur="validate" />
          <FormField id="ai-maxTokens" v-model="maxTokens" label="最大输出 Token"
            type="number" inputmode="numeric" :min="1" :max="32768" :step="1"
            :error="fieldErrors.maxTokens" :disabled="!!pendingAction" @blur="validate" />
        </div>
        <button type="submit" class="button ai-config-submit" :disabled="!!pendingAction">
          <ServerCog :size="18" aria-hidden="true" />
          {{ pendingAction === 'create' ? '正在保存…' : '保存新版本' }}
        </button>
      </form>
    </section>

    <section class="management-section" aria-labelledby="ai-config-history-title">
      <div class="section-intro">
        <div>
          <h2 id="ai-config-history-title">配置版本</h2>
          <p class="page-description">合成测试不会发送用户内容。</p>
        </div>
        <button type="button" class="button secondary compact-button"
          :disabled="loading || !!pendingAction" @click="loadConfigs()">
          <RotateCcw :size="16" aria-hidden="true" />刷新
        </button>
      </div>
      <p v-if="loading && !loaded" role="status" class="muted">正在读取配置…</p>
      <p v-else-if="loaded && !configs.length" class="empty-state">还没有截图识别配置。</p>
      <ol v-else class="management-list ai-config-list" aria-label="AI 配置版本">
        <li v-for="config in configs" :key="config.id">
          <div class="management-primary">
            <div class="identity-line">
              <strong>版本 {{ config.version }} · {{ config.model }}</strong>
              <span class="state-label" :data-state="config.status">
                {{ statusLabels[config.status] }}
              </span>
            </div>
            <p class="wrap-anywhere">{{ config.endpoint_url }}</p>
            <p>
              Key 已加密设置 · 超时 {{ config.timeout_seconds }} 秒 ·
              最多 {{ config.max_output_tokens }} Token
            </p>
            <p v-if="config.last_test_completed_at">
              最近测试 {{ localDate(config.last_test_completed_at) }}
              <span v-if="config.last_test_error_stage">
                · {{ stageLabels[config.last_test_error_stage] ?? config.last_test_error_stage }}失败
              </span>
            </p>
          </div>
          <div class="row-actions">
            <button v-if="['draft', 'tested'].includes(config.status)"
              type="button" class="button secondary compact-button"
              :disabled="!!pendingAction" @click="testConfig(config)">
              <FlaskConical :size="16" aria-hidden="true" />
              {{ pendingId === config.id && pendingAction === 'test' ? '正在测试…' : '测试' }}
            </button>
            <button v-if="['tested', 'retired'].includes(config.status)"
              type="button" class="button compact-button"
              :disabled="!!pendingAction" @click="publishConfig(config)">
              <Rocket :size="16" aria-hidden="true" />
              {{ pendingId === config.id && pendingAction === 'publish'
                ? '正在切换…'
                : config.status === 'retired' ? '回滚' : '发布' }}
            </button>
          </div>
        </li>
      </ol>
    </section>
  </div>
</template>
