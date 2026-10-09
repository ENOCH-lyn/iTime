<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { Ellipsis } from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import {
  primaryNavigation,
  secondaryNavigation,
  type ProductArea,
} from '../lib/product-navigation'
import SyncStatusIndicator from './SyncStatusIndicator.vue'

const props = defineProps<{
  current: ProductArea
}>()

const navigation = ref<HTMLElement | null>(null)
const router = useRouter()
const secondaryActive = computed(() => (
  secondaryNavigation.some(item => item.key === props.current)
))

async function revealCurrent() {
  await nextTick()
  const container = navigation.value
  const current = container?.querySelector<HTMLElement>('[aria-current="page"]')
  if (!container || !current || container.scrollWidth <= container.clientWidth) return
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

async function openSecondary(command: string | number | object) {
  if (typeof command === 'string') await router.push(command)
}
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
    <ElDropdown
      trigger="click"
      placement="bottom-start"
      @command="openSecondary"
    >
      <ElButton
        text
        class="nav-link product-more-trigger"
        :class="{ selected: secondaryActive }"
        aria-label="更多功能"
        :aria-pressed="secondaryActive"
      >
        <Ellipsis :size="18" :stroke-width="1.8" aria-hidden="true" />
        <span class="product-label">更多</span>
      </ElButton>
      <template #dropdown>
        <ElDropdownMenu>
          <ElDropdownItem
            v-for="item in secondaryNavigation"
            :key="item.key"
            :command="item.to"
            :class="{ selected: current === item.key }"
          >
            <component
              :is="item.icon"
              :size="16"
              :stroke-width="1.8"
              aria-hidden="true"
            />
            {{ item.label }}
          </ElDropdownItem>
        </ElDropdownMenu>
      </template>
    </ElDropdown>
    <SyncStatusIndicator />
  </nav>
</template>
