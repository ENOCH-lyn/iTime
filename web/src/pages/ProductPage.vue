<script setup lang="ts">
import { onMounted } from 'vue'
import { ArrowRight, Play } from 'lucide-vue-next'
import PublicSiteFooter from '../components/PublicSiteFooter.vue'
import PublicSiteHeader from '../components/PublicSiteHeader.vue'
import '../public-site.css'

onMounted(() => {
  document.title = 'iTime · 从容安排每一天'
})

const sceneRows = [
  { time: '08:00', label: '高等数学', meta: '固定安排', className: 'fixed-block' },
  { time: '10:30', label: '整理实验报告', meta: '45 分钟', className: 'task-block' },
  { time: '13:30', label: '阅读第三章', meta: '专注 30 分钟', className: 'focus-block' },
  { time: '16:00', label: '留白', meta: '缓冲与休息', className: 'free-block' },
] as const

const workflow = [
  {
    number: '01',
    title: '先整理',
    copy: '把任务、步骤、课表和固定安排放到同一个时间视图里。',
  },
  {
    number: '02',
    title: '再安排',
    copy: '从固定时间出发，为真正能完成的事情留出连续时间。',
  },
  {
    number: '03',
    title: '再复盘',
    copy: '计划、实际执行和遇到的困难分开记录，逐渐找到自己的节奏。',
  },
] as const
</script>

<template>
  <div class="public-site product-page">
    <PublicSiteHeader current="product" />
    <main id="main-content">
      <section class="product-hero">
        <div class="hero-schedule-scene" aria-hidden="true">
          <div class="scene-heading">
            <span>今天</span>
            <strong>10 月 8 日 · 周四</strong>
          </div>
          <div class="scene-day-axis">
            <span v-for="day in ['周一', '周二', '周三', '周四', '周五']" :key="day">
              {{ day }}
            </span>
          </div>
          <div class="scene-grid">
            <div v-for="row in 8" :key="row" class="scene-grid-line"></div>
          </div>
          <div class="scene-current-line"></div>
          <div class="scene-blocks">
            <div
              v-for="row in sceneRows"
              :key="row.time"
              class="scene-block"
              :class="row.className"
            >
              <time>{{ row.time }}</time>
              <strong>{{ row.label }}</strong>
              <span>{{ row.meta }}</span>
            </div>
          </div>
        </div>

        <div class="hero-copy">
          <h1 tabindex="-1">iTime</h1>
          <p class="hero-lead">把任务、课表和计划放在一起，给重要的事留出真正能完成的时间。</p>
          <div class="hero-actions">
            <RouterLink to="/register" class="button">
              开始使用
              <ArrowRight :size="18" aria-hidden="true" />
            </RouterLink>
            <RouterLink to="/demo" class="button secondary">
              <Play :size="17" aria-hidden="true" />
              查看演示
            </RouterLink>
          </div>
          <p class="hero-note">首版免费使用，不需要绑定支付方式。</p>
        </div>
      </section>
      <section id="workflow" class="workflow-section" aria-labelledby="workflow-heading">
        <header class="section-heading">
          <p>不再只列一张越来越长的清单</p>
          <h2 id="workflow-heading">从看清时间开始</h2>
        </header>
        <ol class="workflow-list">
          <li v-for="item in workflow" :key="item.number">
            <span class="workflow-number">{{ item.number }}</span>
            <h3>{{ item.title }}</h3>
            <p>{{ item.copy }}</p>
          </li>
        </ol>
      </section>

      <section class="product-story" aria-labelledby="story-heading">
        <div class="story-copy">
          <p>一天可以很具体，也可以留有余地</p>
          <h2 id="story-heading">固定安排不能移动，其他事情仍然可以从容调整。</h2>
        </div>
        <div class="story-timeline" aria-label="计划示例">
          <div>
            <time>09:00</time>
            <span class="story-line"></span>
            <p><strong>课程</strong><span>保留原来的真实日期与时间</span></p>
          </div>
          <div>
            <time>11:00</time>
            <span class="story-line task"></span>
            <p><strong>课程作业</strong><span>拆成可以开始的连续时段</span></p>
          </div>
          <div>
            <time>14:30</time>
            <span class="story-line rest"></span>
            <p><strong>缓冲</strong><span>计划里也应当有调整空间</span></p>
          </div>
          <div>
            <time>20:30</time>
            <span class="story-line review"></span>
            <p><strong>复盘</strong><span>事实、说明和建议分开保留</span></p>
          </div>
        </div>
      </section>

      <section class="closing-section" aria-labelledby="closing-heading">
        <div>
          <h2 id="closing-heading">从眼前的一件事开始。</h2>
          <p>先整理第一项任务，或者导入这周的课表。</p>
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
