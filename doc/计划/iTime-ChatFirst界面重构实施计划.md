# iTime Chat-first 界面重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付一个以 AI 对话为首要工作区、输入区不遮挡消息、支持安全 Markdown、会话标题清晰且设置入口符合直觉的 Web 界面首轮重构。

**Architecture:** 先在不改变后端业务合同的前提下重建 AI 页面外壳：全局窄栏、会话侧栏、标题栏、消息视口和输入区各自承担单一职责，并通过显式 grid 区域和滚动状态机保证消息不被覆盖。设置页改为 `/settings` 单页分区，旧 `/account/*` 深链接重定向到对应区段；其他业务页本轮只收敛产品导航层级和设置入口，不重写其领域工作区。

**Tech Stack:** Vue 3、TypeScript、Vue Router、Element Plus、Lucide Vue、markdown-it、DOMPurify、Playwright、axe-core。

---

## 0. 执行边界

- 本轮不修改后端对话、额度、隐私、同步和账号语义。
- 本轮不迁移任务、日历、规划、专注、复盘、统计的领域工作区，只调整它们共享的一级导航。
- 本轮不删除旧设置地址；`/account` 和 `/account/:section` 必须兼容跳转。
- 每次提交只暂存本任务列出的文件。
- 工作区已有 `server/src/itime/ai/provider.py` 和 `server/src/itime/review/job_service.py` 修改，任何任务都不得暂存、还原或格式化这两个文件。
- 截图存在不代表视觉通过。每张指定截图都要用 `view_image` 实际查看，并按标题、导航、消息、输入区、换行、按钮、空态和错误态逐项记录。

## 1. 文件职责

### 新建

- `web/src/lib/product-navigation.ts`：一级与“更多”导航的唯一数据源和 `ProductArea` 类型。
- `web/src/lib/markdown.ts`：markdown-it 配置、标题降级、外链属性和 DOMPurify 清理。
- `web/src/components/MarkdownMessage.vue`：AI Markdown 展示与代码复制；不处理业务动作。
- `web/src/components/AccountMenu.vue`：账号菜单中的设置、帮助和退出。
- `web/src/components/AppRail.vue`：AI 工作区 64px 一级窄栏、同步状态和账号菜单。
- `web/src/components/ConversationSidebar.vue`：新建、搜索、活动/归档会话、分页和行菜单。
- `web/src/components/ConversationHeader.vue`：会话标题、移动端菜单、停止生成和会话操作。
- `web/src/components/ConversationViewport.vue`：消息列表、结构化卡片、滚动跟随和“回到最新”。
- `web/src/pages/SettingsPage.vue`：统一设置编排、Dialog/Drawer 开关和退出。
- `web/e2e/chat-first-visual.spec.ts`：长 Markdown、长历史、输入扩展、几何关系和视觉证据专项。
- `web/e2e/settings-page.spec.ts`：统一设置、旧路由兼容和设置状态专项。

### 修改

- `web/package.json`、`web/pnpm-lock.yaml`：加入 Markdown 解析与清理依赖。
- `server/tests/web_environment.py`：为隔离 E2E 上游增加确定性的长 Markdown 回复，不改生产服务。
- `web/src/main.ts`：注册 `/settings`，为旧设置地址增加重定向。
- `web/src/components/ProductNavigation.vue`：改用统一导航数据，一级只保留 AI、任务、日历、专注，“更多”承载规划、复盘、统计。
- `web/src/components/ConversationComposer.vue`：Enter 发送、输入法保护、1—8 行、附件图标、阈值字数和发送/停止切换。
- `web/src/pages/AIPage.vue`：只编排数据、作业与新组件；无会话时也允许直接输入并自动创建会话。
- `web/src/pages/AccountPage.vue`：由 `SettingsPage` 替代后删除。
- `web/src/components/SyncStatusIndicator.vue`：设置地址改为 `/settings#sync`。
- `web/src/components/{TaskWorkspaceNavigation,CalendarNavigation,PlanNavigation,ReviewNavigation,StatisticsNavigation}.vue`、`web/src/pages/FocusPage.vue`：账号入口改为 `/settings`。
- `web/src/ai-workspace.css`：Chat-first 三栏、消息排版、Markdown、composer 和响应式合同。
- `web/src/style.css`：应用窄栏、账号菜单、统一设置分区和设置弹层样式。
- `web/e2e/{ai-conversations,ai-timetable-import,auth,privacy-governance,quota,sync,data-exports,account-deletion,tasks,recovery-after-restore}.spec.ts`：按兼容路由与新可访问名称更新定位器。
- `doc/README.md`、`designer.md`、`doc/测试/iTime-测试方案与测试用例.md`、`doc/验收/iTime-Web实施与验证.md`：实现完成后记录实际位置、命令、结果和残余范围。

### 删除

- `web/src/components/ConversationNavigation.vue`：职责由 `AppRail` 与 `ConversationSidebar` 分开承担。
- `web/src/components/ConversationThread.vue`：职责由 `ConversationHeader` 与 `ConversationViewport` 分开承担。

## 2. 提交顺序

1. `feat: 安全渲染 AI Markdown`
2. `feat: 重建 Chat-first 导航外壳`
3. `fix: 保证对话消息与输入区互不遮挡`
4. `feat: 合并用户设置入口`
5. `test: 验证 Chat-first 视觉与交互`
6. `docs: 记录 Chat-first 界面验证`

### Task 1: 安全 Markdown 消息

**Files:**
- Modify: `web/package.json`
- Modify: `web/pnpm-lock.yaml`
- Modify: `server/tests/web_environment.py`
- Create: `web/src/lib/markdown.ts`
- Create: `web/src/components/MarkdownMessage.vue`
- Modify: `web/src/components/ConversationThread.vue`
- Modify: `web/src/ai-workspace.css`
- Modify: `web/e2e/ai-conversations.spec.ts`

- [ ] **Step 1: 先写 Markdown 行为断言和隔离上游夹具**

在 `server/tests/web_environment.py` 的普通对话分支中加入唯一触发词，返回覆盖标题、列表、引用、表格、外链、行内代码、代码块和原始 HTML 的内容：

```python
elif "Markdown 视觉验收" in user_prompt:
    parts = [
        (
            "# 学习安排\n\n"
            "先确认 **核心目标**，再处理 *次要事项*。\n\n"
            "## 今天\n\n"
            "1. 完成线性代数\n"
            "2. 整理论文资料\n\n"
            "> 计划需要保留缓冲。\n\n"
            "| 时间 | 内容 |\n"
            "| --- | --- |\n"
            "| 09:00 | 复习 `矩阵` |\n\n"
            "[查看资料](https://example.com/reference)\n\n"
            "```ts\n"
            "const minutes = 45\n"
            "console.log(minutes)\n"
            "```\n\n"
            "<img src=x onerror=\"window.__markdownXss = true\">"
        )
    ]
