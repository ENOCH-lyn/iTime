<script setup lang="ts">
import { ref, watch } from 'vue'
import { Eye, EyeOff } from 'lucide-vue-next'

const props = defineProps<{
  id: string
  label: string
  type?: 'text' | 'email' | 'password' | 'number' | 'date'
  autocomplete?: string
  inputmode?: 'text' | 'numeric' | 'decimal'
  maxlength?: number
  min?: number
  max?: number
  step?: number
  readonly?: boolean
  hint?: string
  error?: string
  disabled?: boolean
}>()
const model = defineModel<string>({ required: true })
defineEmits<{ blur: [] }>()
const visible = ref(false)
const feedbackShown = ref(Boolean(props.hint || props.error))
watch(() => [props.hint, props.error], values => {
  if (values.some(Boolean)) feedbackShown.value = true
})
</script>

<template>
  <div class="field">
    <label :for="id">{{ label }}</label>
    <div class="input-wrap">
      <input :id="id" v-model="model" :name="id" :type="type === 'password' && visible ? 'text' : (type ?? 'text')"
        :autocomplete="autocomplete" :inputmode="inputmode" :maxlength="maxlength" :min="min" :max="max"
        :step="step" :readonly="readonly" :disabled="disabled"
        :spellcheck="false" :aria-invalid="!!error"
        :aria-describedby="[hint ? `${id}-hint` : '', error ? `${id}-error` : ''].filter(Boolean).join(' ') || undefined"
        :class="{ 'password-input': type === 'password' }" @blur="$emit('blur')" />
      <button v-if="type === 'password'" type="button" class="reveal" :aria-label="`${visible ? '隐藏' : '显示'}${label}`"
        :aria-pressed="visible" :disabled="disabled" @click="visible = !visible">
        <EyeOff v-if="visible" :size="18" aria-hidden="true" /><Eye v-else :size="18" aria-hidden="true" />
      </button>
    </div>
    <p v-if="feedbackShown" :id="error ? `${id}-error` : `${id}-hint`"
      :class="error ? 'field-error' : 'field-hint'">{{ error || hint || '\u00a0' }}</p>
  </div>
</template>
