<script setup lang="ts">
import { onMounted } from 'vue'
import PublicSiteFooter from '../components/PublicSiteFooter.vue'
import PublicSiteHeader from '../components/PublicSiteHeader.vue'
import '../public-site.css'

onMounted(() => { document.title = '帮助中心 · iTime' })
</script>

<template>
  <div class="public-site">
    <PublicSiteHeader current="help" />
    <main id="main-content" class="public-content">
      <header class="public-content-heading">
        <h1 tabindex="-1">帮助中心</h1>
        <p>从整理第一项任务开始；遇到失败时，先看提示里的原因和下一步。</p>
      </header>

      <nav class="help-index" aria-label="帮助主题">
        <a href="#start">开始使用</a>
        <a href="#calendar">任务与日历</a>
        <a href="#account">账号安全</a>
        <a href="#recover">失败恢复</a>
      </nav>

      <section id="start" class="help-section">
        <h2>开始使用</h2>
        <div>
          <h3>先整理任务还是课表？</h3>
          <p>两者都可以。没有课表不会阻止使用；可以先创建一项任务，也可以从日历页手工添加固定安排或导入课表。</p>
          <div class="help-actions">
            <RouterLink to="/tasks">打开任务</RouterLink>
            <RouterLink to="/calendar">打开日历</RouterLink>
            <RouterLink to="/demo">查看公开演示</RouterLink>
          </div>
        </div>
      </section>

      <section id="calendar" class="help-section">
        <h2>任务与日历</h2>
        <div>
          <h3>步骤完成后，任务为什么没有自动完成？</h3>
          <p>步骤、计划块和任务完成是三种不同事实。完成全部步骤或到达倒计时终点后，仍需要由你明确完成任务。</p>
          <h3>可以怎样导入课表或日历？</h3>
          <p>日历页支持 Excel 模板、ICS 文件、截图识别和 ICS 订阅 URL。文件与截图先生成候选，确认后才进入正式日历；订阅来源保持只读。</p>
          <h3>发现时间冲突怎么办？</h3>
          <p>冲突不会静默删除任何安排。请打开冲突项目查看两边时间，再编辑固定安排、任务截止或后续计划。</p>
        </div>
      </section>

      <section id="account" class="help-section">
        <h2>账号安全</h2>
        <div>
          <h3>无法登录或忘记密码</h3>
          <p>请联系管理员重置密码；不要重复注册。当前环境没有配置邮件服务。</p>
          <h3>密码忘记了，或发现不认识的设备</h3>
          <p>修改密码会退出所有设备。已经登录时，也可在设置中查看设备并远程退出。</p>
          <div class="help-actions">
            <RouterLink to="/settings#devices">查看登录设备</RouterLink>
          </div>
          <h3>账号已停用</h3>
          <p>请联系为你提供 iTime 服务的管理员，并提供账号邮箱和错误提示。重复注册不会恢复已停用的账号。</p>
        </div>
      </section>

      <section id="recover" class="help-section">
        <h2>失败恢复</h2>
        <div>
          <h3>操作失败，是否已经保存？</h3>
          <p>先看提示里的结果和下一步；网络中断时先重新打开页面确认，不要直接重复提交。</p>
          <h3>怎样提供排查信息？</h3>
          <p>记录错误提示和发生时间。不要发送密码、邮箱验证链接、恢复码或管理员密钥。</p>
        </div>
      </section>
    </main>
    <PublicSiteFooter />
  </div>
</template>
