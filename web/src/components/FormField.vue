<script setup lang="ts">
import { ref } from 'vue'
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
</script>

<template>
  <ElFormItem
    class="field itime-form-field"
    :label="label"
    :label-for="id"
    :error="error"
  >
    <ElInput
      :id="id"
      v-model="model"
      :name="id"
      :type="type === 'password' && visible ? 'text' : (type ?? 'text')"
      :autocomplete="autocomplete"
      :inputmode="inputmode"
      :maxlength="maxlength"
      :min="min"
      :max="max"
      :step="step"
      :readonly="readonly"
      :disabled="disabled"
      :spellcheck="false"
      :aria-invalid="!!error"
      :aria-describedby="hint && !error ? `${id}-hint` : undefined"
      @blur="$emit('blur')"
    >
      <template v-if="type === 'password'" #suffix>
        <ElButton
          text
          circle
          class="password-reveal"
          :aria-label="`${visible ? '隐藏' : '显示'}${label}`"
          :aria-pressed="visible"
          :disabled="disabled"
          @click="visible = !visible"
        >
          <EyeOff v-if="visible" :size="18" aria-hidden="true" />
          <Eye v-else :size="18" aria-hidden="true" />
        </ElButton>
      </template>
    </ElInput>
    <p v-if="hint && !error" :id="`${id}-hint`" class="field-hint">{{ hint }}</p>
  </ElFormItem>
</template>
