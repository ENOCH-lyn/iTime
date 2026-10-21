<script setup lang="ts">
import { onMounted } from 'vue'
import PublicSiteFooter from '../components/PublicSiteFooter.vue'
import PublicSiteHeader from '../components/PublicSiteHeader.vue'
import '../public-site.css'

onMounted(() => { document.title = '用户协议 · iTime' })
</script>

<template>
  <div class="public-site">
    <PublicSiteHeader />
    <main id="main-content" class="public-content legal-content">
      <header class="public-content-heading">
        <h1 tabindex="-1">用户协议</h1>
        <p>更新日期：2026 年 10 月 4 日</p>
      </header>

      <aside class="document-notice">
        iTime 当前处于 Web 首版开发阶段，尚未面向公众正式运营。本协议记录当前产品边界；正式上线前还会补充运营主体和联系方式。
      </aside>

      <section>
        <h2>1. 服务范围</h2>
        <p>iTime 提供个人任务、课表、日历、规划、执行记录和复盘工具。当前 Web 版本不提供分享、多人协作、真实支付或业务提醒。</p>
        <p>公开演示只使用页面内预设数据，不会保存为个人内容。注册登录后的数据由服务端按账号保存。</p>
      </section>

      <section>
        <h2>2. 账号使用</h2>
        <p>你需要使用本人可接收邮件的地址注册，并妥善保管密码和登录设备。发现陌生设备时，应及时远程退出或重设密码。</p>
        <p>不得绕过身份校验访问他人数据，不得尝试获取平台密钥、管理员凭据或破坏服务稳定性。</p>
      </section>

      <section>
        <h2>3. 内容与数据</h2>
        <p>你应确保录入的文字、链接、课表和文件有合法来源。外部日历订阅保持只读，iTime 不代表你向外部服务写回内容。</p>
        <p>任务、困难、长期记忆、日历系列和 ICS 订阅源按页面规则进入 30 天回收站；学期、项目和 AI 会话等尚未开放独立删除。你可以在数据管理页生成个人数据包，也可以申请注销账号。注销申请会立即退出全部设备，7 天内可用正确凭据撤销，到期后清理当前业务数据库中的个人数据。</p>
      </section>

      <section>
        <h2>4. AI 结果</h2>
        <p>AI 输出可能包含错误或不完整信息，不构成医疗、法律、财务或其他专业意见。涉及任务、日历、计划、记忆和偏好的业务变更，必须在你确认后才会应用。</p>
        <p>模型调用失败、取消、每日金额额度和结果接受的具体状态以产品页面和服务端记录为准。失败或取消的调用不会扣减用户额度，成功结果按服务端账本结算。</p>
      </section>

      <section>
        <h2>5. 服务变更与中断</h2>
        <p>开发阶段可能调整接口、界面和数据结构。发生维护、网络或上游服务故障时，先按提示确认结果，再决定是否重试。</p>
      </section>

      <section>
        <h2>6. 联系与生效</h2>
        <p>当前问题请联系为你提供 iTime 测试环境的管理员。正式上线前，本页将补充运营主体、联系渠道、生效日期和适用的争议处理条款。</p>
      </section>
    </main>
    <PublicSiteFooter />
  </div>
</template>
