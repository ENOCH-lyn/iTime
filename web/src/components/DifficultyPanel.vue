<script setup lang="ts">
import {
  MessageCircleMore,
  Pencil,
  RotateCcw,
  Save,
  Trash2,
  X,
} from 'lucide-vue-next'
import { computed, onMounted, ref, watch } from 'vue'
import StatusMessage from './StatusMessage.vue'
import VoiceTranscriptionControl from './VoiceTranscriptionControl.vue'
import { failure, localFailure, type Failure } from '../lib/api'
import {
  createDifficulty,
  deleteDifficulty,
  listDifficulties,
  restoreDifficulty,
  updateDifficulty,
  type DifficultyDraft,
  type DifficultyRecord,
  type FocusSession,
} from '../lib/execution'
import type { TaskSummary } from '../lib/tasks'

const props = defineProps<{
  focus: FocusSession | null
  tasks: TaskSummary[]
}>()

const scope = ref<'active' | 'trashed'>('active')
const items = ref<DifficultyRecord[]>([])
const loading = ref(false)
const pending = ref(false)
const error = ref<Failure | null>(null)
const success = ref('')
const editing = ref<DifficultyRecord | null>(null)
const content = ref('')
const category = ref('')
const emotion = ref('')
const association = ref('')
let operation: { fingerprint: string; id: string } | null = null

const associationTask = computed(() => {
  if (!association.value.startsWith('task:')) return null
  return association.value.slice(5)
})

watch(() => props.focus?.id, () => {
  if (!editing.value && props.focus) association.value = `focus:${props.focus.id}`
})

onMounted(async () => {
  resetForm()
  await load()
})

function operationId(fingerprint: string) {
  if (operation?.fingerprint !== fingerprint) {
    operation = { fingerprint, id: crypto.randomUUID() }
  }
  return operation.id
}

function resetForm() {
  editing.value = null
  content.value = ''
  category.value = ''
  emotion.value = ''
  association.value = props.focus ? `focus:${props.focus.id}` : ''
  operation = null
}

function replaceVoiceContent(text: string) {
  content.value = text
  error.value = null
}

function appendVoiceContent(text: string) {
  content.value = [content.value.trim(), text].filter(Boolean).join('\n')
  error.value = null
}

async function load() {
  loading.value = true
  error.value = null
  try {
    items.value = (await listDifficulties(scope.value)).items
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    loading.value = false
  }
}

async function switchScope(next: 'active' | 'trashed') {
  if (scope.value === next) return
  scope.value = next
  resetForm()
  success.value = ''
  await load()
}

function draft(): DifficultyDraft | null {
  const text = content.value.trim()
  if (!text) {
    error.value = localFailure('请写下遇到的困难。')
    return null
  }
  const focusId = association.value.startsWith('focus:')
    ? association.value.slice(6)
    : null
  const linkedFocus = focusId && props.focus?.id === focusId ? props.focus : null
  const taskId = linkedFocus?.task_id ?? (
    editing.value?.focus_session_id === focusId
      ? editing.value.task_id
      : associationTask.value
  )
  return {
    task_id: taskId,
    focus_session_id: focusId,
    occurrence_key: linkedFocus?.occurrence_key ?? (
      editing.value?.focus_session_id === focusId
        ? editing.value.occurrence_key
        : null
    ),
    content: text,
    category: category.value.trim() || null,
    emotion: emotion.value.trim() || null,
    occurred_at: editing.value?.occurred_at ?? null,
  }
}

async function save() {
  if (pending.value || scope.value === 'trashed') return
  error.value = null
  success.value = ''
  const fields = draft()
  if (!fields) return
  const fingerprint = JSON.stringify({
    id: editing.value?.id ?? null,
    version: editing.value?.version ?? null,
    ...fields,
  })
  pending.value = true
  try {
    if (editing.value) {
      await updateDifficulty(
        editing.value,
        operationId(fingerprint),
        fields,
      )
      success.value = '困难记录已保存。'
    } else {
      await createDifficulty(operationId(fingerprint), fields)
      success.value = '困难已记录。'
    }
    operation = null
    resetForm()
    await load()
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    pending.value = false
  }
}

function edit(item: DifficultyRecord) {
  editing.value = item
  content.value = item.content
  category.value = item.category ?? ''
  emotion.value = item.emotion ?? ''
  association.value = item.focus_session_id
    ? `focus:${item.focus_session_id}`
    : item.task_id
      ? `task:${item.task_id}`
      : ''
  error.value = null
  success.value = ''
  operation = null
}

async function trash(item: DifficultyRecord) {
  if (pending.value || !window.confirm('将这条困难移到回收站？30 天内可以恢复。')) return
  pending.value = true
  error.value = null
  try {
    await deleteDifficulty(item, crypto.randomUUID())
    if (editing.value?.id === item.id) resetForm()
    success.value = '困难已移到回收站，将保留 30 天。'
    await load()
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    pending.value = false
  }
}

async function restore(item: DifficultyRecord) {
  if (pending.value) return
  pending.value = true
  error.value = null
  try {
    await restoreDifficulty(item, crypto.randomUUID())
    success.value = '困难记录已恢复。'
    await load()
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    pending.value = false
  }
}

function formatDateTime(value: string | null) {
  return value ? new Date(value).toLocaleString('zh-CN') : ''
}
</script>

