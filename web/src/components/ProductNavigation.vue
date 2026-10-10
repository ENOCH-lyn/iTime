<script setup lang="ts">
import { nextTick, onMounted, ref, watch } from 'vue'
import {
  primaryNavigation,
  type ProductArea,
} from '../lib/product-navigation'
import SyncStatusIndicator from './SyncStatusIndicator.vue'

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
  <nav ref="navigation" class="product-navigation" aria-label="产品">
    <ElMenu
      class="product-menu"
      :default-active="primaryNavigation.find(item => item.key === current)?.to"
      router
    >
      <ElMenuItem
        v-for="item in primaryNavigation"
        :key="item.key"
        :index="item.to"
        class="nav-link"
        :class="{ selected: current === item.key }"
        :aria-current="current === item.key ? 'page' : undefined"
        :aria-label="item.label"
      >
        <component
          :is="item.icon"
          :size="18"
          :stroke-width="1.8"
          aria-hidden="true"
        />
        <span class="product-label">{{ item.label }}</span>
      </ElMenuItem>
    </ElMenu>
    <SyncStatusIndicator />
  </nav>
</template>
