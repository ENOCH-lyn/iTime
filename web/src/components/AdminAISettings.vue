<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import {
  FlaskConical,
  GitBranch,
  RotateCcw,
  Rocket,
  Save,
  ServerCog,
} from 'lucide-vue-next'
import FormField from './FormField.vue'
import StatusMessage from './StatusMessage.vue'
import {
  createAIConfig,
  getAIConfigRoute,
  listAIConfigs,
  publishAIConfig,
  saveAIConfigRoute,
  testAIConfig,
  type AIConfigCapability,
  type AIConfigRoute,
  type AIConfigStatus,
  type AIConfigVersion,
} from '../lib/admin-ai'
import { failure, type Failure } from '../lib/api'
import { refreshAdminSession } from '../lib/admin'
import { localDate } from '../lib/admin-management'

const configs = ref<AIConfigVersion[]>([])
const loaded = ref(false)
const loading = ref(false)
const route = ref<AIConfigRoute | null>(null)
const firstFallbackId = ref('')
const secondFallbackId = ref('')
const pendingId = ref('')
const pendingAction = ref<'create' | 'test' | 'publish' | 'route' | ''>('')
const error = ref<Failure | null>(null)
const success = ref('')
const endpoint = ref('')
const model = ref('')
const apiKey = ref('')
const timeout = ref('120')
const maxTokens = ref('4096')
const currency = ref('')
const inputPrice = ref('')
const cachedInputPrice = ref('')
const outputPrice = ref('')
const fieldErrors = ref<Record<string, string>>({})
const capability = ref<AIConfigCapability>('calendar_screenshot')

const capabilityCopy: Record<AIConfigCapability, {
  label: string
  heading: string
  testDescription: string
}> = {
  calendar_screenshot: {
    label: '截图识别',
    heading: '截图识别配置',
    testDescription: '合成图片',
  },
  conversation: {
    label: '全局对话',
    heading: '全局对话配置',
    testDescription: '合成文本',
  },
  planning: {
    label: '日周规划',
    heading: '日周规划配置',
    testDescription: '合成工具',
  },
  review: {
    label: '每日复盘',
    heading: '每日复盘配置',
    testDescription: '合成工具',
  },
}

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

const fallbackCandidates = computed(() => configs.value.filter(config => (
  config.id !== route.value?.primary?.id
  && ['tested', 'retired'].includes(config.status)
  && config.pricing_configured
)))
const secondFallbackCandidates = computed(() => fallbackCandidates.value.filter(
  config => config.id !== firstFallbackId.value,
))
const routeDirty = computed(() => {
  if (!route.value) return false
  const saved = route.value.fallbacks.map(item => item.config.id)
  const draft = [firstFallbackId.value, secondFallbackId.value].filter(Boolean)
  return saved.join(':') !== draft.join(':')
})

function resetRouteEditor(next: AIConfigRoute | null) {
  firstFallbackId.value = next?.fallbacks[0]?.config.id ?? ''
  secondFallbackId.value = next?.fallbacks[1]?.config.id ?? ''
}

function routeRole(configId: string) {
  if (route.value?.primary?.id === configId) return '主服务'
  const fallback = route.value?.fallbacks.find(item => item.config.id === configId)
  return fallback ? `备用 ${fallback.position}` : ''
}

