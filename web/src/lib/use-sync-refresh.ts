import { onBeforeUnmount, onMounted } from 'vue'
import type { SyncEvent } from './sync'

interface SyncEventDetail {
  events: SyncEvent[]
  reset_required: boolean
}

export function useSyncRefresh(
  entityTypes: readonly string[],
  refresh: (events: SyncEvent[]) => Promise<void> | void,
) {
  const accepted = new Set(entityTypes)
  let timer = 0
  let pending: SyncEvent[] = []

  function flush() {
    timer = 0
    const events = pending
    pending = []
    void refresh(events)
  }

  function handle(event: Event) {
    const detail = (event as CustomEvent<SyncEventDetail>).detail
    if (!detail) return
    const relevant = detail.events.filter(item => accepted.has(item.entity_type))
    if (!detail.reset_required && !relevant.length) return
    pending.push(...relevant)
    if (timer) window.clearTimeout(timer)
    timer = window.setTimeout(flush, 100)
  }

  onMounted(() => window.addEventListener('itime:sync-events', handle))
  onBeforeUnmount(() => {
    window.removeEventListener('itime:sync-events', handle)
    if (timer) window.clearTimeout(timer)
  })
}