```

在 `web/e2e/ai-conversations.spec.ts` 增加用例，先证明当前纯文本实现失败：

```ts
test('AI Markdown 有语义且不执行原始 HTML', async ({ page, request }) => {
  await account(page, request)
  await newConversation(page)
  await send(page, 'Markdown 视觉验收')

  const assistant = page.locator('.message-assistant').last()
  await expect(assistant.getByRole('heading', { name: '学习安排' })).toBeVisible()
  await expect(assistant.getByRole('list')).toContainText('完成线性代数')
  await expect(assistant.getByRole('table')).toContainText('09:00')
  await expect(assistant.getByRole('link', { name: '查看资料' })).toHaveAttribute(
    'rel',
    'noopener noreferrer',
  )
  await expect(assistant.getByRole('link', { name: '查看资料' })).toHaveAttribute(
    'target',
    '_blank',
  )
  await expect(assistant.getByRole('button', { name: '复制代码' })).toBeVisible()
  await expect(assistant.locator('img')).toHaveCount(0)
  expect(await page.evaluate(() => (window as Window & {
    __markdownXss?: boolean
  }).__markdownXss)).not.toBe(true)
})
```

- [ ] **Step 2: 运行用例并确认失败点正确**

Run:

```bash
cd web
pnpm exec playwright test e2e/ai-conversations.spec.ts \
  --project=desktop --grep "AI Markdown"
```

Expected: FAIL，首个语义标题或列表定位器不可见；不得出现服务启动、注册或 AI 作业错误。

- [ ] **Step 3: 安装锁定依赖**

Run:

```bash
cd web
pnpm add markdown-it dompurify
pnpm add -D @types/markdown-it
```

Expected: `package.json` 出现 `markdown-it`、`dompurify` 和 `@types/markdown-it`，`pnpm-lock.yaml` 更新，无 peer dependency 错误。

- [ ] **Step 4: 实现唯一 Markdown 管道**

`web/src/lib/markdown.ts` 使用以下完整接口；原始 HTML关闭，一级标题降为消息内二级标题，五六级标题收敛为四级，清理后才补外链属性：

```ts
import DOMPurify from 'dompurify'
import MarkdownIt from 'markdown-it'

const markdown = new MarkdownIt({
  html: false,
  breaks: false,
  linkify: true,
  typographer: false,
})

const defaultFence = markdown.renderer.rules.fence
if (!defaultFence) throw new Error('markdown-it fence renderer is unavailable')
markdown.renderer.rules.fence = (tokens, index, options, environment, renderer) => (
  `<div class="markdown-code-block">`
  + '<button type="button" class="markdown-copy" data-copy-code>复制代码</button>'
  + defaultFence(tokens, index, options, environment, renderer)
  + '</div>'
)

export function renderMarkdown(source: string) {
  const tokens = markdown.parse(source, {})
  for (const token of tokens) {
    if (!['heading_open', 'heading_close'].includes(token.type)) continue
    if (token.tag === 'h1') token.tag = 'h2'
    if (token.tag === 'h5' || token.tag === 'h6') token.tag = 'h4'
  }
  const clean = DOMPurify.sanitize(markdown.renderer.render(
    tokens,
    markdown.options,
    {},
  ), {
    USE_PROFILES: { html: true },
  })
  const template = document.createElement('template')
  template.innerHTML = clean
  for (const link of template.content.querySelectorAll<HTMLAnchorElement>('a')) {
    if (!/^https?:$/i.test(new URL(link.href, window.location.href).protocol)) continue
    link.target = '_blank'
    link.rel = 'noopener noreferrer'
  }
  return template.innerHTML
}
```

- [ ] **Step 5: 增加 Markdown 展示组件并只用于 assistant**

`web/src/components/MarkdownMessage.vue`：

```vue
<script setup lang="ts">
import { computed } from 'vue'
import { renderMarkdown } from '../lib/markdown'

const props = defineProps<{ content: string }>()
const rendered = computed(() => {
  try {
    return { html: renderMarkdown(props.content), failed: false }
  } catch {
    return { html: '', failed: true }
  }
})

async function copyCode(event: MouseEvent) {
  const target = event.target
  if (!(target instanceof Element)) return
  const button = target.closest<HTMLButtonElement>('[data-copy-code]')
  if (!button) return
  const code = button.closest('.markdown-code-block')?.querySelector('code')
  if (!code) return
  await navigator.clipboard.writeText(code.textContent ?? '')
  button.textContent = '已复制'
  window.setTimeout(() => { button.textContent = '复制代码' }, 1500)
}
</script>

<template>
  <p v-if="rendered.failed" class="message-content">{{ content }}</p>
  <div v-else class="markdown-message" @click="copyCode"
    v-html="rendered.html"></div>
</template>
```

在 `ConversationThread.vue` 将正文分流：

```vue
<MarkdownMessage
  v-if="message.role === 'assistant' && message.content"
  :content="message.content"
/>
<p v-else-if="message.content" class="message-content">
  {{ message.content }}
</p>
```

用户消息继续使用 Vue 文本插值，禁止 `v-html`。

- [ ] **Step 6: 增加受约束的 Markdown 排版**

在 `web/src/ai-workspace.css` 增加以下规则，消息标题不得使用页面级字号：

```css
.markdown-message {
  max-width: 72ch;
  overflow-wrap: anywhere;
  line-height: 1.7;
}
.markdown-message > * + * { margin-top: 14px; }
.markdown-message h2 { font-size: 1.25rem; }
.markdown-message h3 { font-size: 1.0625rem; }
.markdown-message h4 { font-size: .9375rem; }
.markdown-message ul,
.markdown-message ol { padding-left: 1.5rem; }
.markdown-message blockquote {
  padding-left: 14px;
  border-left: 3px solid var(--border);
  color: var(--muted);
}
.markdown-message table {
  width: 100%;
  border-collapse: collapse;
  font-size: .875rem;
}
.markdown-message th,
.markdown-message td {
  padding: 8px 10px;
  border: 1px solid var(--border);
  text-align: left;
}
.markdown-message code {
  padding: 2px 5px;
  border-radius: 4px;
  background: var(--canvas);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: .875em;
}
.markdown-code-block {
  position: relative;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--canvas);
}
.markdown-code-block pre {
  overflow-x: auto;
  padding: 42px 16px 16px;
  margin: 0;
}
.markdown-code-block pre code {
  padding: 0;
  background: transparent;
}
.markdown-copy {
  position: absolute;
  top: 6px;
  right: 6px;
  min-height: 32px;
  padding: 4px 9px;
  border-radius: 6px;
  background: var(--surface);
  color: var(--muted);
  font-size: .75rem;
}
```

- [ ] **Step 7: 运行安全与现有消息回归**

Run:

```bash
cd web
pnpm build
pnpm exec playwright test e2e/ai-conversations.spec.ts \
  --project=desktop --grep "AI Markdown|多会话生成"
