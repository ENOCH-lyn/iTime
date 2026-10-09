<script setup lang="ts">
import { CircleHelp, LogOut, Settings, UserRound } from 'lucide-vue-next'
import { useRouter } from 'vue-router'

defineProps<{
  email: string
  pending: boolean
}>()
const emit = defineEmits<{ logout: [] }>()
const router = useRouter()

async function handleCommand(command: string | number | object) {
  if (command === 'settings') await router.push('/settings')
  if (command === 'help') await router.push('/help')
  if (command === 'logout') emit('logout')
}
</script>

<template>
  <ElDropdown
    trigger="click"
    placement="right-end"
    @command="handleCommand"
  >
    <ElButton
      text
      circle
      class="account-menu-trigger"
      :aria-label="`账号菜单 ${email}`"
      title="账号"
    >
      <UserRound :size="20" :stroke-width="1.8" aria-hidden="true" />
    </ElButton>
    <template #dropdown>
      <ElDropdownMenu>
        <ElDropdownItem command="settings">
          <Settings :size="16" aria-hidden="true" />
          设置
        </ElDropdownItem>
        <ElDropdownItem command="help">
          <CircleHelp :size="16" aria-hidden="true" />
          帮助
        </ElDropdownItem>
        <ElDropdownItem command="logout" divided :disabled="pending">
          <LogOut :size="16" aria-hidden="true" />
          {{ pending ? '正在退出…' : '退出登录' }}
        </ElDropdownItem>
      </ElDropdownMenu>
    </template>
  </ElDropdown>
</template>
