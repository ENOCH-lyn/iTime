<script setup lang="ts">
import { ChevronDown, LoaderCircle, Sparkles, Check } from 'lucide-vue-next'
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  reasoning: string
  steps: string[]
  running: boolean
}>()

const open = ref(props.running)

const parsed = computed(() => {
  const lines = props.reasoning.split('\n')
  const steps: string[] = []
  const body: string[] = []
  for (const line of lines) {
    const trimmed = line.trim()
    if (trimmed.startsWith('›')) {
      const label = trimmed.replace(/^›\s*/, '').trim()
      if (label) steps.push(label)
    } else {
      body.push(line)
    }
  }
  return { steps, body: body.join('\n').trim() }
})

const steps = computed(() => (
  props.steps.length ? props.steps : parsed.value.steps
))
const text = computed(() => parsed.value.body)
const hasContent = computed(() => !!text.value || steps.value.length > 0)

watch(
  () => props.running,
  (running) => {
    open.value = running
  },
)
</script>

<template>
  <section v-if="hasContent" class="thinking-trace" aria-label="思考过程">
    <button
      type="button"
      class="thinking-head"
      :aria-expanded="open"
      @click="open = !open"
    >
      <LoaderCircle
        v-if="running"
        :size="15"
        class="spin thinking-icon thinking-icon--live"
        aria-hidden="true"
      />
      <Sparkles v-else :size="15" class="thinking-icon" aria-hidden="true" />
      <span class="thinking-title">{{ running ? '正在思考' : '思考过程' }}</span>
      <span v-if="steps.length" class="thinking-meta">{{ steps.length }} 个步骤</span>
      <ChevronDown
        :size="15"
        class="thinking-chevron"
        :class="{ 'thinking-chevron--open': open }"
        aria-hidden="true"
      />
    </button>
    <div v-show="open" class="thinking-body">
      <ol v-if="steps.length" class="thinking-steps">
        <li v-for="(step, index) in steps" :key="`${index}-${step}`">
          <Check
            :size="13"
            class="thinking-step-check"
            :class="{ 'thinking-step-check--live': running && index === steps.length - 1 }"
            aria-hidden="true"
          />
          <span>{{ step }}</span>
        </li>
      </ol>
      <p v-if="text" class="thinking-text">{{ text }}</p>
    </div>
  </section>
</template>

<style scoped>
.thinking-trace {
  margin: 0 0 10px;
  padding: 2px 0 2px 10px;
  border-left: 2px solid var(--m3-outline-variant);
}
.thinking-head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 2px 0;
  border: 0;
  background: none;
  color: var(--m3-on-surface-variant);
  font: 500 12.5px/20px var(--font);
  letter-spacing: 0.2px;
  text-align: left;
  cursor: pointer;
}
.thinking-head:hover {
  color: var(--m3-on-surface);
}
.thinking-icon {
  flex: 0 0 auto;
  color: var(--m3-primary);
}
.thinking-title {
  flex: 0 0 auto;
}
.thinking-meta {
  flex: 1 1 auto;
  color: var(--m3-on-surface-variant);
  font-weight: 400;
}
.thinking-chevron {
  flex: 0 0 auto;
  transition: transform 160ms ease;
}
.thinking-chevron--open {
  transform: rotate(180deg);
}
.thinking-body {
  display: grid;
  gap: 8px;
  padding: 4px 0 6px;
}
.thinking-steps {
  display: grid;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.thinking-steps li {
  display: flex;
  align-items: flex-start;
  gap: 7px;
  color: var(--m3-on-surface-variant);
  font: 400 12.5px/18px var(--font);
}
.thinking-step-check {
  flex: 0 0 auto;
  margin-top: 2px;
  color: var(--m3-primary);
}
.thinking-step-check--live {
  opacity: 0.45;
}
.thinking-text {
  margin: 0;
  color: var(--m3-on-surface-variant);
  font: 400 12.5px/19px var(--font);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
</style>
