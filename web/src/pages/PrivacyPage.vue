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
        <p>更新日期：2026 年 10 月 2 日</p>
      </header>

      <aside class="document-notice">
        当前版本用于本地开发和隔离测试，尚未部署正式 AI 供应商、质检系统或公众生产环境。正式上线前将补充运营主体、服务器地区、AI 供应商及联系渠道。
      </aside>

      <section>
        <h2>1. 处理的数据</h2>
        <p>账号功能处理邮箱、密码摘要、登录设备和安全事件。业务功能处理你创建的任务、目标、步骤、日历、导入候选及设置。</p>
        <p>服务端不会保存明文密码。平台 Key、鉴权头和管理员 TOTP 密钥不会作为普通业务正文返回客户端。</p>
      </section>

      <section>
        <h2>2. AI 与外部内容</h2>
        <p>当前已实现的截图识别会把你主动上传的图片发送到管理员发布的 OpenAI 兼容端点；识别结果先进入候选校对，确认前不会写入正式日历。</p>
        <p>全局 AI 对话、规划、复盘、公开链接摘要和金额额度仍在开发。正式启用时会说明具体供应商、处理地区、数据用途和可用的控制选项。</p>
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
              <tr><th scope="row">任务与日历</th><td data-label="产品规则">账号存续期间保存；删除进入 30 天回收站</td><td data-label="当前状态">保存已实现，回收站待实现</td></tr>
              <tr><th scope="row">识别原图</th><td data-label="产品规则">确认后删除；未完成最长保留 24 小时</td><td data-label="当前状态">已实现</td></tr>
              <tr><th scope="row">AI 技术正文日志</th><td data-label="产品规则">默认保留 7 天</td><td data-label="当前状态">待实现</td></tr>
              <tr><th scope="row">质量改进样本</th><td data-label="产品规则">默认参与，可关闭；最长保留 30 天；不用于训练</td><td data-label="当前状态">待实现</td></tr>
              <tr><th scope="row">账号注销</th><td data-label="产品规则">7 天撤销期后清理个人数据</td><td data-label="当前状态">待实现</td></tr>
            </tbody>
          </table>
        </div>
      </section>

      <section>
        <h2>5. 安全与恢复</h2>
        <p>服务使用账号所有权校验、可撤销会话、操作去重、敏感配置加密和请求编号。订阅 URL 及平台 AI Key 不在普通界面或日志中回显。</p>
        <p>任何页面标记为“待实现”的隐私能力都不能视为已经生效，也不构成当前开发环境的删除或导出承诺。</p>
      </section>
    </main>
    <PublicSiteFooter />
  </div>
</template>
