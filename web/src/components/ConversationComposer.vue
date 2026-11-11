<script setup lang="ts">
import { computed, ref } from 'vue'
import type {
  UploadFile,
  UploadFiles,
  UploadInstance,
} from 'element-plus'
import {
  ArrowUp,
  FileUp,
  Paperclip,
  Square,
  X,
} from 'lucide-vue-next'

const props = defineProps<{
  pending: boolean
  disabled: boolean
  archived: boolean
  aiFrozen: boolean
  active: boolean
}>()
const model = defineModel<string>({ required: true })
const attachment = defineModel<File | null>('attachment', { required: true })
const emit = defineEmits<{
  submit: []
  cancel: []
  invalidAttachment: [message: string]
}>()
const upload = ref<UploadInstance | null>(null)
const DOCUMENT_EXTENSIONS = ['.pdf', '.docx', '.txt', '.md', '.markdown', '.csv']
const CALENDAR_EXTENSIONS = ['.xlsx', '.ics']

const canSubmit = computed(() => !!model.value.trim() || !!attachment.value)
const characterCount = computed(() => Array.from(model.value).length)
const countVisible = computed(() => characterCount.value >= 9600)
const placeholder = computed(() => (
  props.archived
    ? '重新打开会话后可继续'
    : props.aiFrozen
      ? 'AI 功能已暂停'
      : props.active
        ? '等待当前回复完成'
        : '告诉 iTime 你想安排什么'
))

function submit() {
  if (props.pending || props.disabled || !canSubmit.value) return
  emit('submit')
}

function extensionOf(name: string) {
  const index = name.lastIndexOf('.')
  return index >= 0 ? name.slice(index).toLocaleLowerCase() : ''
}

function acceptFile(selected: File) {
  const name = selected.name.toLocaleLowerCase()
  const extension = extensionOf(name)
  const isImage = ['image/png', 'image/jpeg', 'image/webp'].includes(selected.type)
  const isDocument = DOCUMENT_EXTENSIONS.includes(extension)
  const isCalendar = CALENDAR_EXTENSIONS.includes(extension)
  if (extension === '.doc') {
    upload.value?.clearFiles()
    emit('invalidAttachment', '旧版 .doc 请另存为 .docx 后再上传。')
    return
  }
  if (!isImage && !isDocument && !isCalendar) {
    upload.value?.clearFiles()
    emit('invalidAttachment', '支持 PDF、DOCX、TXT、MD、CSV、图片、XLSX、ICS。')
    return
  }
  const maximum = extension === '.ics'
    ? 2 * 1024 * 1024
    : isDocument
      ? 10 * 1024 * 1024
      : 5 * 1024 * 1024
  if (!selected.size || selected.size > maximum) {
    upload.value?.clearFiles()
    emit(
      'invalidAttachment',
      extension === '.ics'
        ? 'ICS 文件不能超过 2 MB。'
        : isDocument
          ? '文档不能超过 10 MB。'
          : '附件不能超过 5 MB。',
    )
    return
  }
  attachment.value = selected
}

function selectAttachment(uploadFile: UploadFile, _files: UploadFiles) {
  const selected = uploadFile.raw
  if (selected) acceptFile(selected)
}

function onPaste(event: ClipboardEvent) {
  if (props.disabled || props.pending || props.active || attachment.value) return
  const selected = event.clipboardData?.files?.[0]
  if (!selected) return
  event.preventDefault()
  acceptFile(selected)
}

function removeAttachment() {
  attachment.value = null
  upload.value?.clearFiles()
}
</script>

