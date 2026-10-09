<script setup lang="ts">
import { computed } from 'vue'
import { Ellipsis } from 'lucide-vue-next'
import { useRouter } from 'vue-router'
import {
  primaryNavigation,
  secondaryNavigation,
  type ProductArea,
} from '../lib/product-navigation'
import AccountMenu from './AccountMenu.vue'
import SyncStatusIndicator from './SyncStatusIndicator.vue'

const props = defineProps<{
  current: ProductArea | null
  email: string
  pendingLogout: boolean
}>()
const emit = defineEmits<{ logout: [] }>()
const router = useRouter()
const secondaryActive = computed(() => (
  secondaryNavigation.some(item => item.key === props.current)
))

async function openSecondary(command: string | number | object) {
  if (typeof command === 'string') await router.push(command)
}
</script>

<template>
  <aside class="app-rail" aria-label="iTime">
    <ElTooltip content="iTime" placement="right" :show-after="500">
      <RouterLink to="/ai" class="app-rail-brand" aria-label="iTime">
        i<span aria-hidden="true">T</span>
      </RouterLink>
    </ElTooltip>

    <nav class="app-rail-navigation" aria-label="主要功能">
      <div class="app-rail-menu">
        <ElTooltip
          v-for="item in primaryNavigation"
          :key="item.key"
          :content="item.label"
          placement="right"
          :show-after="500"
        >
          <RouterLink
            :to="item.to"
            class="app-rail-link"
            :class="{ selected: current === item.key }"
            :aria-current="current === item.key ? 'page' : undefined"
            :aria-label="item.label"
          >
            <component
              :is="item.icon"
              :size="20"
              :stroke-width="1.8"
              aria-hidden="true"
            />
          </RouterLink>
        </ElTooltip>

        <ElDropdown
          trigger="click"
          placement="right-start"
          @command="openSecondary"
        >
          <ElButton
            text
            circle
            class="app-rail-link"
            :class="{ selected: secondaryActive }"
            aria-label="更多功能"
            :aria-pressed="secondaryActive"
            title="更多"
          >
            <Ellipsis :size="20" :stroke-width="1.8" aria-hidden="true" />
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
      </div>
    </nav>

    <div class="app-rail-bottom">
      <SyncStatusIndicator />
      <AccountMenu
        :email="email"
        :pending="pendingLogout"
        @logout="emit('logout')"
      />
    </div>
  </aside>
</template>
