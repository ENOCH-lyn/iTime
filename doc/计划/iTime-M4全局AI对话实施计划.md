# iTime M4 全局 AI 对话实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付可跨页面和设备恢复的多会话文本 AI，包括持久化消息、后台流式作业、SSE、取消和真实 Web 界面。

**Architecture:** 扩展现有不可变 AI 配置和租约作业内核，新增独立的对话、消息、生成映射和事件表。worker 将供应商流分批写入 PostgreSQL，SSE 只推送已提交事件，普通消息与作业查询承担最终恢复。

**Tech Stack:** Python 3.13、FastAPI、SQLAlchemy async、PostgreSQL、Alembic、httpx、Vue 3、TypeScript、EventSource、Playwright Chromium。

---

## 文件结构

新增服务端文件：

- `server/src/itime/ai/conversation_models.py`：会话、消息、生成映射和事件 ORM。
- `server/src/itime/ai/conversation_schemas.py`：会话与消息输入合同。
- `server/src/itime/ai/conversation_service.py`：结果投影、相关历史、接单、消息终态和事件写入。
- `server/src/itime/ai/conversation_router.py`：会话、消息和 SSE 路由。
- `server/src/itime/ai/conversation_provider.py`：OpenAI 兼容文本流解析与错误分类。
- `server/src/itime/migrations/versions/0017_ai_conversations.py`：能力扩展和对话数据结构。
- `server/tests/test_ai_conversations.py`：真实 HTTP/PostgreSQL 会话合同。
- `server/tests/test_ai_conversation_worker.py`：本地流式上游、worker、租约和竞态。

修改服务端文件：

- `server/src/itime/ai/models.py`：扩展 capability、媒体可空和事件序号。
- `server/src/itime/ai/schemas.py`：增加 `conversation` 能力。
- `server/src/itime/ai/provider.py`：按能力执行合成配置测试。
- `server/src/itime/ai/job_service.py`：可空媒体、严格租约、续租和事件清理。
- `server/src/itime/ai/job_router.py`：取消时同步对话消息终态。
- `server/src/itime/ai/worker.py`：按能力分发并在网络等待时续租。
- `server/src/itime/main.py`、`server/src/itime/migrations/env.py`：注册新路由与模型。

新增 Web 文件：

- `web/src/lib/conversations.ts`：会话、消息、作业和事件 API 类型。
- `web/src/pages/AIPage.vue`：全局对话页面状态编排。
- `web/src/components/ConversationNavigation.vue`：会话列表与管理。
- `web/src/components/ConversationThread.vue`：消息、生成状态和取消。
- `web/src/components/ConversationComposer.vue`：多行输入与幂等重试。
- `web/src/ai-workspace.css`：三种视口的稳定布局。
- `web/e2e/ai-conversations.spec.ts`：真实浏览器流程。

修改 Web 和测试环境文件：

- `web/src/main.ts`、`web/src/components/ProductNavigation.vue`：`/ai` 路由和主入口。
- `web/src/lib/ai-jobs.ts`：通用 capability、事件序号和取消结果。
- `web/src/components/AdminAISettings.vue`、`web/src/lib/admin-ai.ts`：配置文本对话能力。
- `server/tests/web_environment.py`：流式文本上游和 AI worker。
- 现有 E2E 登录辅助：默认登录落点从 `/tasks` 调整为 `/ai`。

## Task 1：数据库模型与迁移

**Files:**

- Create: `server/src/itime/ai/conversation_models.py`
- Create: `server/src/itime/migrations/versions/0017_ai_conversations.py`
- Modify: `server/src/itime/ai/models.py`
- Modify: `server/src/itime/ai/schemas.py`
- Modify: `server/src/itime/migrations/env.py`
- Test: `server/tests/test_ai_conversations.py`

- [ ] **Step 1：先写迁移往返和约束测试**

测试必须创建两个账号，验证会话/消息复合所有者外键、同会话序号唯一、同会话活动作业唯一、截图必须带媒体、对话不得带媒体，并验证存在对话数据时 downgrade 明确拒绝。

```python
def test_ai_conversation_migration_constraints(database_url, client):
    _, owner_id = create_account(client, database_url)
    conversation_id = seed_conversation(database_url, owner_id)
    assert conversation_id
    assert_database_rejects_cross_owner_message(database_url, conversation_id)
    assert_database_rejects_second_active_job(database_url, conversation_id)
```

- [ ] **Step 2：运行测试确认失败**

Run: `cd server && uv run pytest -q tests/test_ai_conversations.py`

