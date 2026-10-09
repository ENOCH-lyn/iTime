<script setup lang="ts">
import FocusHistory from './FocusHistory.vue'
import type { FocusSession } from '../lib/execution'

defineProps<{
  items: FocusSession[]
  selectedId: string | null
  loading: boolean
}>()

const emit = defineEmits<{
  select: [item: FocusSession]
  revise: [item: FocusSession]
}>()
</script>

<template>
  <nav class="secondary-nav focus-sidebar-nav" aria-label="专注">
    <FocusHistory
      :items="items"
      :selected-id="selectedId"
      :loading="loading"
      @select="emit('select', $event)"
      @revise="emit('revise', $event)"
    />
  </nav>
</template>
