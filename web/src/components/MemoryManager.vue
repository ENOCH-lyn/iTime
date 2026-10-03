<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Plus, RotateCcw, Save, Trash2, X } from 'lucide-vue-next'
import { failure, localFailure, type Failure } from '../lib/api'
import {
  createMemory,
  deleteMemory,
  getMemorySettings,
  listMemories,
  restoreMemory,
  saveMemorySettings,
  updateMemory,
  type Memory,
  type MemoryDraft,
  type MemoryKind,
  type MemorySettings,
} from '../lib/memories'
import StatusMessage from './StatusMessage.vue'

const emit = defineEmits<{
  error: [error: Failure]
  success: [message: string]
}>()

const settings = ref<MemorySettings | null>(null)
const items = ref<Memory[]>([])
const lifecycle = ref<'active' | 'trashed'>('active')
const loading = ref(true)
const pending = ref(false)
const error = ref<Failure | null>(null)
const success = ref('')
const editingId = ref('')
const draft = reactive<MemoryDraft>({
  kind: 'preference',
  title: '',
  content: '',
})
let operation: { fingerprint: string; id: string } | null = null

const kindLabels: Record<MemoryKind, string> = {
  preference: '偏好',
  constraint: '约束',
  context: '背景',
  goal: '目标',
}

function operationId(fingerprint: string) {
  if (operation?.fingerprint !== fingerprint) {
    operation = { fingerprint, id: crypto.randomUUID() }
  }
  return operation.id
}

function clearDraft() {
  editingId.value = ''
  draft.kind = 'preference'
  draft.title = ''
  draft.content = ''
  operation = null
}

function setError(reason: unknown) {
  error.value = failure(reason)
  emit('error', error.value)
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const [currentSettings, page] = await Promise.all([
      getMemorySettings(),
      listMemories(lifecycle.value),
    ])
    settings.value = currentSettings
    items.value = page.items
  } catch (reason) {
    setError(reason)
  } finally {
    loading.value = false
  }
}

onMounted(load)

async function toggleEnabled() {
  if (!settings.value || pending.value) return
  pending.value = true
  error.value = null
  const enabled = !settings.value.enabled
  const id = operationId(`settings:${settings.value.version}:${enabled}`)
  try {
    settings.value = await saveMemorySettings(settings.value, enabled, id)
    operation = null
    success.value = enabled
      ? '长期记忆已恢复用于后续 AI 请求。'
      : '长期记忆已暂停，后续 AI 请求不会读取已有记忆。'
    emit('success', success.value)
  } catch (reason) {
    setError(reason)
  } finally {
    pending.value = false
  }
}

async function switchLifecycle(value: 'active' | 'trashed') {
  lifecycle.value = value
  clearDraft()
  await load()
}

function edit(memory: Memory) {
  if (!memory.title || !memory.content) return
  editingId.value = memory.id
  draft.kind = memory.kind
  draft.title = memory.title
  draft.content = memory.content
  operation = null
}

async function save() {
  if (pending.value) return
  if (!draft.title.trim() || !draft.content.trim()) {
    setError(localFailure('请填写记忆标题和内容。'))
    return
  }
  pending.value = true
  error.value = null
  const normalized: MemoryDraft = {
    kind: draft.kind,
    title: draft.title.trim(),
    content: draft.content.trim(),
  }
  const existing = items.value.find(item => item.id === editingId.value)
  const fingerprint = JSON.stringify({
    action: existing ? 'update' : 'create',
    id: existing?.id,
    version: existing?.version,
    ...normalized,
  })
  try {
    if (existing) {
      await updateMemory(existing, normalized, operationId(fingerprint))
      success.value = '记忆已更新。'
    } else {
      await createMemory(normalized, operationId(fingerprint))
      success.value = '记忆已保存。'
    }
    operation = null
    clearDraft()
    await load()
    emit('success', success.value)
  } catch (reason) {
    setError(reason)
  } finally {
    pending.value = false
  }
}

async function remove(memory: Memory) {
  if (pending.value || !window.confirm(`将“${memory.title}”移到回收站？`)) return
  pending.value = true
  error.value = null
  try {
    await deleteMemory(memory, crypto.randomUUID())
    success.value = '记忆已移到回收站，将保留 30 天。'
    await load()
    emit('success', success.value)
  } catch (reason) {
    setError(reason)
  } finally {
    pending.value = false
  }
}

