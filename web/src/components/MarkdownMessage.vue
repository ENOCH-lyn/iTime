<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '../lib/markdown'

const props = defineProps<{ content: string }>()
const rendered = computed(() => {
  try {
    return { html: renderMarkdown(props.content), failed: false }
  } catch {
    return { html: '', failed: true }
  }
})

async function copyCode(event: MouseEvent) {
  const target = event.target
  if (!(target instanceof Element)) return
  const button = target.closest<HTMLButtonElement>('[data-copy-code]')
  const code = button?.closest('.markdown-code-block')?.querySelector('code')
  if (!button || !code) return
  try {
    await navigator.clipboard.writeText(code.textContent ?? '')
    button.textContent = '已复制'
  } catch {
    button.textContent = '复制失败'
  }
  window.setTimeout(() => {
    button.textContent = '复制'
  }, 1500)
}
</script>

<template>
  <p v-if="rendered.failed" class="message-content">{{ content }}</p>
  <div
    v-else
    class="markdown-message"
    @click="copyCode"
    v-html="rendered.html"
  ></div>
</template>