```

Expected: build PASS；两个 desktop 用例 PASS；assistant 有 Markdown 语义，用户内容仍为纯文本。

- [ ] **Step 8: 提交 Markdown 切片**

Run:

```bash
git add \
  web/package.json \
  web/pnpm-lock.yaml \
  server/tests/web_environment.py \
  web/src/lib/markdown.ts \
  web/src/components/MarkdownMessage.vue \
  web/src/components/ConversationThread.vue \
  web/src/ai-workspace.css \
  web/e2e/ai-conversations.spec.ts
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: 安全渲染 AI Markdown"
```

Expected: 暂存清单不含 `server/src/itime/ai/provider.py` 和 `server/src/itime/review/job_service.py`。

### Task 2: Chat-first 导航与会话层级

**Files:**
- Create: `web/src/lib/product-navigation.ts`
- Create: `web/src/components/AccountMenu.vue`
- Create: `web/src/components/AppRail.vue`
- Create: `web/src/components/ConversationSidebar.vue`
- Create: `web/src/components/ConversationHeader.vue`
- Modify: `web/src/components/ProductNavigation.vue`
- Modify: `web/src/components/ConversationThread.vue`
- Modify: `web/src/pages/AIPage.vue`
- Modify: `web/src/ai-workspace.css`
- Modify: `web/src/style.css`
- Modify: `web/e2e/ai-conversations.spec.ts`
- Delete: `web/src/components/ConversationNavigation.vue`

- [ ] **Step 1: 写导航层级和会话标题几何断言**

在 `web/e2e/ai-conversations.spec.ts` 的多会话用例中加入：

```ts
await expect(page.getByRole('navigation', { name: '主要功能' })).toBeVisible()
await expect(page.getByRole('link', { name: 'AI', exact: true })).toHaveAttribute(
  'aria-current',
  'page',
)
await expect(page.getByRole('link', { name: '设置', exact: true })).toHaveCount(0)
await expect(page.getByRole('button', { name: '更多功能' })).toBeVisible()

const titleGeometry = await page.locator('.conversation-title-text').evaluateAll(
  nodes => nodes.filter(node => {
    const style = getComputedStyle(node)
    return style.visibility !== 'hidden' && style.display !== 'none'
  }).map(node => {
    const box = node.getBoundingClientRect()
    return { text: node.textContent?.trim() ?? '', width: box.width, height: box.height }
  }),
)
expect(titleGeometry.length).toBeGreaterThan(0)
expect(titleGeometry.every(item => item.text && item.width > 48 && item.height > 0))
  .toBe(true)
```

再断言 header 只显示真实标题，不显示重复的“AI 对话”：

```ts
const header = page.getByRole('banner', { name: '会话标题' })
await expect(header.getByRole('heading', { name: firstPrompt })).toBeVisible()
await expect(header.getByText('AI 对话', { exact: true })).toHaveCount(0)
```

- [ ] **Step 2: 运行导航用例并确认失败**

Run:

```bash
cd web
pnpm exec playwright test e2e/ai-conversations.spec.ts \
  --project=desktop --grep "多会话生成"
```

Expected: FAIL，缺少“主要功能”导航或 `.conversation-title-text` 几何不满足；业务生成断言仍能到达。

- [ ] **Step 3: 建立统一导航数据**

`web/src/lib/product-navigation.ts`：

```ts
import {
  CalendarDays,
  ChartNoAxesCombined,
  CheckSquare2,
  ListChecks,
  MessageCircleMore,
  NotebookPen,
  TimerReset,
} from 'lucide-vue-next'

export type ProductArea =
  | 'ai'
  | 'tasks'
  | 'plans'
  | 'calendar'
  | 'focus'
  | 'reviews'
  | 'stats'

export const primaryNavigation = [
  { key: 'ai', to: '/ai', label: 'AI', icon: MessageCircleMore },
  { key: 'tasks', to: '/tasks', label: '任务', icon: CheckSquare2 },
  { key: 'calendar', to: '/calendar', label: '日历', icon: CalendarDays },
  { key: 'focus', to: '/focus', label: '专注', icon: TimerReset },
] as const

export const secondaryNavigation = [
  { key: 'plans', to: '/plans', label: '规划', icon: ListChecks },
  { key: 'reviews', to: '/reviews', label: '复盘', icon: NotebookPen },
  { key: 'stats', to: '/stats', label: '统计', icon: ChartNoAxesCombined },
] as const
```

`ProductNavigation.vue` 从该文件读取数据，一级渲染四项，`ElDropdown` 的“更多功能”渲染三项；当前区域在更多菜单内时，“更多”本身使用选中样式。删除 `settings` 类型和固定设置项。

- [ ] **Step 4: 实现账号菜单与全局窄栏**

`AccountMenu.vue` 对外只发出退出事件：

```vue
<script setup lang="ts">
import { CircleHelp, LogOut, Settings } from 'lucide-vue-next'
defineProps<{ email: string; pending: boolean }>()
const emit = defineEmits<{ logout: [] }>()
</script>

<template>
  <ElDropdown trigger="click" placement="right-end">
    <ElButton text circle class="account-menu-trigger"
      :aria-label="`账号菜单 ${email}`" :title="email">
      <span aria-hidden="true">{{ email.slice(0, 1).toLocaleUpperCase() }}</span>
    </ElButton>
    <template #dropdown>
      <ElDropdownMenu>
        <ElDropdownItem>
          <RouterLink to="/settings"><Settings :size="16" />设置</RouterLink>
        </ElDropdownItem>
        <ElDropdownItem>
          <RouterLink to="/help"><CircleHelp :size="16" />帮助</RouterLink>
        </ElDropdownItem>
        <ElDropdownItem divided :disabled="pending" @click="emit('logout')">
          <LogOut :size="16" />{{ pending ? '正在退出…' : '退出登录' }}
        </ElDropdownItem>
      </ElDropdownMenu>
    </template>
  </ElDropdown>
</template>
```

`AppRail.vue` 使用 `primaryNavigation`、`secondaryNavigation`、`SyncStatusIndicator` 和 `AccountMenu`；桌面宽度固定 64px，每个图标有 Tooltip 与 `aria-label`，不显示常驻文字标签。props 固定为：

```ts
defineProps<{
  current: ProductArea | null
  email: string
  pendingLogout: boolean
}>()
defineEmits<{ logout: [] }>()
```

- [ ] **Step 5: 拆出会话侧栏并修复 Element Plus 包装层**

将 `ConversationNavigation.vue` 的会话职责迁到 `ConversationSidebar.vue`。组件提供 `footer` slot，平板和手机 Drawer 用它在会话历史之后放产品导航与账号菜单。会话按钮内部使用稳定类名，不依赖 Element Plus 自动生成的 `<span>`：

```vue
<ElButton text class="conversation-select"
  :aria-pressed="selectedId === conversation.id"
  @click="emit('select', conversation)">
  <div class="conversation-select-copy">
    <strong class="conversation-title-text" :title="conversation.title">
      {{ conversation.title }}
    </strong>
    <small>{{ activityLabel(conversation) || readableTime(conversation.updated_at) }}</small>
  </div>
