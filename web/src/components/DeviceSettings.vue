<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { Monitor, RefreshCw } from 'lucide-vue-next'
import StatusMessage from './StatusMessage.vue'
import { api, failure, type Failure } from '../lib/api'
import { clearSession, notifySessionChange, refreshSession } from '../lib/auth'

interface Device { id: string; device_name: string; created_at: string; last_seen_at: string; expires_at: string; current: boolean }
interface DevicePage { items: Device[]; next_cursor: { before_time: string; before_id: string } | null }
const router = useRouter()
const items = ref<Device[]>([])
const cursor = ref<DevicePage['next_cursor']>(null)
const loading = ref(false)
const loaded = ref(false)
const pending = ref(false)
const error = ref<Failure | null>(null)
const success = ref('')
const selected = ref<Device | null>(null)
const dialog = ref<HTMLDialogElement | null>(null)
function date(value: string) { return new Date(value).toLocaleString('zh-CN', { dateStyle: 'medium', timeStyle: 'short' }) }
async function load(more = false) {
  if (loading.value) return
  loading.value = true; error.value = null
  try {
    const params = more && cursor.value ? `?${new URLSearchParams(cursor.value)}` : ''
    const page = await api<DevicePage>(`/auth/sessions${params}`)
    items.value = more ? [...items.value, ...page.items] : page.items
    cursor.value = page.next_cursor
    loaded.value = true
  } catch (err) {
    error.value = failure(err)
    if (['unauthenticated', 'account_suspended'].includes(error.value.code)) await refreshSession()
  } finally { loading.value = false }
}
onMounted(() => load())
function confirm(device: Device) {
  selected.value = device
  dialog.value?.showModal()
}
async function revoke() {
  if (!selected.value || pending.value) return
  pending.value = true
  try {
    await api(`/auth/sessions/${selected.value.id}`, undefined, 'DELETE')
    notifySessionChange()
    if (selected.value.current) {
      clearSession({ discardLocal: true }); await router.replace('/login')
    } else {
      success.value = '该设备已退出，不能继续访问你的账号。'
      await load()
    }
  } catch (err) {
    error.value = failure(err)
    if (['unauthenticated', 'invalid_csrf', 'account_suspended'].includes(error.value.code)) await refreshSession()
  } finally { pending.value = false; dialog.value?.close(); selected.value = null }
}
</script>

<template>
  <div class="section-intro"><p class="page-description">不认识某台设备？退出它，然后修改密码。</p>
    <button class="button secondary" :disabled="loading || pending" @click="load()"><RefreshCw :size="16" aria-hidden="true" />刷新列表</button>
  </div>
  <StatusMessage :error="error" :success="success" />
  <p class="field-hint">时间按当前设备时区显示。最近活动记录登录和实际保存操作。</p>
  <ul class="device-list" aria-label="已登录的设备">
    <li v-for="device in items" :key="device.id">
      <Monitor class="device-icon" :size="23" :stroke-width="1.75" aria-hidden="true" />
      <div class="device-info">
        <strong>{{ device.device_name }}</strong><span v-if="device.current" class="verified-label">当前设备</span>
        <p>登录于 {{ date(device.created_at) }}</p>
        <p>最近活动 {{ date(device.last_seen_at) }}</p>
        <details><summary>到期时间</summary><p>{{ date(device.expires_at) }}</p></details>
      </div>
      <button class="button secondary" :disabled="pending || loading" :aria-label="device.current ? '退出当前设备' : `退出设备 ${device.device_name}`" @click="confirm(device)">退出</button>
    </li>
  </ul>
  <p v-if="loading" role="status" class="muted">正在读取设备…</p>
  <p v-else-if="loaded && !items.length" class="muted">没有有效的登录设备。</p>
  <button v-if="cursor" class="button secondary" :disabled="loading" @click="load(true)">加载更多设备</button>
  <dialog ref="dialog" aria-labelledby="revoke-title" aria-describedby="revoke-description" @cancel="pending && $event.preventDefault()">
    <h2 id="revoke-title">{{ selected?.current ? '退出当前设备？' : '退出这台设备？' }}</h2>
    <p id="revoke-description" class="wrap-anywhere">{{ selected?.device_name }} 将需要重新登录。设备中尚未同步的操作不会因此保存到服务器。</p>
    <div class="dialog-actions">
      <button class="button secondary" :disabled="pending" autofocus @click="dialog?.close()">取消</button>
      <button class="button" :disabled="pending" @click="revoke">{{ pending ? '正在退出…' : '确认退出' }}</button>
    </div>
  </dialog>
</template>