Expected: FAIL，原因是迁移和模型尚不存在。

- [ ] **Step 3：实现模型和迁移**

模型须提供以下稳定类型：

```python
class Conversation(Base):
    __tablename__ = "ai_conversations"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(120))
    title_source: Mapped[str] = mapped_column(String(8))
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

class ConversationMessage(Base):
    __tablename__ = "ai_conversation_messages"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID]
    conversation_id: Mapped[UUID]
    sequence: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(12))
    content: Mapped[str] = mapped_column(Text, default="", server_default="")

class ChatGeneration(Base):
    __tablename__ = "ai_chat_generations"
    job_id: Mapped[UUID] = mapped_column(primary_key=True)
    owner_id: Mapped[UUID]
    conversation_id: Mapped[UUID]
    request_message_id: Mapped[UUID]
    response_message_id: Mapped[UUID]
    history_through_sequence: Mapped[int] = mapped_column(Integer)
    context_message_ids: Mapped[list[str]] = mapped_column(JSONB)
    stream_generation: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )

class AIJobEvent(Base):
    __tablename__ = "ai_job_events"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    job_id: Mapped[UUID]
    owner_id: Mapped[UUID]
    sequence: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

Capability = Literal["calendar_screenshot", "conversation"]
```

迁移从 `0016_task_lifecycle` 升到 `0017_ai_conversations`，扩展三个 capability CHECK，将 `ai_jobs.input_media_id` 改为可空，新增 `conversation_id` 和 `last_event_sequence`。同会话活动作业由 `ai_jobs.conversation_id` 部分唯一索引约束；延迟触发器保证每个 conversation 作业恰有一个角色正确的生成映射，并拒绝其他能力挂载映射。

- [ ] **Step 4：验证迁移和 ORM**

Run:

```bash
cd server
uv run alembic upgrade head
uv run alembic check
uv run pytest -q tests/test_ai_conversations.py
```

Expected: 迁移为 `0017_ai_conversations (head)`，无模型漂移，专项约束测试通过。

- [ ] **Step 5：提交**

```bash
git add server/src/itime/ai/conversation_models.py \
  server/src/itime/ai/models.py server/src/itime/ai/schemas.py \
  server/src/itime/migrations/env.py \
  server/src/itime/migrations/versions/0017_ai_conversations.py \
  server/tests/test_ai_conversations.py
git commit -m "feat: 建立 AI 对话持久化模型"
```

## Task 2：会话 API 与原子接单

**Files:**

- Create: `server/src/itime/ai/conversation_schemas.py`
- Create: `server/src/itime/ai/conversation_service.py`
- Create: `server/src/itime/ai/conversation_router.py`
- Modify: `server/src/itime/main.py`
- Test: `server/tests/test_ai_conversations.py`

- [ ] **Step 1：写会话与接单失败测试**

覆盖创建/重放、列表游标、改名、归档/恢复、版本冲突、他人 404、AI 冻结、无发布配置、单会话活动作业、账号三个活动作业上限、相同操作号不同正文和事务回滚。

```python
def test_message_submission_is_atomic_and_idempotent(client, database_url):
    conversation = create_conversation(client)
    response = send_message(client, conversation, "帮我整理今天的安排")
    replay = send_message(
        client,
        conversation,
        "帮我整理今天的安排",
        operation_id=response.json()["operation_id"],
    )
    assert replay.json() == response.json()
    assert_atomic_chat_rows(database_url, response.json())
```

- [ ] **Step 2：运行测试确认失败**

Run: `cd server && uv run pytest -q tests/test_ai_conversations.py`

Expected: FAIL，路由返回 404。

- [ ] **Step 3：实现 schema、服务和路由**

入口类型固定为：

```python
class ConversationCreateInput(Input):
    operation_id: UUID
    title: str | None = Field(default=None, max_length=120)

class ConversationUpdateInput(Input):
    operation_id: UUID
    expected_version: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=120)
    archived: bool | None = None

class ConversationMessageCreateInput(Input):
    operation_id: UUID
    expected_conversation_version: int = Field(ge=1)
    content: str = Field(min_length=1, max_length=12000)
```

服务实现 `conversation_result`、`message_result`、`select_relevant_context`、`create_conversation`、`update_conversation` 和 `enqueue_message`。接单事务一次写入两条消息、作业、映射、queued 事件和操作结果。

- [ ] **Step 4：运行专项和静态检查**

Run:

```bash
cd server
uv run pytest -q tests/test_ai_conversations.py
uv run ruff check .
```