</ElButton>
```

侧栏顶部顺序固定为“新建会话”、`ElInput` 搜索、活动/归档分段控件、列表。重命名和归档放入每行 `ElDropdown`，仅在 hover、focus 或触控环境显示菜单按钮。

搜索只过滤当前已加载会话；有分页游标时继续保留“读取更早会话”，避免把局部结果描述成完整搜索。

- [ ] **Step 6: 拆出会话标题栏**

`ConversationHeader.vue` 的公共接口固定为：

```ts
defineProps<{
  conversation: Conversation | null
  cancelling: boolean
}>()
defineEmits<{
  openNavigation: []
  cancel: []
  rename: [conversation: Conversation]
  archive: [conversation: Conversation]
}>()
```

正常状态只显示会话标题和更多菜单；无会话时标题为“新对话”；移动/平板左侧显示“打开导航”按钮；生成中右侧显示停止按钮。不得显示“AI 对话”副标题和常驻刷新按钮。

- [ ] **Step 7: 让 AIPage 只编排三栏**

`AIPage.vue` 顶层结构改为：

```vue
<div class="ai-workspace">
  <AppRail
    current="ai"
    :email="identity?.user.email ?? ''"
    :pending-logout="pendingLogout"
    @logout="logout"
  />
  <ConversationSidebar
    class="conversation-sidebar-desktop"
    :conversations="conversations"
    :selected-id="selected?.id ?? null"
    :archived="archivedView"
    :loading="loadingList"
    :has-more="!!conversationCursor"
    :create-disabled="authLoading || creating"
    :ai-frozen="identity?.user.ai_access === 'frozen'"
    @select="selectConversation"
    @create="createNewConversation"
    @archived-view="switchArchived"
    @load-more="loadConversationList(true)"
    @rename="openRename"
    @archive="toggleArchive"
  />
  <main id="main-content" class="ai-main">
    <ConversationHeader
      :conversation="selected"
      :cancelling="cancelling"
      @open-navigation="navigationOpen = true"
      @cancel="cancelGeneration"
      @rename="openRename"
      @archive="toggleArchive"
    />
    <div v-if="error || success || identity?.user.ai_access === 'frozen'"
      class="ai-workspace-status">
      <StatusMessage :error="error" :success="success"
        :retry-label="error?.retryable ? '重新同步' : undefined"
        @retry="selected ? syncCurrent() : loadConversationList()" />
    </div>
    <ConversationThread
      :conversation="selected"
      :messages="messages"
      :loading="loadingThread"
      :has-older="!!olderCursor"
      :loading-older="loadingOlder"
      :cancelling="cancelling"
      @load-older="loadOlder"
      @cancel="cancelGeneration"
      @retry-message="retryMessage"
      @refresh="syncCurrent()"
    />
    <ConversationComposer
      v-if="selected"
      v-model="composer"
      v-model:attachment="composerAttachment"
      :pending="sending"
      :disabled="composerDisabled"
      :archived="selected.archived"
      :ai-frozen="identity?.user.ai_access === 'frozen'"
      :active="!!activeJob"
      @submit="send"
      @invalid-attachment="invalidAttachment"
    />
  </main>
  <ElDrawer v-model="navigationOpen" class="conversation-drawer"
    direction="ltr" size="min(320px, 92vw)" :with-header="false">
    <ConversationSidebar
      :conversations="conversations"
      :selected-id="selected?.id ?? null"
      :archived="archivedView"
      :loading="loadingList"
      :has-more="!!conversationCursor"
      :create-disabled="authLoading || creating"
      :ai-frozen="identity?.user.ai_access === 'frozen'"
      @select="conversation => {
        navigationOpen = false
        selectConversation(conversation)
      }"
      @create="() => {
        navigationOpen = false
        createNewConversation()
      }"
      @archived-view="switchArchived"
      @load-more="loadConversationList(true)"
      @rename="openRename"
      @archive="toggleArchive"
    >
      <template #footer>
        <ProductNavigation current="ai" />
        <div class="conversation-drawer-account">
          <RouterLink to="/settings" class="nav-link">设置</RouterLink>
          <RouterLink to="/help" class="nav-link">帮助</RouterLink>
          <ElButton text class="nav-link" :loading="pendingLogout" @click="logout">
            退出登录
          </ElButton>
        </div>
      </template>
    </ConversationSidebar>
  </ElDrawer>
</div>
```

`ConversationSidebar` 的 props 与 emit 名称在桌面和 Drawer 中保持完全一致；选择或新建后先关闭 Drawer，再执行原业务方法。Drawer 内顺序必须是新建、搜索、会话历史、产品导航、账号操作。

Task 2 期间暂时保留 `ConversationThread` 和 `ConversationComposer`。从 `ConversationThread` 删除旧 header、移动返回和常驻刷新按钮，消息区行为不变；`AIPage` 在 `ConversationHeader` 后继续显式渲染状态、`ConversationThread` 和 `ConversationComposer`，保证本提交独立构建。Task 3 再用 `ConversationViewport` 替换并删除 `ConversationThread`。

- [ ] **Step 8: 实现桌面、平板、手机响应式外壳**

`web/src/ai-workspace.css` 的核心布局：

```css
.ai-workspace {
  height: 100dvh;
  display: grid;
  grid-template-columns: 64px 280px minmax(0, 1fr);
  overflow: hidden;
  background: var(--surface);
}
.app-rail {
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  border-right: 1px solid var(--border);
  background: var(--canvas);
}
.conversation-sidebar {
  min-width: 0;
  height: 100dvh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border-right: 1px solid var(--border);
  background: var(--canvas);
}
@media (max-width: 1023px) {
  .ai-workspace { grid-template-columns: 64px minmax(0, 1fr); }
  .conversation-sidebar-desktop { display: none; }
}
@media (max-width: 700px) {
  .ai-workspace { display: block; }
  .app-rail { display: none; }
}
```

- [ ] **Step 9: 运行导航专项**

Run:

```bash
cd web
pnpm build
pnpm exec playwright test e2e/ai-conversations.spec.ts \
  --grep "多会话生成"
```

Expected: desktop、mobile、tablet 共 3 项 PASS；每个可见会话标题宽度大于 48px；移动端通过抽屉进入历史和产品导航。

- [ ] **Step 10: 提交导航切片**

Run:

```bash
git add \
  web/src/lib/product-navigation.ts \
  web/src/components/AccountMenu.vue \
  web/src/components/AppRail.vue \
  web/src/components/ConversationSidebar.vue \
  web/src/components/ConversationHeader.vue \
  web/src/components/ProductNavigation.vue \
  web/src/pages/AIPage.vue \
  web/src/ai-workspace.css \
  web/src/style.css \
  web/e2e/ai-conversations.spec.ts
