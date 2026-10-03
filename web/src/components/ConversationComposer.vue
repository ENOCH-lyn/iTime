<script setup lang="ts">
import { MessageCircleMore, SendHorizontal, Sparkles } from 'lucide-vue-next'
import type { ConversationResponseMode } from '../lib/conversations'
import type { PlanningFields, PlanScope } from '../lib/plans'

const props = defineProps<{
  pending: boolean
  disabled: boolean
  archived: boolean
  aiFrozen: boolean
  active: boolean
}>()
const model = defineModel<string>({ required: true })
const mode = defineModel<ConversationResponseMode>('mode', { required: true })
const planning = defineModel<PlanningFields>('planning', { required: true })
const emit = defineEmits<{ submit: [] }>()

function patchPlanning(changes: Partial<PlanningFields>) {
  planning.value = { ...planning.value, ...changes }
}

function setScope(scope: PlanScope) {
  patchPlanning({ scope })
}

function submit() {
  if (props.pending || props.disabled || !model.value.trim()) return
  emit('submit')
}

function keydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
    event.preventDefault()
    submit()
  }
}
</script>

<template>
  <form class="conversation-composer" @submit.prevent="submit">
    <fieldset class="segmented-field composer-mode">
      <legend class="sr-only">回复模式</legend>
      <label :class="{ selected: mode === 'text' }">
        <input v-model="mode" type="radio" value="text" :disabled="disabled" />
        <MessageCircleMore :size="16" aria-hidden="true" />普通回复
      </label>
      <label :class="{ selected: mode === 'planning' }">
        <input v-model="mode" type="radio" value="planning" :disabled="disabled" />
        <Sparkles :size="16" aria-hidden="true" />生成计划
      </label>
    </fieldset>

    <div v-if="mode === 'planning'" class="composer-planning-fields">
      <fieldset class="segmented-field composer-plan-scope">
        <legend>范围</legend>
        <label :class="{ selected: planning.scope === 'day' }">
          <input
            type="radio"
            name="composer-plan-scope"
            value="day"
            :checked="planning.scope === 'day'"
            :disabled="disabled"
            @change="setScope('day')"
          />单日
        </label>
        <label :class="{ selected: planning.scope === 'week' }">
          <input
            type="radio"
            name="composer-plan-scope"
            value="week"
            :checked="planning.scope === 'week'"
            :disabled="disabled"
            @change="setScope('week')"
          />整周
        </label>
      </fieldset>
      <label class="composer-plan-field">
        <span>日期</span>
        <input
          type="date"
          :value="planning.target_date"
          :disabled="disabled"
          aria-label="计划日期"
          @input="patchPlanning({ target_date: ($event.target as HTMLInputElement).value })"
        />
      </label>
      <label class="composer-plan-field">
        <span>密度</span>
        <select
          :value="planning.density"
          :disabled="disabled"
          aria-label="计划密度"
          @change="patchPlanning({ density: ($event.target as HTMLSelectElement).value as PlanningFields['density'] })"
        >
          <option value="loose">宽松</option>
          <option value="balanced">均衡</option>
          <option value="tight">紧凑</option>
        </select>
      </label>
      <label class="composer-plan-field">
        <span>缓冲</span>
        <select
          :value="planning.buffer_minutes"
          :disabled="disabled"
          aria-label="计划缓冲"
          @change="patchPlanning({ buffer_minutes: Number(($event.target as HTMLSelectElement).value) })"
        >
          <option v-for="value in 13" :key="value - 1" :value="(value - 1) * 5">
            {{ (value - 1) * 5 }} 分钟
          </option>
        </select>
      </label>
      <label class="composer-plan-field">
        <span>开始</span>
        <input
          type="time"
          step="900"
          :value="planning.availability_start"
          :disabled="disabled"
          aria-label="可规划开始"
          @input="patchPlanning({ availability_start: ($event.target as HTMLInputElement).value })"
        />
      </label>
      <label class="composer-plan-field">
        <span>结束</span>
        <input
          type="time"
          step="900"
          :value="planning.availability_end"
          :disabled="disabled"
          aria-label="可规划结束"
          @input="patchPlanning({ availability_end: ($event.target as HTMLInputElement).value })"
        />
      </label>
    </div>

    <label for="conversation-message" class="sr-only">消息</label>
    <textarea id="conversation-message" v-model="model" rows="3" maxlength="12000"
      :disabled="disabled" :placeholder="archived
        ? '重新打开会话后可继续'
        : aiFrozen
          ? 'AI 功能已暂停'
          : active
            ? '等待当前回复完成'
            : mode === 'planning'
              ? '说明这次计划需要优先考虑什么'
              : '输入消息'"
      @keydown="keydown" />
    <div class="composer-actions">
      <span>{{ Array.from(model).length }}/12000</span>
      <button type="submit" class="button composer-send"
        :disabled="pending || disabled || !model.trim()"
        :aria-label="mode === 'planning' ? '生成计划' : '发送消息'">
        <SendHorizontal :size="18" aria-hidden="true" />
        <span>{{ pending ? '正在发送…' : mode === 'planning' ? '生成' : '发送' }}</span>
      </button>
    </div>
  </form>
</template>