Expected: 会话专项全部通过，Ruff 无错误。

- [ ] **Step 5：提交**

```bash
git add server/src/itime/ai/conversation_schemas.py \
  server/src/itime/ai/conversation_service.py \
  server/src/itime/ai/conversation_router.py \
  server/src/itime/main.py server/tests/test_ai_conversations.py
git commit -m "feat: 实现 AI 多会话与消息接单服务"
```

## Task 3：文本 provider、事件写入与租约续期

**Files:**

- Create: `server/src/itime/ai/conversation_provider.py`
- Modify: `server/src/itime/ai/provider.py`
- Modify: `server/src/itime/ai/job_service.py`
- Modify: `server/src/itime/ai/worker.py`
- Test: `server/tests/test_ai_conversation_worker.py`
- Test: `server/tests/test_ai_jobs.py`
- Test: `server/tests/test_ai_config.py`

- [ ] **Step 1：写流式协议和租约测试**

本地 HTTP 服务必须能返回拆开的 SSE 半帧、多个 data 帧、一次性 JSON、429、503、401、空输出、超大输出和阻塞流。测试双 worker、空闲续租、续租失败停止、取消与 late delta、重试 reset 和总截止时间。

```python
async def test_stream_retries_with_new_generation(database_url, chat_upstream):
    job_id = seed_chat_job(database_url, chat_upstream.endpoint)
    await process_until_terminal(database_url)
    events = read_events(database_url, job_id)
    assert [event.kind for event in events].count("content_reset") == 1
    assert final_message(database_url, job_id).content == "第二次完整回复"
```

- [ ] **Step 2：运行测试确认失败**

Run:

```bash
cd server
uv run pytest -q tests/test_ai_conversation_worker.py tests/test_ai_jobs.py tests/test_ai_config.py
```

Expected: FAIL，缺少文本 provider、续租与 conversation 分发。

- [ ] **Step 3：实现 provider 和 worker**

关键接口和租约更新固定为：

```python
async def stream_conversation_reply(
    config: ProviderConfig,
    settings: Settings,
    cipher: Fernet,
    *,
    messages: list[dict[str, str]],
    timeout_seconds: float,
    on_delta: Callable[[str], Awaitable[bool]],
) -> ConversationProviderResult:
    async with openai_stream(config, settings, cipher, messages) as response:
        content = ""
        async for delta in parse_openai_text_events(response):
            content += delta
            if len(content) > MAX_CONVERSATION_OUTPUT_CHARS:
                raise ConversationProviderError.output_too_large()
            if not await on_delta(delta):
                raise ConversationGenerationSuperseded()
        if not content.strip():
            raise ConversationProviderError.empty_output()
        return ConversationProviderResult(
            content=content,
            provider_request_id=response.headers.get("x-request-id"),
            http_status=response.status_code,
        )

async def renew_job_lease(
    database: Database,
    claim: ClaimedJob,
    *,
    lease_seconds: int,
) -> bool:
    current = now()
    async with database.transaction() as session:
        result = await session.execute(
            update(AIJob)
            .where(
                AIJob.id == claim.id,
                AIJob.status == "running",
                AIJob.lease_id == claim.lease_id,
                AIJob.lease_until > current,
                AIJob.deadline_at > current,
            )
            .values(
                lease_until=func.least(
                    current + timedelta(seconds=lease_seconds),
                    AIJob.deadline_at,
                ),
                updated_at=current,
            )
        )
        return result.rowcount == 1
```

worker 使用 capability 分发；心跳间隔不超过租约的三分之一。所有增量、完成和失败提交同时匹配 job、lease、running 和未过期租约。重试前清空消息并写 `content_reset`。

- [ ] **Step 4：运行专项与既有截图回归**

Run:

```bash
cd server
uv run pytest -q tests/test_ai_conversation_worker.py \
  tests/test_ai_jobs.py tests/test_ai_config.py \
  tests/test_calendar_screenshot_imports.py
uv run ruff check .
```

Expected: 文本与截图作业全部通过，长请求不会被重复领取。

- [ ] **Step 5：提交**

```bash
git add server/src/itime/ai/conversation_provider.py \
  server/src/itime/ai/provider.py server/src/itime/ai/job_service.py \
  server/src/itime/ai/worker.py \
  server/tests/test_ai_conversation_worker.py server/tests/test_ai_jobs.py \
  server/tests/test_ai_config.py server/tests/test_calendar_screenshot_imports.py
git commit -m "feat: 实现可恢复的 AI 文本流作业"
```

## Task 4：SSE 续读、取消联动与事件清理