git add -u web/src/components/ConversationNavigation.vue
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: 重建 Chat-first 导航外壳"
```

Expected: 暂存清单仅含本任务前端文件。

### Task 3: 消息视口、滚动跟随与输入区几何合同

**Files:**
- Create: `web/src/components/ConversationViewport.vue`
- Modify: `web/src/components/ConversationComposer.vue`
- Modify: `web/src/pages/AIPage.vue`
- Modify: `web/src/ai-workspace.css`
- Create: `web/e2e/chat-first-visual.spec.ts`
- Delete: `web/src/components/ConversationThread.vue`

- [ ] **Step 1: 写几何门禁**

`web/e2e/chat-first-visual.spec.ts` 复用真实注册辅助函数，创建会话并发送“Markdown 视觉验收”，然后加入通用断言：

```ts
async function expectConversationGeometry(page: Page) {
  const geometry = await page.evaluate(() => {
    const viewport = document.querySelector<HTMLElement>(
      '.conversation-message-viewport',
    )!
    const composer = document.querySelector<HTMLElement>(
      '.conversation-composer-shell',
    )!
    const last = document.querySelector<HTMLElement>(
      '.conversation-messages > li:last-child',
    )!
    viewport.scrollTop = viewport.scrollHeight
    const viewportBox = viewport.getBoundingClientRect()
    const composerBox = composer.getBoundingClientRect()
    const lastBox = last.getBoundingClientRect()
    return {
      viewportBottom: viewportBox.bottom,
      composerTop: composerBox.top,
      lastBottom: lastBox.bottom,
      scrollFits: document.documentElement.scrollWidth <= innerWidth,
    }
  })
  expect(geometry.viewportBottom).toBeLessThanOrEqual(geometry.composerTop)
  expect(geometry.lastBottom).toBeLessThanOrEqual(geometry.composerTop - 16)
  expect(geometry.scrollFits).toBe(true)
}
```

用例依次执行：

1. 普通长 Markdown。
2. 输入八行文本。
3. 添加一个 PNG 附件。
4. 将视口高度缩到 500px 并聚焦输入框。
5. 每个阶段调用 `expectConversationGeometry(page)`。

- [ ] **Step 2: 运行几何门禁并确认当前布局失败**

Run:

```bash
cd web
pnpm exec playwright test e2e/chat-first-visual.spec.ts \
  --project=desktop --grep "消息与输入区"
```

Expected: FAIL，`lastBottom` 与 composer 间距不足，或新专项组件选择器尚不存在。

- [ ] **Step 3: 实现 ConversationViewport 的滚动状态机**

组件对外接口：

```ts
const props = defineProps<{
  conversation: Conversation | null
  messages: ConversationMessage[]
  loading: boolean
  hasOlder: boolean
  loadingOlder: boolean
}>()
const emit = defineEmits<{
  loadOlder: []
  retryMessage: [message: ConversationMessage]
}>()
defineExpose({
  scrollToLatest: (force = true) => scrollToLatest(force),
})
```

内部规则：

```ts
const viewport = ref<HTMLElement | null>(null)
const nearBottom = ref(true)

function measureBottom() {
  const element = viewport.value
  if (!element) return
  nearBottom.value = (
    element.scrollHeight - element.scrollTop - element.clientHeight <= 96
  )
}

async function scrollToLatest(force = false) {
  await nextTick()
  if (!viewport.value || (!force && !nearBottom.value)) return
  viewport.value.scrollTop = viewport.value.scrollHeight
  measureBottom()
}

watch(
  () => props.messages.map(message => (
    `${message.id}:${message.content.length}:${message.status}`
  )).join('|'),
  () => void scrollToLatest(false),
)
```

视口滚动事件调用 `measureBottom`；`!nearBottom` 时显示“回到最新”按钮。加载更早消息前记录 `scrollHeight` 和 `scrollTop`，数据更新后增加高度差，避免阅读位置跳动。

消息模板沿用现有提案卡、课表卡、失败/取消状态；assistant 正文使用 `MarkdownMessage`，user 正文纯文本。删除重复作者名和常驻时间，时间放入 `title`/`aria-label` 或消息操作菜单。

- [ ] **Step 4: 修正 ai-main 显式 grid 区域**

这是遮挡修复的关键。即使状态条不存在，每个子项也必须落在固定行：

```css
.ai-main {
  min-width: 0;
  height: 100dvh;
  display: grid;
  grid-template-areas:
    "header"
    "status"
    "messages"
    "composer";
  grid-template-rows: 64px auto minmax(0, 1fr) auto;
  overflow: hidden;
  background: var(--surface);
}
.conversation-header { grid-area: header; }
.ai-workspace-status { grid-area: status; }
.conversation-viewport-shell {
  grid-area: messages;
  min-width: 0;
  min-height: 0;
}
.conversation-message-viewport {
  width: 100%;
  height: 100%;
  overflow-y: auto;
}
.conversation-composer-shell {
  grid-area: composer;
  min-width: 0;
  padding-bottom: env(safe-area-inset-bottom);
  border-top: 1px solid var(--border);
  background: var(--surface);
}
.conversation-messages {
  width: min(780px, 100%);
  padding: 32px clamp(16px, 5vw, 48px) 32px;
  margin: 0 auto;
}
.conversation-messages > li:last-child {
  margin-bottom: 16px;
}
```

`.ai-workspace-status` 即使无内容也保留空容器但使用 `:empty { display: none; }`，不能依赖 DOM 顺序自动落行。

- [ ] **Step 5: 重做 composer 行为**

`ConversationComposer.vue`：

```ts
const composing = ref(false)
const countVisible = computed(() => Array.from(model.value).length >= 9600)

function keydown(event: KeyboardEvent) {
  if (event.isComposing || composing.value || event.key !== 'Enter') return
  if (event.shiftKey) return
  event.preventDefault()
  if (props.active) emit('cancel')
  else submit()
}
```

模板要求：

- `ElInput` 使用 `:autosize="{ minRows: 1, maxRows: 8 }"`。
- 监听 `compositionstart`、`compositionend`。
- 附件入口只显示回形针图标，使用 `ElTooltip content="添加附件"` 和 `aria-label="添加附件"`。
- 字数低于 9600 时不渲染计数。
- `active` 时主按钮显示停止图标，`aria-label="停止生成"` 并发出 `cancel`。
- `pending` 仅表示消息接单请求，不替代 `active`。
- 保留 `Ctrl+Enter`、`Meta+Enter` 的发送兼容，但默认 Enter 已可发送。

- [ ] **Step 6: 无会话时直接输入并自动创建**

把 `AIPage.vue` 的 `composerDisabled` 改为只受归档、冻结和活动作业约束。`ConversationComposer` 始终渲染。

将新建逻辑改为返回会话：

```ts
async function ensureConversation() {
  if (selected.value) return selected.value
  await createNewConversation()
  return selected.value
}

async function send() {
  if (sending.value || composerDisabled.value) return
  const conversation = await ensureConversation()
  if (!conversation) return
  // 后续沿用现有附件、operation_id、未知结果对账和 SSE 逻辑。
}
```

欢迎区只保留“今天想先理清哪件事？”；删除“选择一个会话，或新建会话开始”的解释。

- [ ] **Step 7: 用 visualViewport 固定软键盘高度**

`AIPage.vue` 监听可用时的 `window.visualViewport`，把高度写入工作区 CSS 变量：

```ts
const visualHeight = ref(window.visualViewport?.height ?? window.innerHeight)

function updateVisualHeight() {
  visualHeight.value = window.visualViewport?.height ?? window.innerHeight
}

