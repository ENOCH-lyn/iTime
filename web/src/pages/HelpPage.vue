<script setup lang="ts">
import { onMounted } from 'vue'
import AppearanceControls from '../components/AppearanceControls.vue'
onMounted(() => { document.title = '账号帮助 · iTime' })
</script>

<template>
  <div class="public-layout">
    <header class="public-header"><RouterLink to="/" class="wordmark">iTime<span aria-hidden="true">.</span></RouterLink><AppearanceControls compact /></header>
    <main id="main-content" class="help-content">
      <h1 tabindex="-1">账号帮助</h1>
      <p class="page-description">遇到问题时，可以从这里继续。</p>
      <section><h2>没有收到验证或重设邮件</h2><p>请核对邮箱地址，并检查垃圾邮件。邮件可能有短暂延迟；重新发送后，旧链接会失效，请使用最新邮件。</p><RouterLink to="/resend-verification">重新发送验证邮件</RouterLink></section>
      <section><h2>验证链接无效</h2><p>链接可能已经过期、使用过或被新的链接替换。为保护账号，验证时需要输入注册密码。若刷新了验证页面，请重新打开邮件链接。</p><RouterLink to="/forgot-password">找回密码</RouterLink></section>
      <section><h2>密码忘记了，或发现不认识的设备</h2><p>通过邮箱重设密码会退出所有设备。已经登录时，也可在设置中查看设备、远程退出，并修改密码。</p><RouterLink to="/account/devices">查看登录设备</RouterLink></section>
      <section><h2>操作失败，是否已经保存</h2><p>错误提示会说明保存状态。网络中断时，服务端可能已处理请求，请先检查结果。注册或发送邮件可重新尝试；若改密响应中断，请先尝试使用新密码登录。</p><p>需要排查时，请保留错误提示中的请求编号，不要提供密码或邮件链接。</p></section>
      <section><h2>账号已停用</h2><p>请联系为你提供 iTime 服务的管理员，并提供账号邮箱和错误请求编号。重复注册不会恢复已停用的账号。</p></section>
      <RouterLink to="/login" class="button">返回登录</RouterLink>
    </main>
  </div>
</template>