**Files:**

- Modify: `server/src/itime/ai/conversation_router.py`
- Modify: `server/src/itime/ai/conversation_service.py`
- Modify: `server/src/itime/ai/job_router.py`
- Modify: `server/src/itime/ai/job_service.py`
- Test: `server/tests/test_ai_conversations.py`
- Test: `server/tests/test_ai_conversation_worker.py`

- [ ] **Step 1：写真实 SSE 和取消测试**

验证 `after`、`Last-Event-ID`、重复连接、15 秒有界连接、终态关闭、他人 404、取消保留部分正文、取消终态事件、过期事件清理后消息快照仍完整。

```python
def test_sse_resumes_after_last_event_id(client, running_chat_job):
    first = read_sse(client, running_chat_job, stop_after=2)
    resumed = read_sse(
        client,
        running_chat_job,
        headers={"Last-Event-ID": str(first[-1]["sequence"])},
    )
    assert resumed[0]["sequence"] == first[-1]["sequence"] + 1
```

- [ ] **Step 2：运行测试确认失败**

Run: `cd server && uv run pytest -q tests/test_ai_conversations.py tests/test_ai_conversation_worker.py`

Expected: FAIL，events 路由和对话取消联动尚不存在。

- [ ] **Step 3：实现 SSE、取消和清理**

SSE 输出固定为：

```text
id: 7
event: content_delta
data: {"sequence":7,"generation":1,"payload":{"message_id":"9d77f709-6a1f-4cdf-86c4-6319f1eec972","text":"今天先完成数学作业。"}}

```

`job_router.cancel_ai_job` 在同一事务调用对话取消投影。`cleanup_expired_job_events` 使用 `FOR UPDATE SKIP LOCKED` 或有界主键删除，每批最多 1000 条。

- [ ] **Step 4：运行服务端全量验证**

Run:

```bash
cd server
uv run pytest -q
uv run ruff check .
uv run alembic current
uv run alembic check
uv build --no-sources
```

Expected: 全部测试通过，迁移位于 head，无漂移，构建成功。

- [ ] **Step 5：提交**

```bash
git add server/src/itime/ai/conversation_router.py \
  server/src/itime/ai/conversation_service.py \
  server/src/itime/ai/job_router.py server/src/itime/ai/job_service.py \
  server/tests/test_ai_conversations.py server/tests/test_ai_conversation_worker.py
git commit -m "feat: 完成 AI 对话流恢复与取消"
```

## Task 5：全局 AI 对话 Web 页面

**Files:**

- Create: `web/src/lib/conversations.ts`
- Create: `web/src/pages/AIPage.vue`
- Create: `web/src/components/ConversationNavigation.vue`
- Create: `web/src/components/ConversationThread.vue`
- Create: `web/src/components/ConversationComposer.vue`
- Create: `web/src/ai-workspace.css`
- Modify: `web/src/main.ts`
- Modify: `web/src/components/ProductNavigation.vue`
- Modify: `web/src/lib/ai-jobs.ts`
- Modify: `web/src/components/AdminAISettings.vue`
- Modify: `web/src/lib/admin-ai.ts`
- Test: `web/e2e/ai-conversations.spec.ts`

- [ ] **Step 1：写浏览器流程**

先覆盖配置 `conversation` 能力、创建两个会话、发送消息、分段可见、刷新恢复、切换会话、取消、改名、归档/恢复和错误状态。

```ts
test('对话流可离页恢复且不重复提交', async ({ page, request }) => {
  await account(page, request)
  await page.getByLabel('消息').fill('请分两段回答')
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByText('第一段')).toBeVisible()
  await page.goto('/tasks')
  await page.goto('/ai')
  await expect(page.getByText('完整回复')).toBeVisible()
  await expect(page.getByText('请分两段回答')).toHaveCount(1)
})
```

- [ ] **Step 2：运行构建确认失败**

Run: `cd web && pnpm build`

Expected: FAIL，页面和 API 模块尚不存在。

- [ ] **Step 3：实现页面和状态恢复**

`AIPage` 在挂载时并行读取会话与身份；选择会话后读取消息快照，为活动作业建立 EventSource。每个事件按 sequence 和 generation 去重；SSE 错误后使用 1、2、3 秒有界轮询，页面恢复可见或窗口聚焦时重新同步。

输入组件只在会话无活动作业、账号 AI 可用且在线时启用发送。请求保存状态未知时保留 operation ID 和正文，读取消息后决定重放。

- [ ] **Step 4：运行构建和 AI 页面专项**

