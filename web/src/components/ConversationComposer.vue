<script setup lang="ts">
import { computed, ref } from 'vue'
import type {
  UploadFile,
  UploadFiles,
  UploadInstance,
} from 'element-plus'
import {
  FileUp,
  Paperclip,
  SendHorizontal,
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

const canSubmit = computed(() => !!model.value.trim() || !!attachment.value)
const characterCount = computed(() => Array.from(model.value).length)
const countVisible = computed(() => characterCount.value >= 9600)

function submit() {
  if (props.pending || props.disabled || !canSubmit.value) return
  emit('submit')
}

function selectAttachment(uploadFile: UploadFile, _files: UploadFiles) {
  const selected = uploadFile.raw
  if (!selected) return
  const name = selected.name.toLocaleLowerCase()
  const supported = (
    ['image/png', 'image/jpeg', 'image/webp'].includes(selected.type)
    || name.endsWith('.xlsx')
    || name.endsWith('.ics')
  )
  const maximum = name.endsWith('.ics') ? 2 * 1024 * 1024 : 5 * 1024 * 1024
  if (!supported) {
    upload.value?.clearFiles()
    emit('invalidAttachment', '仅支持 PNG、JPEG、WebP、XLSX 或 ICS。')
    return
  }
  if (!selected.size || selected.size > maximum) {
    upload.value?.clearFiles()
    emit(
      'invalidAttachment',
      name.endsWith('.ics') ? 'ICS 文件不能超过 2 MiB。' : '附件不能超过 5 MiB。',
    )
    return
  }
  attachment.value = selected
}

function removeAttachment() {
  attachment.value = null
  upload.value?.clearFiles()
}

</script>

<template>
  <form class="conversation-composer" @submit.prevent="submit">
    <div v-if="attachment" class="composer-attachment" aria-label="待发送附件">
      <FileUp :size="16" aria-hidden="true" />
      <span class="composer-attachment-name">{{ attachment.name }}</span>
      <ElButton
        text
        circle
        aria-label="移除附件"
        title="移除附件"
        :disabled="pending"
        @click="removeAttachment"
      >
        <X :size="16" aria-hidden="true" />
      </ElButton>
    </div>
    <label for="conversation-message" class="sr-only">消息</label>
    <ElInput id="conversation-message" v-model="model" type="textarea"
      :autosize="{ minRows: 1, maxRows: 8 }" maxlength="12000"
      :disabled="disabled || active" :placeholder="archived
        ? '重新打开会话后可继续'
        : aiFrozen
          ? 'AI 功能已暂停'
          : active
            ? '等待当前回复完成'
            : '告诉 iTime 你想安排什么…'"
    />
    <div class="composer-actions">
      <div class="composer-secondary-actions">
        <ElUpload
          ref="upload"
          :auto-upload="false"
          :limit="1"
          :show-file-list="false"
          accept=".xlsx,.ics,image/png,image/jpeg,image/webp"
          :disabled="disabled || pending || active || !!attachment"
          :on-change="selectAttachment"
        >
          <ElTooltip content="添加附件" placement="top" :show-after="500">
            <span class="composer-attachment-trigger">
              <Paperclip :size="18" aria-hidden="true" />
              <span class="sr-only">添加附件</span>
            </span>
          </ElTooltip>
        </ElUpload>
        <span v-if="countVisible">{{ characterCount }}/12000</span>
      </div>
      <ElButton
        v-if="active"
        type="primary"
        class="composer-send"
        aria-label="取消生成"
        @click="emit('cancel')"
      >
        <Square :size="17" aria-hidden="true" />
        <span>停止</span>
      </ElButton>
      <ElButton v-else type="primary" class="composer-send"
        :loading="pending"
        :disabled="pending || disabled || !canSubmit"
        aria-label="发送消息"
        @click="submit">
        <SendHorizontal :size="18" aria-hidden="true" />
        <span>{{ pending ? '正在发送…' : '发送' }}</span>
      </ElButton>
    </div>
  </form>
</template>
