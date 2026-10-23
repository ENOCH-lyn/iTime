<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import { RotateCcw, Trash2 } from 'lucide-vue-next'
import { failure, type Failure } from '../lib/api'
import {
  listTasks,
  restoreTask,
  type TaskSummary,
} from '../lib/tasks'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{
  close: []
  changed: []
}>()

const dialog = ref<HTMLDialogElement | null>(null)
const items = ref<TaskSummary[]>([])
const loading = ref(false)
const error = ref<Failure | null>(null)
const restoringId = ref<string | null>(null)
const notice = ref('')

function dueLabel(task: TaskSummary) {
  const value = task.schedule.due_date
  if (!value) return '无截止日期'
  const date = new Date(`${value}T12:00:00`)
  return `截止 ${new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric' }).format(date)}`
}

function close() {
  if (restoringId.value) return
  emit('close')
}

async function load() {
  loading.value = true
  error.value = null
  try {
    const page = await listTasks({
      lifecycle: 'trashed',
      search: '',
      status: 'all',
      project_id: null,
      inbox: false,
      priority: null,
      label: '',
    })
    items.value = page.items
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    loading.value = false
  }
}

async function restore(task: TaskSummary) {
  if (restoringId.value) return
  restoringId.value = task.id
  error.value = null
  notice.value = ''
  try {
    await restoreTask(task, crypto.randomUUID())
    items.value = items.value.filter(item => item.id !== task.id)
    notice.value = `已恢复「${task.title}」`
    emit('changed')
  } catch (reason) {
    error.value = failure(reason)
  } finally {
    restoringId.value = null
  }
}

watch(() => props.open, async open => {
  await nextTick()
  if (open) {
    if (!dialog.value?.open) dialog.value?.showModal()
    await load()
  } else {
    dialog.value?.close()
    items.value = []
    error.value = null
    notice.value = ''
  }
}, { immediate: true })
</script>

<template>
  <dialog
    ref="dialog"
    class="task-trash-dialog"
    aria-labelledby="task-trash-title"
    @cancel.prevent="close"
    @click.self="close"
  >
    <header class="task-trash-header">
      <Trash2 :size="22" aria-hidden="true" />
      <div>
        <h2 id="task-trash-title">任务回收站</h2>
        <p>删除的任务保留 30 天，可随时恢复。</p>
      </div>
      <button type="button" class="m3-icon-button" aria-label="关闭回收站" title="关闭" @click="close">
        <span class="material-symbols-rounded" aria-hidden="true">close</span>
      </button>
    </header>

    <div class="task-trash-body">
      <p v-if="notice" class="task-trash-notice" role="status">{{ notice }}</p>
      <p v-if="error" class="task-trash-error" role="alert">{{ error.message }}</p>
      <p v-if="loading" class="task-trash-muted" role="status">正在读取回收站…</p>
      <div v-else-if="!items.length" class="task-trash-empty">
        <Trash2 :size="32" aria-hidden="true" />
        <strong>回收站是空的</strong>
        <p>删除任务后会在这里保留 30 天。</p>
      </div>
      <ul v-else class="task-trash-list">
        <li v-for="task in items" :key="task.id">
          <div class="task-trash-copy">
            <strong>{{ task.title }}</strong>
            <span>
              {{ dueLabel(task) }}
              <template v-if="task.trash_remaining_days !== null">
                · 剩余 {{ task.trash_remaining_days }} 天
              </template>
            </span>
          </div>
          <button
            type="button"
            class="m3-button m3-button--outlined"
            :aria-label="`恢复任务：${task.title}`"
            :title="`恢复「${task.title}」`"
            :disabled="!!restoringId"
            @click="restore(task)"
          >
            <RotateCcw :size="16" aria-hidden="true" />
            {{ restoringId === task.id ? '恢复中…' : '恢复' }}
          </button>
        </li>
      </ul>
    </div>

    <footer class="task-trash-footer">
      <button type="button" class="m3-button m3-button--text" @click="close">关闭</button>
    </footer>
  </dialog>
</template>

<style scoped>
.task-trash-dialog {
  inset: 0;
  width: min(640px, calc(100vw - 32px));
  max-width: min(640px, calc(100vw - 32px));
  max-height: min(760px, calc(100dvh - 32px));
  margin: auto;
  padding: 0;
  overflow: hidden;
  border: 0;
  border-radius: var(--m3-shape-xl);
  background: var(--m3-surface-container-high);
  color: var(--m3-on-surface);
  box-shadow: var(--m3-elevation-3);
}
.task-trash-dialog::backdrop {
  background: rgb(0 0 0 / 32%);
}
.task-trash-dialog[open] {
  display: flex;
  flex-direction: column;
}
.task-trash-header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 24px 24px 16px;
  border-bottom: 1px solid var(--m3-outline-variant);
}
.task-trash-header > svg {
  flex: 0 0 auto;
  width: 40px;
  height: 40px;
  padding: 9px;
  border-radius: var(--m3-shape-full);
  background: var(--m3-primary-container);
  color: var(--m3-on-primary-container);
}
.task-trash-header > div {
  flex: 1 1 auto;
  min-width: 0;
}
.task-trash-header h2 {
  margin: 0;
  font: 500 22px/28px var(--font);
  letter-spacing: 0;
}
.task-trash-header p {
  margin: 4px 0 0;
  color: var(--m3-on-surface-variant);
  font: 400 12px/16px var(--font);
}
.task-trash-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 24px 16px;
}
.task-trash-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.task-trash-list li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: center;
  min-height: 64px;
  padding: 8px 0;
  border-bottom: 1px solid var(--m3-outline-variant);
}
.task-trash-copy {
  min-width: 0;
}
.task-trash-copy strong {
  display: block;
  overflow: hidden;
  font: 500 14px/20px var(--font);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.task-trash-copy span {
  display: block;
  color: var(--m3-on-surface-variant);
  font: 400 12px/16px var(--font);
}
.task-trash-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 48px 24px;
  color: var(--m3-on-surface-variant);
  text-align: center;
}
.task-trash-empty strong {
  color: var(--m3-on-surface);
  font: 500 16px/24px var(--font);
}
.task-trash-empty p {
  margin: 0;
  font: 400 13px/18px var(--font);
}
.task-trash-empty > svg {
  color: var(--m3-primary);
}
.task-trash-muted {
  margin: 24px 0;
  color: var(--m3-on-surface-variant);
  font: 400 13px/18px var(--font);
}
.task-trash-notice {
  margin: 8px 0;
  color: var(--m3-primary);
  font: 400 13px/18px var(--font);
}
.task-trash-error {
  margin: 8px 0;
  color: var(--m3-error);
  font: 400 13px/18px var(--font);
}
.task-trash-footer {
  flex: 0 0 auto;
  display: flex;
  justify-content: flex-end;
  padding: 12px 24px 20px;
  border-top: 1px solid var(--m3-outline-variant);
}
@media (max-width: 600px) {
  .task-trash-dialog {
    width: 100vw;
    max-width: 100vw;
    height: 100dvh;
    max-height: 100dvh;
    border-radius: 0;
  }
  .task-trash-header {
    padding: 16px;
  }
  .task-trash-body {
    padding: 8px 16px 16px;
  }
  .task-trash-footer {
    padding: 12px 16px 20px;
  }
}
</style>
