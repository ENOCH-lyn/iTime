<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'

const props = withDefaults(defineProps<{
  open: boolean
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'primary' | 'danger'
  busy?: boolean
}>(), {
  confirmLabel: '确定',
  cancelLabel: '取消',
  tone: 'primary',
  busy: false,
})

const emit = defineEmits<{
  'update:open': [value: boolean]
  confirm: []
}>()

const dialog = ref<HTMLDialogElement | null>(null)
const cancelButton = ref<HTMLButtonElement | null>(null)
const titleId = `m3-confirm-${Math.random().toString(36).slice(2, 8)}`

function close() {
  if (props.busy) return
  emit('update:open', false)
}

function confirm() {
  if (props.busy) return
  emit('confirm')
}

watch(() => props.open, async value => {
  await nextTick()
  if (value && !dialog.value?.open) {
    dialog.value?.showModal()
    cancelButton.value?.focus()
  } else if (!value && dialog.value?.open) {
    dialog.value.close()
  }
}, { immediate: true })
</script>

<template>
  <dialog
    ref="dialog"
    class="m3-confirm-dialog"
    :aria-labelledby="titleId"
    @cancel.prevent="close"
    @click.self="close"
  >
    <div class="m3-confirm-body">
      <h2 :id="titleId">{{ title }}</h2>
      <p>{{ message }}</p>
      <div class="m3-confirm-actions">
        <button
          ref="cancelButton"
          type="button"
          class="m3-button m3-button--text"
          :disabled="busy"
          @click="close"
        >
          {{ cancelLabel }}
        </button>
        <button
          type="button"
          class="m3-button"
          :class="tone === 'danger' ? 'm3-button--danger' : 'm3-button--filled'"
          :disabled="busy"
          @click="confirm"
        >
          {{ busy ? '处理中…' : confirmLabel }}
        </button>
      </div>
    </div>
  </dialog>
</template>

<style scoped>
.m3-confirm-dialog {
  inset: 0;
  width: min(420px, calc(100vw - 32px));
  max-width: min(420px, calc(100vw - 32px));
  max-height: calc(100dvh - 32px);
  margin: auto;
  padding: 0;
  overflow: hidden;
  border: 0;
  border-radius: var(--m3-shape-xl);
  background: var(--m3-surface-container-high);
  color: var(--m3-on-surface);
  box-shadow: var(--m3-elevation-3);
}

.m3-confirm-dialog::backdrop {
  background: rgb(0 0 0 / 32%);
}

.m3-confirm-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 24px;
}

.m3-confirm-body h2 {
  margin: 0;
  color: var(--m3-on-surface);
  font: 500 22px/28px var(--font);
  letter-spacing: 0;
}

.m3-confirm-body p {
  margin: 0;
  color: var(--m3-on-surface-variant);
  font: 400 14px/20px var(--font);
  white-space: pre-wrap;
}

.m3-confirm-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}

@media (max-width: 600px) {
  .m3-confirm-dialog {
    width: calc(100vw - 24px);
    max-width: calc(100vw - 24px);
  }
  .m3-confirm-body {
    padding: 20px;
  }
}
</style>