onMounted(() => {
  window.visualViewport?.addEventListener('resize', updateVisualHeight)
})

onBeforeUnmount(() => {
  window.visualViewport?.removeEventListener('resize', updateVisualHeight)
})
```

工作区绑定：

```vue
<div class="ai-workspace"
  :style="{ '--visual-viewport-height': `${visualHeight}px` }">
```

样式：

```css
.ai-workspace,
.ai-main {
  height: var(--visual-viewport-height, 100dvh);
  max-height: 100dvh;
}
```

浏览器不提供 `visualViewport` 时回退到 `innerHeight` 和 `100dvh`。resize 只更新布局高度，不改变滚动位置；消息区由 grid 的 `minmax(0, 1fr)` 自动收缩。

- [ ] **Step 8: 连接 viewport 暴露方法**

`AIPage.vue` 使用：

```ts
const viewport = ref<{
  scrollToLatest: (force?: boolean) => Promise<void>
} | null>(null)
```

发送成功和切换会话后调用 `viewport.value?.scrollToLatest(true)`；流式 delta 调用 `scrollToLatest(false)`，用户向上阅读时不强制滚动。

- [ ] **Step 9: 跑专项与受影响业务卡回归**

Run:

```bash
cd web
pnpm build
pnpm exec playwright test \
  e2e/chat-first-visual.spec.ts \
  e2e/ai-conversations.spec.ts \
  e2e/ai-timetable-import.spec.ts
```

Expected: 三个项目全部 PASS；八行输入、附件、500px 高度下消息与 composer 不相交；提案和课表卡仍可操作。

- [ ] **Step 10: 提交视口切片**

Run:

```bash
git add \
  web/src/components/ConversationViewport.vue \
  web/src/components/ConversationComposer.vue \
  web/src/pages/AIPage.vue \
  web/src/ai-workspace.css \
  web/e2e/chat-first-visual.spec.ts
git add -u web/src/components/ConversationThread.vue
git diff --cached --check
git diff --cached --name-only
git commit -m "fix: 保证对话消息与输入区互不遮挡"
```

### Task 4: 统一设置页和兼容路由

**Files:**
- Create: `web/src/pages/SettingsPage.vue`
- Create: `web/e2e/settings-page.spec.ts`
- Modify: `web/src/main.ts`
- Modify: `web/src/style.css`
- Modify: `web/src/components/SyncStatusIndicator.vue`
- Modify: `web/src/components/PrivacySettings.vue`
- Modify: `web/src/components/QuotaOverview.vue`
- Modify: `web/src/components/SyncSettings.vue`
- Modify: `web/src/components/{TaskWorkspaceNavigation,CalendarNavigation,PlanNavigation,ReviewNavigation,StatisticsNavigation}.vue`
- Modify: `web/src/pages/FocusPage.vue`
- Modify: `web/e2e/{auth,privacy-governance,quota,sync,data-exports,account-deletion,tasks,recovery-after-restore}.spec.ts`
- Delete: `web/src/pages/AccountPage.vue`

- [ ] **Step 1: 先写统一设置和旧地址兼容断言**

`web/e2e/settings-page.spec.ts` 覆盖：

```ts
test('设置单页分组并兼容旧深链接', async ({ page, request }) => {
  await account(page, request)
  await page.goto('/settings')
  await expect(page.getByRole('heading', { name: '设置', exact: true })).toBeVisible()
  for (const name of ['账号与安全', '偏好', 'AI 与隐私', '数据与同步', '账号操作']) {
    await expect(page.getByRole('heading', { name, exact: true })).toBeVisible()
  }
  await expect(page.getByRole('navigation', { name: '个人设置' })).toHaveCount(0)

  await page.goto('/account/password')
  await expect(page).toHaveURL('/settings#password')
  await expect(page.getByRole('dialog', { name: '修改密码' })).toBeVisible()

  await page.goto('/account/devices')
  await expect(page).toHaveURL('/settings#devices')
  await expect(page.getByRole('dialog', { name: '登录设备' })).toBeVisible()

  await page.goto('/account/data')
  await expect(page).toHaveURL('/settings#data')
  await expect(page.locator('#data-export-settings')).toBeVisible()
})
```

另加 320×700 最大字号、深色、根级横向溢出和截图断言。

- [ ] **Step 2: 运行设置专项并确认失败**

Run:

```bash
cd web
pnpm exec playwright test e2e/settings-page.spec.ts --project=desktop
```

Expected: FAIL，`/settings` 当前进入 NotFound，或旧地址没有重定向。

- [ ] **Step 3: 注册新路由并保留旧地址**

`web/src/main.ts` 的 routes 内直接加入：

```ts
{
  path: '/settings',
  component: () => import('./pages/SettingsPage.vue'),
},
{
  path: '/account',
  redirect: '/settings',
},
{
  path: '/account/:section(password|devices|privacy|quota|sync|data|appearance)',
  redirect: route => ({
    path: '/settings',
    hash: `#${String(route.params.section)}`,
  }),
},
```

- [ ] **Step 4: 编排单页设置**

`SettingsPage.vue` 顶层使用 `<AppRail :current="null">`，设置不作为产品一级导航选中态；主区最大宽度 960px。区段结构：

```vue
<main id="main-content" class="settings-page">
  <header class="settings-page-header">
    <h1 tabindex="-1">设置</h1>
  </header>
  <section id="account" class="settings-section" aria-labelledby="account-title">
    <h2 id="account-title">账号与安全</h2>
    <div class="settings-row">
      <div><strong>邮箱</strong><p>{{ identity?.user.email }}</p></div>
      <span class="verified-label">已验证</span>
    </div>
    <div class="settings-row">
      <div><strong>密码</strong></div>
      <ElButton @click="passwordOpen = true">修改密码</ElButton>
    </div>
    <div class="settings-row">
      <div><strong>登录设备</strong></div>
      <ElButton @click="devicesOpen = true">查看设备</ElButton>
    </div>
  </section>
  <section id="appearance" class="settings-section" aria-labelledby="preference-title">
    <h2 id="preference-title">偏好</h2>
    <AppearanceControls />
    <RouterLink to="/calendar">时间与学期</RouterLink>
  </section>
  <section id="privacy" class="settings-section" aria-labelledby="ai-privacy-title">
    <h2 id="ai-privacy-title">AI 与隐私</h2>
    <QuotaOverview compact @open-details="quotaOpen = true" />
    <PrivacySettings compact />
  </section>
  <section id="data" class="settings-section" aria-labelledby="data-title">
    <h2 id="data-title">数据与同步</h2>
    <SyncSettings compact @open-details="syncOpen = true" />
    <DataExportSettings />
  </section>
  <section id="account-actions" class="settings-section">
    <h2>账号操作</h2>
    <div class="settings-row">
      <RouterLink to="/help">帮助</RouterLink>
      <ElButton :loading="pending" @click="logout">退出登录</ElButton>
    </div>
  </section>
  <AccountDeletionSettings />