<template>
  <section class="difficulty-panel" aria-labelledby="difficulty-title">
    <header class="focus-section-heading">
      <div>
        <h2 id="difficulty-title">困难</h2>
        <p>记录卡住的位置，需要时再带到 AI。</p>
      </div>
      <div class="difficulty-scope" role="group" aria-label="困难范围">
        <button
          type="button"
          :class="{ selected: scope === 'active' }"
          @click="switchScope('active')"
        >记录</button>
        <button
          type="button"
          :class="{ selected: scope === 'trashed' }"
          @click="switchScope('trashed')"
        >回收站</button>
      </div>
    </header>

    <StatusMessage :error="error" :success="success" />

    <form v-if="scope === 'active'" class="difficulty-form" @submit.prevent="save">
      <div class="field">
        <label for="difficulty-content">
          {{ editing ? '编辑困难' : '遇到了什么困难？' }}
        </label>
        <textarea
          id="difficulty-content"
          v-model="content"
          rows="3"
          maxlength="2000"
          :disabled="pending"
          placeholder="用一句话记下当前卡住的位置"
        />
        <VoiceTranscriptionControl
          purpose="difficulty_content"
          target-label="困难正文"
          :field-limit="2000"
          :current-text="content"
          :disabled="pending"
          @replace="replaceVoiceContent"
          @append="appendVoiceContent"
        />
      </div>
      <div class="difficulty-fields">
        <div class="field">
          <label for="difficulty-category">分类 <span class="muted">可选</span></label>
          <input
            id="difficulty-category"
            v-model="category"
            maxlength="40"
            :disabled="pending"
            placeholder="例如：理解、环境"
          />
        </div>
        <div class="field">
          <label for="difficulty-emotion">感受 <span class="muted">可选</span></label>
          <input
            id="difficulty-emotion"
            v-model="emotion"
            maxlength="40"
            :disabled="pending"
            placeholder="例如：困惑"
          />
        </div>
      </div>
      <div class="field">
        <label for="difficulty-association">关联</label>
        <select id="difficulty-association" v-model="association" :disabled="pending">
          <option value="">不关联</option>
          <option v-if="focus" :value="`focus:${focus.id}`">当前专注 · {{ focus.title }}</option>
          <option
            v-else-if="editing?.focus_session_id"
            :value="`focus:${editing.focus_session_id}`"
          >原专注记录</option>
          <option v-for="task in tasks" :key="task.id" :value="`task:${task.id}`">
            任务 · {{ task.title }}
          </option>
          <option
            v-if="editing?.task_id && !tasks.some(task => task.id === editing?.task_id)"
            :value="`task:${editing.task_id}`"
          >任务 · {{ editing.task_title ?? '原任务' }}</option>
        </select>
      </div>
      <div class="difficulty-form-actions">
        <button
          v-if="editing"
          type="button"
          class="button secondary"
          :disabled="pending"
          @click="resetForm"
        >
          <X :size="17" aria-hidden="true" />取消编辑
        </button>
        <button type="submit" class="button" :disabled="pending || !content.trim()">
          <Save :size="17" aria-hidden="true" />
          {{ pending ? '正在保存' : editing ? '保存困难' : '记录困难' }}
        </button>
      </div>
    </form>

    <p v-if="loading" class="focus-empty" role="status">正在读取困难记录...</p>
    <p v-else-if="!items.length" class="focus-empty">
      {{ scope === 'active' ? '还没有困难记录。' : '回收站为空。' }}
    </p>
    <ul v-else class="difficulty-list">
      <li v-for="item in items" :key="item.id">
        <div class="difficulty-copy">
          <p>{{ item.content }}</p>
          <small>
            {{ formatDateTime(item.occurred_at) }}
            <template v-if="item.task_title"> · {{ item.task_title }}</template>
            <template v-if="item.category"> · {{ item.category }}</template>
            <template v-if="item.emotion"> · {{ item.emotion }}</template>
          </small>
          <small v-if="item.lifecycle === 'trashed'">
            将于 {{ formatDateTime(item.purge_after) }} 清理
          </small>
        </div>
        <div class="difficulty-actions">
          <template v-if="item.lifecycle === 'active'">
            <button
              type="button"
              class="small-icon-button"
              title="编辑困难"
              :aria-label="`编辑困难 ${item.content}`"
              :disabled="pending"
              @click="edit(item)"
            ><Pencil :size="16" aria-hidden="true" /></button>
            <RouterLink
              :to="{ path: '/ai', query: { difficulty: item.id } }"
              class="small-icon-button"
              title="带到 AI 对话"
              :aria-label="`带到 AI 对话 ${item.content}`"
            ><MessageCircleMore :size="16" aria-hidden="true" /></RouterLink>
            <button
              type="button"
              class="small-icon-button danger-icon"
              title="删除困难"
              :aria-label="`删除困难 ${item.content}`"
              :disabled="pending"
              @click="trash(item)"
            ><Trash2 :size="16" aria-hidden="true" /></button>
          </template>
          <button
            v-else
            type="button"
            class="small-icon-button"
            title="恢复困难"
            :aria-label="`恢复困难 ${item.content}`"
            :disabled="pending"
            @click="restore(item)"
          ><RotateCcw :size="16" aria-hidden="true" /></button>
        </div>
      </li>
    </ul>
  </section>
</template>
