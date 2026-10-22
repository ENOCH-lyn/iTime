<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import {
  primaryNavigation,
  type ProductArea,
} from '../lib/product-navigation'

const props = defineProps<{
  current: ProductArea
}>()

const navigation = ref<HTMLElement | null>(null)

async function revealCurrent() {
  await nextTick()
  const container = navigation.value
  const current = container?.querySelector<HTMLElement>('[aria-current="page"]')
  if (!container || !current) return
  if (container.scrollWidth <= container.clientWidth) return
  const containerBox = container.getBoundingClientRect()
  const currentBox = current.getBoundingClientRect()
  container.scrollTo({
    left: Math.max(
      0,
      container.scrollLeft
      + currentBox.left
      - containerBox.left
      - (container.clientWidth - currentBox.width) / 2,
    ),
    behavior: 'auto',
  })
}

onMounted(revealCurrent)
watch(() => props.current, revealCurrent)
</script>

<template>
  <nav ref="navigation" class="m3-navigation" aria-label="产品">
    <RouterLink
      v-for="item in primaryNavigation"
      :key="item.key"
      :to="item.to"
      :class="{ active: current === item.key }"
      :aria-current="current === item.key ? 'page' : undefined"
      :aria-label="item.label"
    >
      <span class="m3-navigation__icon" aria-hidden="true">
        <span class="material-symbols-rounded">{{ item.symbol }}</span>
      </span>
      <span class="m3-navigation__label">{{ item.label }}</span>
    </RouterLink>
  </nav>
</template>