function validPrice(value: string) {
  const normalized = value.trim()
  const match = /^(0|[1-9]\d*)(?:\.(\d{1,9}))?$/.exec(normalized)
  if (!match) return false
  const nanos = BigInt(match[1]) * 1_000_000_000n
    + BigInt((match[2] ?? '').padEnd(9, '0'))
  return nanos <= 1_000_000_000_000_000n
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

async function loadConfigs(keepStatus = false, preserveRouteDraft = false) {
  if (loading.value) return
  loading.value = true
  if (!keepStatus) {
    error.value = null
    success.value = ''
  }
  try {
    const [nextConfigs, nextRoute] = await Promise.all([
      listAIConfigs(capability.value),
      getAIConfigRoute(capability.value),
    ])
    configs.value = nextConfigs
    route.value = nextRoute
    if (!preserveRouteDraft) resetRouteEditor(nextRoute)
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
  if (!/^[A-Za-z]{3}$/.test(currency.value.trim())) {
    errors.currency = '请输入三个字母的币种代码，例如 CNY。'
  }
  if (!validPrice(inputPrice.value)) {
    errors.inputPrice = '请输入 0—1000000，最多 9 位小数。'
  }
  if (cachedInputPrice.value.trim() && !validPrice(cachedInputPrice.value)) {
    errors.cachedInputPrice = '请输入 0—1000000，最多 9 位小数。'
  }
  if (!validPrice(outputPrice.value)) {
    errors.outputPrice = '请输入 0—1000000，最多 9 位小数。'
  }
  fieldErrors.value = errors
  return Object.keys(errors).length === 0
}

async function createConfig() {
  if (pendingAction.value || !validate()) {
    if (Object.keys(fieldErrors.value).length) {
      await nextTick()
      const first = [
        'endpoint',
        'model',
        'apiKey',
        'timeout',
        'maxTokens',
        'currency',
        'inputPrice',
        'cachedInputPrice',
        'outputPrice',
      ]
        .find(key => fieldErrors.value[key])
      if (first) document.getElementById(`ai-${first}`)?.focus()
    }
    return
  }
  pendingAction.value = 'create'
  error.value = null
  success.value = ''
  try {
    const created = await createAIConfig(capability.value, {
      endpoint_url: endpoint.value.trim(),
      model: model.value.trim(),
      api_key: apiKey.value,
      timeout_seconds: Number(timeout.value),
      max_output_tokens: Number(maxTokens.value),
      pricing: {
        currency: currency.value.trim().toUpperCase(),
        input_per_million_tokens: inputPrice.value.trim(),
        cached_input_per_million_tokens:
          cachedInputPrice.value.trim() || undefined,
        output_per_million_tokens: outputPrice.value.trim(),
      },
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
    success.value = `版本 ${updated.version} 已通过${capabilityCopy[config.capability].testDescription}测试。`
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

async function saveRoute() {
  if (!route.value || pendingAction.value) return
  const fallbackIds = [
    firstFallbackId.value,
    secondFallbackId.value,
  ].filter(Boolean)
  if (new Set(fallbackIds).size !== fallbackIds.length) {
    error.value = {
      code: 'invalid_input',
      message: '两个备用位置不能选择同一配置。',
      request_id: null,
      saved: 'not_saved',
      retryable: false,
    }
    return
  }
  pendingAction.value = 'route'
  error.value = null
  success.value = ''
  try {
    const updated = await saveAIConfigRoute(route.value, fallbackIds)
    route.value = updated
    resetRouteEditor(updated)
    success.value = fallbackIds.length
      ? '备用服务调用顺序已保存。'
      : '备用服务已清空；失败时将继续重试主服务。'
  } catch (reason) {
    const current = await handleError(reason)
    error.value = current
    if (current.code === 'ai_config_route_version_conflict') {
      try {
        route.value = await getAIConfigRoute(capability.value)
        error.value = {
          ...current,
          message: '调用顺序已被其他管理员修改；当前选择仍保留，可基于最新版重试。',
        }
      } catch {
        // Keep the original conflict and route draft for a later retry.
      }
    }
  } finally {
    pendingAction.value = ''
  }
}

onMounted(() => void loadConfigs())
watch(capability, () => {
  configs.value = []
  route.value = null
  resetRouteEditor(null)
  loaded.value = false
  error.value = null
  success.value = ''
  void loadConfigs()
})
watch(firstFallbackId, value => {
  if (!value) secondFallbackId.value = ''
  if (secondFallbackId.value === value) secondFallbackId.value = ''
})
</script>

<template>
  <div class="ai-config-settings">
    <section class="management-section" aria-labelledby="new-ai-config-title">
      <div class="section-intro">
        <div>
          <h2 id="new-ai-config-title">{{ capabilityCopy[capability].heading }}</h2>
          <p class="page-description">新版本先测试，发布后仅影响新提交的作业。</p>
        </div>
        <ServerCog :size="24" aria-hidden="true" />
      </div>
      <StatusMessage :error="error" :success="success"
        :retry-label="error?.retryable ? '重新读取' : undefined"
        @retry="loadConfigs()" />
      <form class="ai-config-form" novalidate @submit.prevent="createConfig">
        <fieldset class="segmented-field ai-capability-switch">
          <legend>能力</legend>
          <label v-for="(copy, key) in capabilityCopy" :key="key"
            :class="{ selected: capability === key }">
            <input v-model="capability" type="radio" name="ai-capability"
              :value="key" :disabled="!!pendingAction" />
            {{ copy.label }}
          </label>
        </fieldset>
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
        <fieldset class="ai-pricing-fields">
          <legend>供应商价格</legend>
          <p>金额按每百万 Token 填写，并随此配置版本冻结。</p>
          <div class="ai-config-number-grid">
            <FormField id="ai-currency" v-model="currency" label="币种代码"
              autocomplete="off" :maxlength="3" :error="fieldErrors.currency"
              :disabled="!!pendingAction" hint="三个字母，例如 CNY 或 USD。"
              @blur="validate" />
            <FormField id="ai-inputPrice" v-model="inputPrice"
              label="输入价格（每百万 Token）" inputmode="decimal"
              :maxlength="32" :error="fieldErrors.inputPrice"
              :disabled="!!pendingAction" @blur="validate" />
            <FormField id="ai-cachedInputPrice" v-model="cachedInputPrice"
              label="缓存输入价格（每百万 Token）" inputmode="decimal"
              :maxlength="32" :error="fieldErrors.cachedInputPrice"
              :disabled="!!pendingAction" hint="留空时与普通输入价格相同。"
              @blur="validate" />
            <FormField id="ai-outputPrice" v-model="outputPrice"
              label="输出价格（每百万 Token）" inputmode="decimal"
              :maxlength="32" :error="fieldErrors.outputPrice"
              :disabled="!!pendingAction" @blur="validate" />
          </div>
        </fieldset>
        <button type="submit" class="button ai-config-submit" :disabled="!!pendingAction">
          <ServerCog :size="18" aria-hidden="true" />
          {{ pendingAction === 'create' ? '正在保存…' : '保存新版本' }}
        </button>
      </form>
    </section>

    <section class="management-section ai-route-section" aria-labelledby="ai-route-title">
      <div class="section-intro">
        <div>
          <h2 id="ai-route-title">调用顺序</h2>
          <p class="page-description">仅可重试故障进入下一服务，三次尝试共享原作业截止时间。</p>
        </div>
        <GitBranch :size="24" aria-hidden="true" />
      </div>
      <p v-if="loading && !route" role="status" class="muted">正在读取调用顺序…</p>
      <p v-else-if="!route?.primary" class="empty-state">
        发布一个主配置后才能设置备用服务。
      </p>
      <form v-else class="ai-route-form" @submit.prevent="saveRoute">
        <dl class="ai-route-primary">
          <div>
            <dt>主服务</dt>
            <dd>版本 {{ route.primary.version }} · {{ route.primary.model }}</dd>
          </div>
        </dl>
        <div class="ai-route-selects">
          <div class="field">
            <label for="ai-fallback-one">备用 1</label>
            <select id="ai-fallback-one" v-model="firstFallbackId"
              :disabled="!!pendingAction">
              <option value="">不设置</option>
              <option v-for="config in fallbackCandidates" :key="config.id"
                :value="config.id">
                版本 {{ config.version }} · {{ config.model }}
              </option>
            </select>
          </div>
          <div class="field">
            <label for="ai-fallback-two">备用 2</label>
            <select id="ai-fallback-two" v-model="secondFallbackId"
              :disabled="!!pendingAction || !firstFallbackId">
              <option value="">不设置</option>
              <option v-for="config in secondFallbackCandidates" :key="config.id"
                :value="config.id">
                版本 {{ config.version }} · {{ config.model }}
              </option>
            </select>
          </div>
        </div>
        <div class="inline-actions">
          <button type="button" class="button secondary"
            :disabled="!!pendingAction || !routeDirty"
            @click="resetRouteEditor(route)">
            <RotateCcw :size="16" aria-hidden="true" />撤销调整
          </button>
          <button type="submit" class="button"
            :disabled="!!pendingAction || !routeDirty">
            <Save :size="16" aria-hidden="true" />
            {{ pendingAction === 'route' ? '正在保存…' : '保存调用顺序' }}
          </button>
        </div>
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
      <p v-else-if="loaded && !configs.length" class="empty-state">
        还没有{{ capabilityCopy[capability].label }}配置。
      </p>
      <ol v-else class="management-list ai-config-list" aria-label="AI 配置版本">
        <li v-for="config in configs" :key="config.id">
          <div class="management-primary">
            <div class="identity-line">
              <strong>版本 {{ config.version }} · {{ config.model }}</strong>
              <span class="state-label" :data-state="config.status">
                {{ statusLabels[config.status] }}
              </span>
              <span v-if="routeRole(config.id)" class="state-label"
                :data-state="route?.primary?.id === config.id ? 'active' : 'pending'">
                {{ routeRole(config.id) }}
              </span>
            </div>
            <p class="wrap-anywhere">{{ config.endpoint_url }}</p>
            <p>
              Key 已加密设置 · 超时 {{ config.timeout_seconds }} 秒 ·
              最多 {{ config.max_output_tokens }} Token
            </p>
            <p v-if="config.pricing">
              {{ config.pricing.currency }}
              输入 {{ config.pricing.input_per_million_tokens }} ·
              缓存输入 {{ config.pricing.cached_input_per_million_tokens }} ·
              输出 {{ config.pricing.output_per_million_tokens }} / 百万 Token
            </p>
            <p v-else class="warning-text">
              此历史版本未配置价格，调用只能记录用量，不能计算金额。
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