async function restore(memory: Memory) {
  if (pending.value) return
  pending.value = true
  error.value = null
  try {
    await restoreMemory(memory, crypto.randomUUID())
    success.value = '记忆已恢复。'
    await load()
    emit('success', success.value)
  } catch (reason) {
    setError(reason)
  } finally {
    pending.value = false
  }
}
</script>

<template>
  <section class="memory-manager" aria-labelledby="memory-manager-title">
    <header class="review-page-heading">
      <div>
        <p class="review-eyebrow">由你确认和管理</p>
        <h1 id="memory-manager-title">长期记忆</h1>
      </div>
      <label v-if="settings" class="memory-toggle">
        <input
          type="checkbox"
          :checked="settings.enabled"
          :disabled="pending"
          @change="toggleEnabled"
        />
        <span>{{ settings.enabled ? '用于 AI' : '已暂停' }}</span>
      </label>
    </header>

    <StatusMessage :error="error" :success="success" />

    <div class="memory-toolbar">
      <div class="review-view-switch" role="tablist" aria-label="记忆范围">
        <button
          type="button"
          role="tab"
          :aria-selected="lifecycle === 'active'"
          :class="{ selected: lifecycle === 'active' }"
          @click="switchLifecycle('active')"
        >
          使用中
        </button>
        <button
          type="button"
          role="tab"
          :aria-selected="lifecycle === 'trashed'"
          :class="{ selected: lifecycle === 'trashed' }"
          @click="switchLifecycle('trashed')"
        >
          回收站
        </button>
      </div>
      <button
        v-if="lifecycle === 'active' && editingId"
        type="button"
        class="icon-button"
        title="取消编辑"
        aria-label="取消编辑记忆"
        @click="clearDraft"
      >
        <X :size="18" aria-hidden="true" />
      </button>
    </div>

    <form v-if="lifecycle === 'active'" class="memory-editor" @submit.prevent="save">
      <div class="memory-editor-row">
        <label>
          <span>类型</span>
          <select v-model="draft.kind" :disabled="pending">
            <option v-for="(label, kind) in kindLabels" :key="kind" :value="kind">
              {{ label }}
            </option>
          </select>
        </label>
        <label>
          <span>标题</span>
          <input v-model="draft.title" maxlength="120" :disabled="pending" />
        </label>
      </div>
      <label>
        <span>内容</span>
        <textarea v-model="draft.content" rows="3" maxlength="2000" :disabled="pending" />
      </label>
      <button
        type="submit"
        class="button"
        :disabled="pending || !draft.title.trim() || !draft.content.trim()"
      >
        <Save v-if="editingId" :size="17" aria-hidden="true" />
        <Plus v-else :size="17" aria-hidden="true" />
        {{ editingId ? '保存修改' : '添加记忆' }}
      </button>
    </form>

    <p v-if="loading" class="review-empty" role="status">正在读取记忆...</p>
    <p v-else-if="!items.length" class="review-empty">
      {{ lifecycle === 'active' ? '还没有已确认的长期记忆。' : '回收站为空。' }}
    </p>
    <ul v-else class="memory-list">
      <li v-for="memory in items" :key="memory.id">
        <div>
          <span class="review-source-label">{{ kindLabels[memory.kind] }}</span>
          <strong>{{ memory.title }}</strong>
          <p>{{ memory.content }}</p>
          <small v-if="memory.lifecycle === 'trashed'">
            将在 {{ new Date(memory.purge_after!).toLocaleDateString('zh-CN') }} 后清理
          </small>
        </div>
        <div class="review-inline-actions">
          <button
            v-if="memory.lifecycle === 'active'"
            type="button"
            class="button secondary"
            :disabled="pending"
            @click="edit(memory)"
          >
            编辑
          </button>
          <button
            v-if="memory.lifecycle === 'active'"
            type="button"
            class="icon-button danger"
            title="移到回收站"
            aria-label="删除记忆"
            :disabled="pending"
            @click="remove(memory)"
          >
            <Trash2 :size="17" aria-hidden="true" />
          </button>
          <button
            v-else
            type="button"
            class="button secondary"
            :disabled="pending"
            @click="restore(memory)"
          >
            <RotateCcw :size="17" aria-hidden="true" />
            恢复
          </button>
        </div>
      </li>
    </ul>
  </section>
</template>