</main>
```

实际组件新增 `compact?: boolean` props 和 `openDetails` emit：

- `QuotaOverview compact` 只展示今日可用、处理中和刷新时间，不渲染完整账本；Drawer 内渲染非 compact 版本。
- `SyncSettings compact` 正常时只展示“已同步”和最后时间；冲突、待传、离线或失败时显示数量与恢复入口；Drawer 内渲染完整版本。
- `PrivacySettings compact` 删除顶部重复介绍和图标，保留质检开关、7/30 天必要说明和保存状态。

密码使用 `ElDialog`，设备使用 `ElDrawer`。仅在打开时 `v-if` 挂载 `PasswordSettings`、`DeviceSettings`，避免设置首屏发起无关请求。

- [ ] **Step 5: 根据 hash 打开或滚动到正确区域**

实现稳定映射：

```ts
const overlayByHash = {
  '#password': () => { passwordOpen.value = true },
  '#devices': () => { devicesOpen.value = true },
  '#quota': () => { quotaOpen.value = true },
  '#sync': () => { syncOpen.value = true },
} as const

async function revealHash() {
  await nextTick()
  const action = overlayByHash[route.hash as keyof typeof overlayByHash]
  if (action) {
    action()
    return
  }
  const target = document.querySelector<HTMLElement>(route.hash)
  target?.scrollIntoView({ block: 'start' })
}

watch(() => route.hash, revealHash, { immediate: true })
```

Dialog/Drawer 关闭后使用 `router.replace('/settings')` 清除 overlay hash；滚动区段 hash 可保留。

- [ ] **Step 6: 收敛所有设置入口**

修改共享链接：

- `SyncStatusIndicator.vue` → `/settings#sync`
- 各领域侧栏账号邮箱 → `/settings`
- 帮助页设备入口 → `/settings#devices`
- 数据、隐私、额度、同步的内部返回链接 → `/settings` 对应 hash
- `ProductNavigation` 不再出现设置

E2E 可以继续访问旧 `/account/*` 来证明兼容，但新 UI 定位器必须使用 `/settings`。

- [ ] **Step 7: 设置页样式禁止卡片套卡片**

`web/src/style.css`：

```css
.settings-shell {
  min-height: 100dvh;
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  background: var(--surface);
}
.settings-page {
  width: min(960px, 100%);
  padding: 48px clamp(20px, 6vw, 80px) 96px;
  margin: 0 auto;
}
.settings-mobile-header { display: none; }
.settings-section {
  padding: 32px 0;
  border-bottom: 1px solid var(--border);
  scroll-margin-top: 24px;
}
.settings-section h2 {
  margin-bottom: 20px;
  font-size: 1.125rem;
}
.settings-row {
  min-height: 56px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 20px;
  padding: 12px 0;
}
@media (max-width: 700px) {
  .settings-shell { display: block; }
  .settings-shell > .app-rail { display: none; }
  .settings-page { padding: 24px 18px 72px; }
  .settings-mobile-header {
    min-height: 56px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 12px;
    border-bottom: 1px solid var(--border);
  }
}
```

手机设置页在 `settings-page` 前渲染 `.settings-mobile-header`，左侧为返回 AI 的图标链接，右侧为账号退出菜单，避免隐藏 AppRail 后失去主路径。

不为每个区段添加背景、阴影或外层圆角。

- [ ] **Step 8: 运行设置和受影响专项**

Run:

```bash
cd web
pnpm build
pnpm exec playwright test \
  e2e/settings-page.spec.ts \
  e2e/auth.spec.ts \
  e2e/privacy-governance.spec.ts \
  e2e/quota.spec.ts \
  e2e/sync.spec.ts \
  e2e/data-exports.spec.ts \
  e2e/account-deletion.spec.ts
```

Expected: 三视口全部 PASS；旧地址跳转后原业务操作仍可完成；设置页没有二级侧栏或等权子页面导航。

- [ ] **Step 9: 提交设置切片**

Run:

```bash
git add \
  web/src/pages/SettingsPage.vue \
  web/e2e/settings-page.spec.ts \
  web/src/main.ts \
  web/src/style.css \
  web/src/components/SyncStatusIndicator.vue \
  web/src/components/PrivacySettings.vue \
  web/src/components/QuotaOverview.vue \
  web/src/components/SyncSettings.vue \
  web/src/components/TaskWorkspaceNavigation.vue \
  web/src/components/CalendarNavigation.vue \
  web/src/components/PlanNavigation.vue \
  web/src/components/ReviewNavigation.vue \
  web/src/components/StatisticsNavigation.vue \
  web/src/pages/FocusPage.vue \
  web/e2e/auth.spec.ts \
  web/e2e/privacy-governance.spec.ts \
  web/e2e/quota.spec.ts \
  web/e2e/sync.spec.ts \
  web/e2e/data-exports.spec.ts \
  web/e2e/account-deletion.spec.ts \
  web/e2e/tasks.spec.ts \
  web/e2e/recovery-after-restore.spec.ts
git add -u web/src/pages/AccountPage.vue
git diff --cached --check
git diff --cached --name-only
git commit -m "feat: 合并用户设置入口"
```

### Task 5: 视觉数据、完整状态和截图门禁

**Files:**
- Modify: `web/e2e/chat-first-visual.spec.ts`
- Modify: `web/e2e/settings-page.spec.ts`
- Modify: `web/src/ai-workspace.css`
- Modify: `web/src/style.css`

- [ ] **Step 1: 构造 20 个会话和 30 条消息**

专项测试注册后读取 CSRF，并通过真实 API 创建 20 个会话：

```ts
const session = await (await page.request.get('/api/v1/auth/me')).json()
for (const [index, title] of Array.from({ length: 20 }, (_, offset) => [
  offset,
  offset === 0
    ? '这是一个需要保留省略号但仍能通过 Tooltip 查看完整内容的超长会话标题'
    : offset === 1
      ? 'UnbrokenTitle_ABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789'
      : `会话 ${offset + 1}`,
] as const)) {
  const response = await page.request.post('/api/v1/ai/conversations', {
    headers: { 'X-CSRF-Token': session.csrf_token },
    data: { operation_id: randomUUID(), title },
  })
  expect(response.ok(), `create conversation ${index}`).toBeTruthy()
}
```

选择一个会话后连续发送 15 条短消息，等待每条 assistant 完成，得到 30 条消息。最后发送长 Markdown。

- [ ] **Step 2: 覆盖结构化卡、失败、取消和附件**

专项复用已有确定性触发词：

- 普通 Markdown。
- Agent 任务提案。
- 课表导入卡。
- “取消测试”。
- “触发失败”。
- PNG 附件。

每种状态保存独立截图，不把多个状态挤在一张难以检查的全页图中。

- [ ] **Step 3: 增加文本裁切审计**

只允许 `.conversation-title-text` 和附件文件名使用单行省略。通用检查：

