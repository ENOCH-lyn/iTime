<script setup lang="ts">
import { CalendarClock, ListChecks, TimerReset } from 'lucide-vue-next'
import { formatDuration, type StatisticsResult } from '../lib/statistics'

defineProps<{
  summary: StatisticsResult['summary']
}>()
</script>

<template>
  <section class="statistics-overview" aria-labelledby="statistics-overview-title">
    <div class="statistics-section-heading">
      <div>
        <span class="statistics-eyebrow">范围汇总</span>
        <h2 id="statistics-overview-title">三类事实</h2>
      </div>
      <p>计划、任务与专注分别计算。</p>
    </div>

    <div class="statistics-metric-groups">
      <section class="statistics-metric-group" data-kind="planning">
        <header>
          <CalendarClock :size="20" aria-hidden="true" />
          <h3>计划</h3>
        </header>
        <p class="statistics-primary-value">
          {{ formatDuration(summary.planning.planned_seconds) }}
        </p>
        <dl>
          <div>
            <dt>计划块</dt>
            <dd>{{ summary.planning.block_count }}</dd>
          </div>
          <div>
            <dt>涉及任务</dt>
            <dd>{{ summary.planning.task_count }}</dd>
          </div>
        </dl>
      </section>

      <section class="statistics-metric-group" data-kind="tasks">
        <header>
          <ListChecks :size="20" aria-hidden="true" />
          <h3>任务</h3>
        </header>
        <p class="statistics-primary-value">
          {{ summary.tasks.completed_count }} 项完成
        </p>
        <dl>
          <div>
            <dt>普通任务</dt>
            <dd>{{ summary.tasks.completed_task_count }}</dd>
          </div>
          <div>
            <dt>重复实例</dt>
            <dd>{{ summary.tasks.completed_occurrence_count }}</dd>
          </div>
          <div>
            <dt>跳过实例</dt>
            <dd>{{ summary.tasks.skipped_occurrence_count }}</dd>
          </div>
        </dl>
      </section>

      <section class="statistics-metric-group" data-kind="focus">
        <header>
          <TimerReset :size="20" aria-hidden="true" />
          <h3>专注</h3>
        </header>
        <p class="statistics-primary-value">
          {{ formatDuration(summary.focus.total_seconds) }}
        </p>
        <dl>
          <div>
            <dt>会话</dt>
            <dd>{{ summary.focus.session_count }}</dd>
          </div>
          <div>
            <dt>已结束</dt>
            <dd>{{ summary.focus.finished_count }}</dd>
          </div>
          <div>
            <dt>已放弃</dt>
            <dd>{{ summary.focus.abandoned_count }}</dd>
          </div>
          <div>
            <dt>补记</dt>
            <dd>{{ summary.focus.manual_count }}</dd>
          </div>
          <div>
            <dt>已修订</dt>
            <dd>{{ summary.focus.revised_count }}</dd>
          </div>
          <div>
            <dt>进行中</dt>
            <dd>{{ summary.focus.ongoing_count }}</dd>
          </div>
        </dl>
      </section>
    </div>
  </section>
</template>