Run:

```bash
cd web
pnpm build
pnpm exec playwright test e2e/ai-conversations.spec.ts
```

Expected: TypeScript 构建通过，AI 页面在 desktop、mobile、tablet 三项目通过。

- [ ] **Step 5：提交**

```bash
git add web/src/lib/conversations.ts web/src/pages/AIPage.vue \
  web/src/components/ConversationNavigation.vue \
  web/src/components/ConversationThread.vue \
  web/src/components/ConversationComposer.vue web/src/ai-workspace.css \
  web/src/main.ts web/src/components/ProductNavigation.vue \
  web/src/lib/ai-jobs.ts web/src/components/AdminAISettings.vue \
  web/src/lib/admin-ai.ts web/e2e/ai-conversations.spec.ts
git commit -m "feat: 完成全局 AI 多会话界面"
```

## Task 6：真实浏览器环境与全量回归

**Files:**

- Modify: `server/tests/web_environment.py`
- Modify: `web/e2e/*.spec.ts`
- Test: `web/e2e/ai-conversations.spec.ts`

- [ ] **Step 1：扩展本地流式上游和 worker**

测试上游按请求标记提供成功、慢流、断流重试、取消、401 和 503。测试环境同时启动 API、邮件 worker、AI worker、流式上游和生产构建预览，并在退出时逐一终止。

```python
processes = {
    "api": subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "tests.support_app:create_test_app",
         "--factory", "--host", "127.0.0.1", "--port", "8000", "--no-access-log"],
        cwd=SERVER,
        env=environment,
    ),
    "mail": subprocess.Popen(
        [sys.executable, "-m", "itime.mail_worker"],
        cwd=SERVER,
        env=environment,
    ),
    "ai": subprocess.Popen(
        [sys.executable, "-m", "itime.ai.worker"],
        cwd=SERVER,
        env=environment,
    ),
    "web": subprocess.Popen(
        ["pnpm", "preview", "--host", "localhost", "--port", "4173"],
        cwd=WEB,
        env=environment,
    ),
}
```

- [ ] **Step 2：更新既有登录落点**

所有登录辅助先断言 `/ai`，需要测试任务或日历时再点击产品导航，不能放宽为任意 URL。

- [ ] **Step 3：执行专项和全量浏览器测试**

Run:

```bash
cd web
pnpm build
pnpm exec playwright test e2e/ai-conversations.spec.ts
pnpm test:e2e
```

Expected: AI 专项和全部 Chromium 用例在 desktop、mobile、tablet 通过，无控制台错误或 500。

- [ ] **Step 4：检查视觉与窄屏证据**

检查 `/tmp/itime-ui-evidence/*ai-conversation*` 的桌面、平板、手机、320px 最大字号和深色截图；确认消息、输入区、会话标题和按钮无重叠或横向溢出。

- [ ] **Step 5：提交测试环境**

```bash
git add server/tests/web_environment.py web/e2e
git commit -m "test: 完成 AI 对话真实浏览器回归"
```

## Task 7：文档与阶段证据

**Files:**

- Modify: `README.md`
- Modify: `doc/README.md`
- Modify: `designer.md`
- Modify: `doc/设计/iTime-AI作业与临时媒体设计.md`
- Modify: `doc/设计/iTime-详细设计文档.md`
- Modify: `doc/需求/iTime-需求追踪矩阵.md`
- Modify: `doc/测试/iTime-测试方案与测试用例.md`
- Modify: `doc/计划/iTime-开发总计划.md`
- Modify: `doc/验收/iTime-Web实施与验证.md`
- Modify: `doc/验收/iTime-产品验收清单.md`

- [ ] **Step 1：记录实际实现和实测数据**

只写实际通过的测试数量、命令、日期、环境、已实现边界和剩余缺口。额度、提案、备用路由、正文日志和质检仍标为未完成。

- [ ] **Step 2：检查状态词和占位符**

Run:

```bash
rg -n "TB[D]|TO[DO]|待[补]|已经完成|已完成" README.md designer.md doc
git diff --check
```

Expected: 无占位符；“已完成”均有对应代码与执行证据。

- [ ] **Step 3：最终服务和 Web 回归**

Run:

```bash
cd server
uv run ruff check .
uv run pytest -q
uv run alembic check
uv build --no-sources
cd ../web
pnpm build
pnpm test:e2e
```

Expected: 全部命令成功。

- [ ] **Step 4：提交文档**

```bash
git add README.md designer.md doc
git commit -m "docs: 记录全局 AI 对话实施证据"
```
