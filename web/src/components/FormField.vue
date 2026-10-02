<script setup lang="ts">
import { ref } from 'vue'
import { Eye, EyeOff } from 'lucide-vue-next'

defineProps<{
  id: string
  label: string
  type?: 'text' | 'email' | 'password'
  autocomplete?: string
  hint?: string
  error?: string
  disabled?: boolean
}>()
const model = defineModel<string>({ required: true })
defineEmits<{ blur: [] }>()
const visible = ref(false)
</script>

<template>
  <div class="field">
    <label :for="id">{{ label }}</label>
    <div class="input-wrap">
      <input :id="id" v-model="model" :name="id" :type="type === 'password' && visible ? 'text' : (type ?? 'text')"
        :autocomplete="autocomplete" :disabled="disabled" :aria-invalid="!!error"
        :aria-describedby="[hint ? `${id}-hint` : '', error ? `${id}-error` : ''].filter(Boolean).join(' ') || undefined"
        :class="{ 'password-input': type === 'password' }" @blur="$emit('blur')" />
      <button v-if="type === 'password'" type="button" class="reveal" :aria-label="`${visible ? '隐藏' : '显示'}${label}`"
        :aria-pressed="visible" :disabled="disabled" @click="visible = !visible">
        <EyeOff v-if="visible" :size="18" aria-hidden="true" /><Eye v-else :size="18" aria-hidden="true" />
      </button>
    </div>
    <p v-if="hint" :id="`${id}-hint`" class="field-hint">{{ hint }}</p>
    <p v-if="error" :id="`${id}-error`" class="field-error">{{ error }}</p>
  </div>
</template>
