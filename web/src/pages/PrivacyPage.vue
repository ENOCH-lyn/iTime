<script setup lang="ts">
import { onMounted } from 'vue'
import PublicSiteFooter from '../components/PublicSiteFooter.vue'
import PublicSiteHeader from '../components/PublicSiteHeader.vue'
import '../public-site.css'

onMounted(() => { document.title = '隐私说明 · iTime' })
</script>

<template>
  <div class="public-site">
    <PublicSiteHeader />
    <main id="main-content" class="public-content legal-content">
      <header class="public-content-heading">
        <h1 tabindex="-1">隐私说明</h1>
        <p>更新日期：2026 年 10 月 4 日</p>
      </header>

      <aside class="document-notice">
        当前版本用于本地开发和隔离测试，尚未部署正式 AI 供应商或公众生产环境。正式上线前将补充运营主体、服务器地区、AI 供应商及联系渠道。
      </aside>

      <section>
        <h2>1. 处理的数据</h2>
        <p>账号功能处理邮箱、密码摘要、登录设备和安全事件。业务功能处理你创建的任务、目标、步骤、日历、导入候选及设置。</p>
        <p>服务端不会保存明文密码。平台 Key、鉴权头和管理员 TOTP 密钥不会作为普通业务正文返回客户端。</p>
      </section>

      <section>
        <h2>2. AI 与外部内容</h2>
        <p>当前已实现的截图识别会把你主动上传的图片发送到管理员发布的 OpenAI 兼容端点；识别结果先进入候选校对，确认前不会写入正式日历。全局 AI 对话同样由服务端调用管理员发布的端点，并保存可恢复的消息。</p>
        <p>日周规划、单日复盘、确认式长期记忆、每日金额额度、技术正文日志和可关闭的质量改进样本已经接入同一服务端权限与审计边界。任务公开链接由受限进程读取，不携带浏览器 Cookie 或平台凭据；正式上线时会说明具体供应商、处理地区和数据用途。</p>
      </section>

      <section>
        <h2>3. 管理访问</h2>
        <p>超级管理员可以在授权的管理功能中查看用户正文以处理故障、审计和质量问题。此类访问必须经过独立管理员认证并记录访问者、对象、时间和结果。</p>
        <p>普通用户只能访问自己的内容。管理能力不依赖隐藏菜单，服务端会对每个请求重新校验权限。</p>
      </section>

      <section>
        <h2>4. 保存期限</h2>
        <div class="privacy-table-wrap" tabindex="0" aria-label="数据保存期限，可横向滚动">
          <table>
            <thead>
              <tr><th scope="col">数据</th><th scope="col">产品规则</th><th scope="col">当前状态</th></tr>
            </thead>
            <tbody>
              <tr><th scope="row">业务内容</th><td data-label="产品规则">账号存续期间保存；开放删除的对象进入 30 天回收站</td><td data-label="当前状态">任务、困难、长期记忆、日历系列与 ICS 订阅源已实现；学期、项目和 AI 会话等尚未开放独立删除</td></tr>
              <tr><th scope="row">识别原图</th><td data-label="产品规则">确认后删除；未完成最长保留 24 小时</td><td data-label="当前状态">已实现</td></tr>
              <tr><th scope="row">AI 技术正文日志</th><td data-label="产品规则">默认保留 7 天</td><td data-label="当前状态">已实现；管理员读取会审计</td></tr>
              <tr><th scope="row">质量改进样本</th><td data-label="产品规则">默认参与，可关闭；最长保留 30 天；不用于训练</td><td data-label="当前状态">已实现；可在隐私与质量设置中关闭</td></tr>
              <tr><th scope="row">同步与数据导出</th><td data-label="产品规则">按账号同步；导出文件生成后保留 24 小时</td><td data-label="当前状态">已实现；可在数据管理页生成完整数据包</td></tr>
              <tr><th scope="row">账号注销</th><td data-label="产品规则">申请后立即锁定，7 天内可撤销，到期清理个人数据</td><td data-label="当前状态">已实现；撤销不会复活已停止的临时工作</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2>5. 安全与恢复</h2>
        <p>服务使用账号所有权校验、可撤销会话、操作去重和敏感配置加密。订阅 URL 及平台 AI Key 不在普通界面或日志中回显。</p>
        <p>账号注销到期清理已经覆盖当前业务数据库；生产备份中的删除台账重放仍需在部署恢复演练中完成。尚未开放独立删除的对象不能视为已经具备回收或单项清理能力。</p>
      </section>
    </main>
    <PublicSiteFooter />
  </div>
</template>
