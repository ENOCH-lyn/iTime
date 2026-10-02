<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ArrowRight, Check, RotateCcw } from 'lucide-vue-next'
import PublicSiteFooter from '../components/PublicSiteFooter.vue'
import PublicSiteHeader from '../components/PublicSiteHeader.vue'
import '../public-site.css'
import '../demo.css'

type Density = 'relaxed' | 'balanced' | 'compact'

interface DemoBlock {
  id: string
  time: string
  endTime: string
  title: string
  detail: string
  kind: 'fixed' | 'task' | 'buffer'
}

const density = ref<Density>('balanced')
const completed = ref<string[]>([])

const schedules: Record<Density, DemoBlock[]> = {
  relaxed: [
    { id: 'class', time: '09:00', endTime: '10:20', title: '高等数学', detail: '固定安排 · 教学楼 A101', kind: 'fixed' },
    { id: 'report', time: '11:10', endTime: '11:55', title: '整理实验报告', detail: '任务 · 连续 45 分钟', kind: 'task' },
    { id: 'buffer', time: '14:00', endTime: '14:30', title: '缓冲', detail: '为临时变化留出空间', kind: 'buffer' },
    { id: 'reading', time: '15:00', endTime: '15:30', title: '阅读第三章', detail: '任务 · 专注 30 分钟', kind: 'task' },
    { id: 'exercise', time: '18:00', endTime: '18:30', title: '运动', detail: '任务 · 30 分钟', kind: 'task' },
  ],
  balanced: [
    { id: 'class', time: '09:00', endTime: '10:20', title: '高等数学', detail: '固定安排 · 教学楼 A101', kind: 'fixed' },
    { id: 'report', time: '10:45', endTime: '11:30', title: '整理实验报告', detail: '任务 · 连续 45 分钟', kind: 'task' },
    { id: 'reading', time: '14:30', endTime: '15:00', title: '阅读第三章', detail: '任务 · 专注 30 分钟', kind: 'task' },
    { id: 'buffer', time: '15:15', endTime: '15:30', title: '缓冲', detail: '默认保留 15 分钟', kind: 'buffer' },
    { id: 'exercise', time: '17:30', endTime: '18:00', title: '运动', detail: '任务 · 30 分钟', kind: 'task' },
  ],
  compact: [
    { id: 'class', time: '09:00', endTime: '10:20', title: '高等数学', detail: '固定安排 · 教学楼 A101', kind: 'fixed' },
    { id: 'report', time: '10:30', endTime: '11:15', title: '整理实验报告', detail: '任务 · 连续 45 分钟', kind: 'task' },
    { id: 'reading', time: '13:30', endTime: '14:00', title: '阅读第三章', detail: '任务 · 专注 30 分钟', kind: 'task' },
    { id: 'exercise', time: '16:00', endTime: '16:30', title: '运动', detail: '任务 · 30 分钟', kind: 'task' },
  ],
}

const blocks = computed(() => schedules[density.value])
const completedCount = computed(() => completed.value.length)

onMounted(() => {
  document.title = '产品演示 · iTime'
})

function toggleCompleted(id: string) {
  completed.value = completed.value.includes(id)
    ? completed.value.filter(item => item !== id)
    : [...completed.value, id]
}

function resetDemo() {
  density.value = 'balanced'
  completed.value = []
}
</script>

<template>
  <div class="public-site demo-page">
    <PublicSiteHeader current="demo" />
    <main id="main-content" class="demo-main">
      <header class="demo-intro">
        <div>
          <h1 tabindex="-1">一天的安排</h1>
          <p>用一组预设数据体验固定安排、任务、缓冲和明确完成。</p>
        </div>
        <p class="demo-data-note">演示数据只保留在当前页面，刷新后会重置。</p>
      </header>

      <section class="demo-controls" aria-labelledby="density-heading">
        <fieldset class="demo-density">
          <legend id="density-heading">安排密度</legend>
          <label
            v-for="option in [
              { value: 'relaxed', label: '宽松' },
              { value: 'balanced', label: '均衡' },
              { value: 'compact', label: '紧凑' },
            ]"
            :key="option.value"
            :class="{ selected: density === option.value }"
          >
            <input v-model="density" type="radio" name="density" :value="option.value" />
            {{ option.label }}
          </label>
        </fieldset>
        <button class="icon-text-button" type="button" @click="resetDemo">
          <RotateCcw :size="17" aria-hidden="true" />
          重新开始
        </button>
      </section>

      <section class="demo-workspace" aria-label="日计划演示">
        <aside class="demo-backlog">
          <div class="demo-panel-heading">
            <div>
              <p>待安排</p>
              <h2>今天的任务</h2>
            </div>
            <span>{{ completedCount }}/3 完成</span>
          </div>
          <ul>
            <li
              v-for="task in [
                { id: 'report', title: '整理实验报告', detail: '45 分钟 · 今天截止' },
                { id: 'reading', title: '阅读第三章', detail: '30 分钟 · 可拆分' },
                { id: 'exercise', title: '运动', detail: '30 分钟 · 无截止' },
              ]"
              :key="task.id"
              :class="{ completed: completed.includes(task.id) }"
            >
              <button
                type="button"
                :aria-label="`${completed.includes(task.id) ? '重新打开' : '标记完成'}：${task.title}`"
                :aria-pressed="completed.includes(task.id)"
                @click="toggleCompleted(task.id)"
              >
                <Check :size="16" aria-hidden="true" />
              </button>
              <div>
                <strong>{{ task.title }}</strong>
                <span>{{ task.detail }}</span>
              </div>
            </li>
          </ul>
          <p class="demo-backlog-note">完成计划块不会自动完成任务，只有这里的明确操作会改变完成状态。</p>
        </aside>

        <div class="demo-timeline">
          <header class="demo-timeline-heading">
            <div>
              <p>2026 年 10 月 8 日 · 周四</p>
              <h2>{{ density === 'relaxed' ? '宽松安排' : density === 'compact' ? '紧凑安排' : '均衡安排' }}</h2>
            </div>
            <span>Asia/Shanghai</span>
          </header>
          <ol>
            <li
              v-for="block in blocks"
              :key="block.id"
              :class="[block.kind, { completed: completed.includes(block.id) }]"
            >
              <div class="demo-time">
                <time>{{ block.time }}</time>
                <span>{{ block.endTime }}</span>
              </div>
              <span class="demo-block-line"></span>
              <div class="demo-block-copy">
                <strong>{{ block.title }}</strong>
                <span>{{ block.detail }}</span>
              </div>
              <span v-if="completed.includes(block.id)" class="demo-completed-state">
                已完成
              </span>
            </li>
          </ol>
        </div>
      </section>

      <section class="demo-cta" aria-labelledby="demo-cta-heading">
        <div>
          <h2 id="demo-cta-heading">把示例换成你自己的时间。</h2>
          <p>注册后，任务、课表和安排会保存到你的账号。</p>
        </div>
        <RouterLink to="/register" class="button">
          开始使用
          <ArrowRight :size="18" aria-hidden="true" />
        </RouterLink>
      </section>
    </main>
    <PublicSiteFooter />
  </div>
</template>
