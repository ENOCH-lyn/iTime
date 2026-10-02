<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { Check, Copy, Download, ShieldCheck } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  codes: string[]
  email: string
  actionLabel?: string
  primary?: boolean
}>(), { actionLabel: '继续', primary: false })
const emit = defineEmits<{ done: [] }>()
const acknowledged = ref(false)
const copied = ref(false)
const copyError = ref(false)

function content() {
  return [
    'iTime 管理员恢复码',
    `账号：${props.email}`,
    '每个恢复码只能使用一次。请离线保管。',
    '',
    ...props.codes,
    '',
  ].join('\n')
}
async function copy() {
  copied.value = false
  copyError.value = false
  try {
    await navigator.clipboard.writeText(content())
    copied.value = true
  } catch {
    copyError.value = true
  }
}
function download() {
  const blob = new Blob([content()], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `itime-admin-recovery-${props.email.replace(/[^a-z0-9]+/gi, '-')}.txt`
  link.click()
  URL.revokeObjectURL(url)
  document.getElementById('recovery-title')?.focus()
}
function preventClose(event: BeforeUnloadEvent) {
  if (!acknowledged.value) event.preventDefault()
}
onMounted(() => window.addEventListener('beforeunload', preventClose))
onUnmounted(() => window.removeEventListener('beforeunload', preventClose))
onBeforeRouteLeave(() => acknowledged.value || window.confirm('恢复码只显示这一次。确认已保存并离开？'))
</script>

<template>
  <section class="recovery-result" aria-labelledby="recovery-title">
    <ShieldCheck :size="30" aria-hidden="true" />
    <component :is="primary ? 'h1' : 'h2'" id="recovery-title"
      data-recovery-title tabindex="-1">保存恢复码</component>
    <p>身份验证器不可用时，可用一个恢复码登录。每个码只能使用一次。</p>
    <ol class="recovery-code-list">
      <li v-for="code in codes" :key="code"><code>{{ code }}</code></li>
    </ol>
    <div class="recovery-actions">
      <button type="button" class="button secondary" @click="copy">
        <Check v-if="copied" :size="17" aria-hidden="true" />
        <Copy v-else :size="17" aria-hidden="true" />{{ copied ? '已复制' : '复制全部' }}
      </button>
      <button type="button" class="button secondary" @click="download">
        <Download :size="17" aria-hidden="true" />下载文本
      </button>
    </div>
    <p v-if="copyError" class="field-error" role="alert">浏览器未允许复制，请下载文本或手动保存。</p>
    <label class="checkbox-label recovery-confirm">
      <input v-model="acknowledged" type="checkbox" />我已将恢复码保存在安全的离线位置
    </label>
    <button type="button" class="button full-width" :disabled="!acknowledged" @click="$emit('done')">
      {{ actionLabel }}
    </button>
  </section>
</template>
