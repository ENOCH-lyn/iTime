<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { CalendarRange, Check, LoaderCircle, X } from 'lucide-vue-next'
import { failure } from '../lib/api'
import {
  applyToolProposal,
  getToolProposal,
  rejectToolProposal,
  type ToolProposal,
} from '../lib/tool-proposals'

const props = defineProps<{ proposalId: string }>()
const emit = defineEmits<{ applied: [] }>()
const proposal = ref<ToolProposal | null>(null)
const loading = ref(false)
const pending = ref(false)
const error = ref('')

interface CourseRow {
  name: string
  teacher?: string
  location?: string
  weekday: number
  starts_at: string
  ends_at: string
  week_numbers?: number[]
}

const courses = computed<CourseRow[]>(() => {
  const raw = proposal.value?.payload?.courses
  return Array.isArray(raw) ? (raw as CourseRow[]) : []
})
const summary = computed(() => {
  const raw = (proposal.value?.preview.summary ?? '').replace(/\\n/g, '\n')
  return raw
    .split('\n')
    .filter((line) => !line.trim().startsWith('|'))
    .join('\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
})
const applied = computed(() => proposal.value?.status === 'applied')
const statusLabel = computed(() => {
  if (!proposal.value) return ''
  if (proposal.value.status === 'pending') return '待确认'
  if (proposal.value.status === 'applied') return '已写入课表'
  if (proposal.value.status === 'rejected') return '已放弃，不会写入课表'
  return '已过期'
})
const weekdayLabels = ['', '一', '二', '三', '四', '五', '六', '日']
const time = (value: string) => String(value).slice(0, 5)

function weeksLabel(row: CourseRow) {
  const numbers = row.week_numbers ?? []
  if (!numbers.length) return ''
  return `第 ${numbers[0]}-${numbers[numbers.length - 1]} 周`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    proposal.value = await getToolProposal(props.proposalId)
  } catch (reason) {
    error.value = failure(reason).message
  } finally {
    loading.value = false
  }
}

async function apply() {
  if (!proposal.value || pending.value) return
  pending.value = true
  error.value = ''
  try {
    proposal.value = (await applyToolProposal(proposal.value)).proposal
    emit('applied')
  } catch (reason) {
    error.value = failure(reason).message
    await load()
  } finally {
    pending.value = false
  }
}

async function reject() {
  if (!proposal.value || pending.value) return
  pending.value = true
  error.value = ''
  try {
    proposal.value = (await rejectToolProposal(proposal.value)).proposal
    emit('applied')
  } catch (reason) {
    error.value = failure(reason).message
  } finally {
    pending.value = false
  }
}

onMounted(load)
watch(() => props.proposalId, load)
</script>

<template>
  <section class="timetable-card" aria-label="课表草稿">
    <div v-if="loading && !proposal" class="timetable-loading" role="status">
      <LoaderCircle :size="15" class="spin" /> 读取课表草稿
    </div>
    <template v-else-if="proposal">
      <header>
        <CalendarRange :size="17" aria-hidden="true" />
        <div>
          <strong>课表草稿 · {{ courses.length }} 门课</strong>
          <span>{{ statusLabel }}</span>
        </div>
      </header>
      <p v-if="summary" class="timetable-summary">{{ summary }}</p>
      <div v-if="courses.length" class="timetable-table-wrap">
        <table class="timetable-table">
          <thead>
            <tr>
              <th scope="col">星期</th>
              <th scope="col">时间</th>
              <th scope="col">课程</th>
              <th scope="col">地点</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in courses" :key="`${index}-${row.name}`">
              <td>周{{ weekdayLabels[row.weekday] ?? row.weekday }}</td>
              <td class="timetable-time">
                {{ time(row.starts_at) }}–{{ time(row.ends_at) }}
                <span v-if="weeksLabel(row)" class="timetable-weeks">{{ weeksLabel(row) }}</span>
              </td>
              <td>
                {{ row.name }}
                <span v-if="row.teacher" class="timetable-muted">{{ row.teacher }}</span>
              </td>
              <td class="timetable-muted">{{ row.location || '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="timetable-hint">
        确认后只会写入你的日历，不会生成导入批次。<template v-if="!applied">想改哪门直接说，例如“周三那门改到 14:00”，我会重新给一版。</template>
      </p>
      <div v-if="proposal.status === 'pending'" class="timetable-actions">
        <ElButton type="primary" :loading="pending" @click="apply">
          <Check :size="15" aria-hidden="true" />
          确认写入 {{ courses.length }} 门课
        </ElButton>
        <ElButton :disabled="pending" @click="reject">
          <X :size="15" aria-hidden="true" />
          放弃
        </ElButton>
      </div>
      <p v-if="error" class="timetable-error" role="alert">{{ error }}</p>
    </template>
  </section>
</template>

<style scoped>
.timetable-card {
  margin-top: 10px;
  padding: 12px;
  border: 1px solid var(--m3-outline-variant, var(--border));
  border-radius: 12px;
  background: var(--surface);
  color: var(--text);
}
.timetable-card header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.timetable-card header > svg {
  flex: 0 0 auto;
  color: var(--accent);
}
.timetable-card header > div {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.timetable-card header strong {
  font-size: 13px;
  font-weight: 650;
}
.timetable-card header span {
  font-size: 11px;
  color: var(--muted);
}
.timetable-summary {
  margin: 8px 0 0;
  max-height: 7.5em;
  overflow: hidden;
  font-size: 12px;
  line-height: 1.55;
  color: var(--muted);
  white-space: pre-wrap;
}
.timetable-table-wrap {
  margin-top: 10px;
  overflow-x: auto;
}
.timetable-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
}
.timetable-table th,
.timetable-table td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--m3-outline-variant, var(--border));
  text-align: left;
  vertical-align: top;
  white-space: nowrap;
}
.timetable-table th {
  color: var(--muted);
  font-weight: 500;
}
.timetable-time {
  font-variant-numeric: tabular-nums;
}
.timetable-weeks {
  display: block;
  font-size: 11px;
  color: var(--muted);
}
.timetable-muted {
  color: var(--muted);
}
.timetable-hint {
  margin: 10px 0 0;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--muted);
}
.timetable-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.timetable-error {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--error);
}
.timetable-loading {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}
.spin {
  animation: spin 1s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
