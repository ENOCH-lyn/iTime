<script setup lang="ts">
import { nextTick, reactive, ref, watch } from 'vue'
import { Folder, Target } from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import { failure, localFailure, type Failure } from '../lib/api'
import {
  createProject,
  getProject,
  updateProject,
  type Project,
  type ProjectKind,
} from '../lib/tasks'

const props = defineProps<{
  open: boolean
  project: Project | null
  initialKind: ProjectKind
}>()

const emit = defineEmits<{
  close: []
  saved: [project: Project]
}>()

const dialog = ref<HTMLDialogElement | null>(null)
const values = reactive<{ kind: ProjectKind; name: string; description: string }>({
  kind: 'project',
  name: '',
  description: '',
})
const pending = ref(false)
const error = ref<Failure | null>(null)
const nameError = ref('')
const latest = ref<Project | null>(null)
let operation: { id: string; fingerprint: string } | null = null

watch(() => props.open, async open => {
  if (!open) {
    dialog.value?.close()
    return
  }
  values.kind = props.project?.kind ?? props.initialKind
  values.name = props.project?.name ?? ''
  values.description = props.project?.description ?? ''
  error.value = null
  nameError.value = ''
  latest.value = null
  operation = null
  await nextTick()
  dialog.value?.showModal()
  document.getElementById('project-name')?.focus()
})

function operationId(fingerprint: string) {
  if (!operation || operation.fingerprint !== fingerprint) {
    operation = { id: crypto.randomUUID(), fingerprint }
  }
  return operation.id
}

function close() {
  if (pending.value) return
  dialog.value?.close()
  emit('close')
}

function applyLatest() {
  if (!latest.value) return
  values.kind = latest.value.kind
  values.name = latest.value.name
  values.description = latest.value.description
  error.value = null
  operation = null
}

async function save() {
  if (pending.value) return
  nameError.value = values.name.trim() ? '' : '请输入项目或目标名称。'
  if (nameError.value) {
    await nextTick()
    document.getElementById('project-name')?.focus()
    return
  }
  error.value = null
  const fields = {
    kind: values.kind,
    name: values.name.trim(),
    description: values.description,
  }
  const fingerprint = JSON.stringify({
    ...fields,
    expected_version: latest.value?.version ?? props.project?.version,
  })
  pending.value = true
  try {
    const saved = props.project
      ? await updateProject(
          props.project.id,
          operationId(fingerprint),
          latest.value?.version ?? props.project.version,
          fields,
        )
      : await createProject(operationId(fingerprint), fields)
    operation = null
    emit('saved', saved)
    close()
  } catch (reason) {
    error.value = failure(reason)
    if (props.project && error.value.code === 'project_version_conflict') {
      try { latest.value = await getProject(props.project.id) }
      catch {
        error.value = localFailure('无法读取服务器最新版。当前填写仍保留，请稍后重试。')
      }
    }
    await nextTick()
    document.querySelector<HTMLElement>('#project-dialog .error-status')?.focus()
  } finally {
    pending.value = false
  }
}
</script>

<template>
  <dialog id="project-dialog" ref="dialog" aria-labelledby="project-dialog-title"
    @cancel.prevent="close">
    <form class="project-form" novalidate @submit.prevent="save">
      <div>
        <h2 id="project-dialog-title">{{ project ? '编辑项目或目标' : `新建${initialKind === 'goal' ? '目标' : '项目'}` }}</h2>
        <p>项目用于归拢相关任务，目标用于表达想达到的结果。</p>
      </div>
      <StatusMessage :error="error" />
      <div v-if="latest" class="conflict-comparison">
        <strong>服务器最新版</strong>
        <p>{{ latest.kind === 'goal' ? '目标' : '项目' }} · {{ latest.name }} · 版本 {{ latest.version }}</p>
        <div class="inline-actions">
          <button type="button" class="text-button" @click="error = null">保留填写并基于最新版重试</button>
          <button type="button" class="text-button" @click="applyLatest">改用服务器内容</button>
        </div>
      </div>
      <fieldset class="segmented-field">
        <legend>类型</legend>
        <label :class="{ selected: values.kind === 'project' }">
          <input v-model="values.kind" type="radio" value="project" :disabled="pending" />
          <Folder :size="16" aria-hidden="true" />项目
        </label>
        <label :class="{ selected: values.kind === 'goal' }">
          <input v-model="values.kind" type="radio" value="goal" :disabled="pending" />
          <Target :size="16" aria-hidden="true" />目标
        </label>
      </fieldset>
      <div class="field">
        <label for="project-name">名称</label>
        <input id="project-name" v-model="values.name" maxlength="120" :disabled="pending"
          :aria-invalid="!!nameError" :aria-describedby="nameError ? 'project-name-error' : undefined" />
        <p v-if="nameError" id="project-name-error" class="field-error">{{ nameError }}</p>
      </div>
      <div class="field">
        <label for="project-description">说明 <span class="muted">可选</span></label>
        <textarea id="project-description" v-model="values.description" maxlength="20000"
          rows="4" :disabled="pending"></textarea>
      </div>
      <div class="dialog-actions">
        <button type="button" class="button secondary" :disabled="pending" @click="close">取消</button>
        <button type="submit" class="button" :disabled="pending">
          {{ pending ? '正在保存…' : project ? '保存修改' : '创建' }}
        </button>
      </div>
    </form>
  </dialog>
</template>