```ts
const accidentalClipping = await page.evaluate(() => (
  [...document.querySelectorAll<HTMLElement>('main *')]
    .filter(element => {
      const style = getComputedStyle(element)
      if (element.matches('.conversation-title-text, .composer-attachment-name')) {
        return false
      }
      return (
        style.overflow === 'hidden'
        && (element.scrollWidth > element.clientWidth + 1
          || element.scrollHeight > element.clientHeight + 1)
      )
    })
    .map(element => ({
      tag: element.tagName,
      className: element.className,
      text: element.textContent?.trim().slice(0, 80),
    }))
))
expect(accidentalClipping).toEqual([])
```

- [ ] **Step 4: 固定证据文件名**

截图写入：

```text
/tmp/itime-ui-evidence/{project}-chat-first-normal.png
/tmp/itime-ui-evidence/{project}-chat-first-markdown.png
/tmp/itime-ui-evidence/{project}-chat-first-proposals.png
/tmp/itime-ui-evidence/tablet-chat-first-drawer.png
/tmp/itime-ui-evidence/mobile-chat-first-composer-bottom.png
/tmp/itime-ui-evidence/desktop-chat-first-320-large-attachment.png
/tmp/itime-ui-evidence/{project}-settings-normal.png
/tmp/itime-ui-evidence/desktop-settings-sync-conflict.png
/tmp/itime-ui-evidence/mobile-settings-danger.png
```

截图使用 `animations: 'disabled'`；对话页截当前视口而不是 `fullPage`，因为核心合同是固定应用高度和内部消息滚动。

- [ ] **Step 5: 运行 Chromium 专项并逐张查看**

Run:

```bash
cd web
pnpm exec playwright test \
  e2e/chat-first-visual.spec.ts \
  e2e/settings-page.spec.ts
```

Expected: desktop、mobile、tablet 全部 PASS。

随后逐张使用 `view_image` 查看本任务列出的截图。发现以下任一问题必须回到 CSS/组件修复并重跑：

- 会话标题为空、只剩图标或被菜单完全覆盖。
- 最后一条消息或结构化卡被输入区覆盖。
- Markdown 标题过大、表格破版、代码按钮压住代码。
- 输入八行或附件后发送/停止按钮不可达。
- 手机抽屉与标题栏重叠。
- 设置区段出现卡片套卡片、解释文案重复或危险操作不清晰。
- 320px 最大字号出现裁切、按钮文字溢出或根级横向滚动。

- [ ] **Step 6: 运行真实 Edge 关键路径**

Run:

```bash
cd web
ITIME_E2E_BROWSER_EXECUTABLE_PATH="$HOME/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" \
  pnpm exec playwright test \
    e2e/chat-first-visual.spec.ts \
    e2e/settings-page.spec.ts \
    e2e/ai-timetable-import.spec.ts
```

Expected: Edge desktop、mobile、tablet 全部 PASS，且 Edge 截图也逐张查看。

- [ ] **Step 7: 运行完整前端回归**

Run:

```bash
cd web
pnpm build
pnpm test:e2e
```

Expected: 全量收集完成；仅保留测试文件内已有、可解释的项目条件 skip；无新失败、控制台错误、API 5xx、Vite overlay、axe 违规或根级横向溢出。

- [ ] **Step 8: 提交视觉门禁**

Run:

```bash
git add \
  web/e2e/chat-first-visual.spec.ts \
  web/e2e/settings-page.spec.ts \
  web/src/ai-workspace.css \
  web/src/style.css
git diff --cached --check
git diff --cached --name-only
git commit -m "test: 验证 Chat-first 视觉与交互"
```

### Task 6: 文档、验收证据和最终清理

**Files:**
- Modify: `doc/README.md`
- Modify: `designer.md`
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`
- Modify: `doc/设计/iTime-ChatFirst界面重构设计.md`

- [ ] **Step 1: 更新设计状态与实际组件位置**

将设计文档状态从“方向已确认，待实现”改为实际状态。组件边界写入真实文件路径；如实现与设计有差异，明确记录差异和理由，不回写成原先已经确认的内容。

- [ ] **Step 2: 更新测试用例**

在 UI 用例中新增并编号 Chat-first 专项，至少记录：

- assistant Markdown 与 user 纯文本。
- 原始 HTML/XSS 不执行。
- 会话标题非空和可见宽度。
- 消息视口、最后消息和 composer 的几何关系。
- 输入法、Enter/Shift+Enter、八行和附件。
- 向上阅读时不抢滚动，回到最新可用。
- `/settings` 分区与 `/account/*` 兼容。
- Chromium/Edge 三视口、320px 最大字号、深色和截图人工审查。

- [ ] **Step 3: 写实施与验证结果**

只记录真实执行过的命令、数量、耗时、浏览器版本、截图路径和人工发现/修复。没有执行的实体设备、Safari、Firefox 或完整业务页 AppRail 迁移继续标为未验证/后续范围。

- [ ] **Step 4: 最终检查工作区和提交边界**

Run:

```bash
git status --short
git diff --check
git log --oneline -8
git diff -- server/src/itime/ai/provider.py server/src/itime/review/job_service.py
```

Expected: 两个用户原改动仍存在且内容未被本计划提交改变；没有测试临时文件、截图或凭据进入仓库。

- [ ] **Step 5: 提交文档**

Run:

```bash
git add \
  doc/README.md \
  designer.md \
  doc/测试/iTime-测试方案与测试用例.md \
  doc/验收/iTime-Web实施与验证.md \
  doc/设计/iTime-ChatFirst界面重构设计.md
git diff --cached --check
git diff --cached --name-only
git commit -m "docs: 记录 Chat-first 界面验证"
```

## 3. 最终验收清单

- [ ] 登录后 `/ai` 直接出现可输入的主对话体验，无需先理解模式或选择会话。
- [ ] 桌面为 64px 全局窄栏、会话侧栏和消息主区；平板会话历史进抽屉；手机为单页对话。
- [ ] 一级导航只有 AI、任务、日历、专注；规划、复盘、统计在“更多”；设置只在账号菜单。
- [ ] 所有可见会话标题有文字、有可见宽度，长标题可通过 Tooltip 查看。
- [ ] assistant 支持清理后的 Markdown；user 永远按纯文本显示；业务动作仍来自结构化卡。
- [ ] composer 是显式 grid 行；普通、八行、附件、错误、生成中和窄高度状态都不覆盖消息。
- [ ] 用户向上阅读时流式增量不抢滚动，回到最新按钮可恢复跟随。
- [ ] Enter 发送、Shift+Enter 换行，中文输入法确认候选不误发送。
- [ ] `/settings` 在一个页面完成主要设置，旧 `/account/*` 地址继续可用。
- [ ] 默认文案不重复解释页面；隐私、额度、同步、失败恢复和危险操作保留必要说明。
- [ ] Chromium 与真实 Edge 的 desktop、mobile、tablet 专项通过。
- [ ] 320×700 最大字号深色通过，无根级横向滚动和意外裁切。
- [ ] 所有要求截图已经实际查看，检查记录写入实施与验证文档。
- [ ] 前端完整构建和全量 E2E 通过。
- [ ] 用户原有 `provider.py`、`review/job_service.py` 改动未被暂存、还原或提交。