<template>
  <form class="composer-dock" @submit.prevent="submit" @paste="onPaste">
    <div
      class="composer-field"
      :class="{
        'composer-field--active': active,
        'composer-field--disabled': disabled || aiFrozen || archived,
      }"
    >
      <div v-if="attachment" class="composer-attachment" aria-label="待发送附件">
        <FileUp :size="15" aria-hidden="true" />
        <span class="composer-attachment-name">{{ attachment.name }}</span>
        <button
          type="button"
          class="composer-icon-button"
          aria-label="移除附件"
          title="移除附件"
          :disabled="pending"
          @click="removeAttachment"
        >
          <X :size="15" aria-hidden="true" />
        </button>
      </div>
      <label for="conversation-message" class="sr-only">消息</label>
      <ElInput
        id="conversation-message"
        v-model="model"
        class="composer-input"
        type="textarea"
        :autosize="{ minRows: 1, maxRows: 8 }"
        maxlength="12000"
        :disabled="disabled || active"
        :placeholder="placeholder"
      />
      <div class="composer-tools">
        <div class="composer-tools-left">
          <ElUpload
            ref="upload"
            :auto-upload="false"
            :limit="1"
            :show-file-list="false"
            accept=".pdf,.docx,.txt,.md,.markdown,.csv,.xlsx,.ics,image/png,image/jpeg,image/webp"
            :disabled="disabled || pending || active || !!attachment"
            :on-change="selectAttachment"
          >
            <ElTooltip content="添加 PDF、Word、文本、图片、XLSX 或 ICS（也可直接粘贴）" placement="top" :show-after="400">
              <span class="composer-icon-button composer-attach-trigger" title="添加文件">
                <Paperclip :size="17" aria-hidden="true" />
                <span class="sr-only">添加附件</span>
              </span>
            </ElTooltip>
          </ElUpload>
          <span v-if="countVisible" class="composer-count">{{ characterCount }}/12000</span>
        </div>
        <div class="composer-tools-right">
          <span class="composer-mode" aria-hidden="true">
            <span class="composer-mode-dot"></span>
            先思考再给方案
          </span>
          <ElButton
            v-if="active"
            circle
            type="primary"
            class="composer-send"
            aria-label="停止生成"
            title="停止生成"
            @click="emit('cancel')"
          >
            <Square :size="15" aria-hidden="true" />
          </ElButton>
          <ElButton
            v-else
            circle
            type="primary"
            class="composer-send"
            :loading="pending"
            :disabled="pending || disabled || !canSubmit"
            aria-label="发送消息"
            title="发送消息"
            @click="submit"
          >
            <ArrowUp :size="18" aria-hidden="true" />
          </ElButton>
        </div>
      </div>
    </div>
    <p class="composer-footnote" aria-hidden="true">
      Enter 发送 · Shift+Enter 换行 · 可粘贴截图或文件
    </p>
  </form>
</template>

<style scoped>
.composer-dock {
  grid-area: composer;
  width: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 18px clamp(16px, 4vw, 32px) max(20px, env(safe-area-inset-bottom));
}

.composer-field {
  width: min(760px, 100%);
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 10px 10px 10px 16px;
  border: 1px solid var(--m3-outline-variant);
  border-radius: 26px;
  background: var(--m3-surface-container-low);
  box-shadow: 0 1px 2px rgb(0 0 0 / 4%);
  transition: border-color 150ms ease, box-shadow 150ms ease, background 150ms ease;
}

.composer-field:focus-within {
  border-color: var(--m3-primary);
  background: var(--m3-surface-container-lowest);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--m3-primary) 14%, transparent);
}

.composer-field--active {
  border-color: var(--m3-primary);
}

.composer-field--disabled {
  opacity: 0.72;
}

.composer-attachment {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0 0 6px;
  padding: 6px 6px 6px 10px;
  border-radius: 14px;
  background: var(--m3-surface-container-high);
  color: var(--m3-on-surface);
  font: 400 13px/18px var(--font);
}

.composer-attachment-name {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-input {
  width: 100%;
}

:deep(.composer-input .el-textarea__inner) {
  min-height: 28px !important;
  padding: 4px 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-shadow: none !important;
  color: var(--m3-on-surface);
  font: 400 15px/24px var(--font);
  resize: none;
}

:deep(.composer-input .el-textarea__inner::placeholder) {
  color: var(--m3-on-surface-variant);
}

.composer-tools {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding-top: 2px;
}

.composer-tools-left,
.composer-tools-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.composer-icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: transparent;
  color: var(--m3-on-surface-variant);
  cursor: pointer;
  transition: background 120ms ease, color 120ms ease;
}

.composer-icon-button:hover:not(:disabled) {
  background: var(--m3-surface-container-high);
  color: var(--m3-on-surface);
}

.composer-icon-button:disabled {
  opacity: 0.45;
  cursor: default;
}

.composer-count {
  color: var(--m3-on-surface-variant);
  font: 400 12px/16px var(--font);
  font-variant-numeric: tabular-nums;
}

.composer-mode {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding-right: 2px;
  color: var(--m3-on-surface-variant);
  font: 400 12px/16px var(--font);
  white-space: nowrap;
}

.composer-mode-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--m3-primary);
}

.composer-send.el-button {
  width: 36px;
  height: 36px;
  min-height: 36px;
  margin: 0;
  padding: 0;
  border: 0;
  border-radius: 50%;
  background: var(--m3-primary);
  color: var(--m3-on-primary);
}

.composer-send.el-button:hover:not(.is-disabled) {
  background: var(--m3-primary);
  filter: brightness(1.06);
}

.composer-send.el-button.is-disabled {
  background: var(--m3-surface-container-highest);
  color: var(--m3-on-surface-variant);
}

.composer-footnote {
  width: min(760px, 100%);
  margin: 0;
  padding-left: 16px;
  color: var(--m3-on-surface-variant);
  font: 400 11.5px/16px var(--font);
}

@media (max-width: 600px) {
  .composer-dock {
    padding: 12px 12px max(16px, env(safe-area-inset-bottom));
  }
  .composer-field {
    border-radius: 22px;
    padding: 8px 8px 8px 14px;
  }
  .composer-mode {
    display: none;
  }
  .composer-footnote {
    display: none;
  }
}
</style>
