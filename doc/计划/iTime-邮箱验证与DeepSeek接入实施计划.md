# iTime 邮箱验证与 DeepSeek 接入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现可关闭邮箱验证、邮件链接自动验证登录，并把本地 DeepSeek 配置为 iTime 对话、规划、复盘和课表截图的真实 AI 服务。

**Architecture:** 注册策略增加独立验证开关；注册和验证事务复用现有设备会话合同。DeepSeek 继续走现有不可变配置版本、合成测试、发布、持久作业、额度和提案边界，不增加绕过服务端校验的模型直写路径。

**Tech Stack:** PostgreSQL 18、Alembic、FastAPI、SQLAlchemy async、Vue 3、Playwright、httpx、DeepSeek OpenAI-compatible Chat Completions。

---

## 文件结构

新增：

- `server/src/itime/migrations/versions/0037_registration_email_verification.py`：注册策略验证开关。
- `doc/设计/iTime-邮箱验证与DeepSeek接入设计.md`：确认后的产品与技术合同。
- `doc/计划/iTime-邮箱验证与DeepSeek接入实施计划.md`：本计划。

修改：

- `server/src/itime/admin/models.py`：映射 `require_email_verification`。
- `server/src/itime/admin/management.py`：管理策略输入、输出、并发和审计。
- `server/src/itime/auth/router.py`：注册直登、令牌验证直登和公开策略。
- `server/tests/test_auth.py`：令牌验证会话和关闭验证注册。
- `server/tests/test_admin_management.py`：策略开关、版本冲突和审计。
- `web/src/lib/api.ts`：公开策略与注册响应类型。
- `web/src/lib/admin-management.ts`：后台策略类型。
- `web/src/components/AdminRegistrationSettings.vue`：邮箱验证开关。
- `web/src/pages/AuthPage.vue`：自动验证、注册直登和错误恢复。
- `web/e2e/auth.spec.ts`：真实邮件链接自动登录。
- `web/e2e/admin-management.spec.ts`：后台关闭验证后注册直登。
- `.env.local`：未跟踪的 DeepSeek Key，仅本机。
- 现有 AI 配置表：四项 DeepSeek 配置版本、测试状态和发布指针。

## Task 1: 注册策略邮箱验证开关

- [x] **Step 1: 写迁移和服务失败测试**

在 `server/tests/test_admin_management.py` 增加：

```python
policy = client.get(f"{ADMIN}/registration-policy").json()
assert policy["require_email_verification"] is True
updated = client.put(
    f"{ADMIN}/registration-policy",
    json={
        "mode": "open",
        "require_email_verification": False,
        "expected_version": policy["version"],
    },
)
assert updated.json()["require_email_verification"] is False
```

同时断言旧版本提交返回 `registration_policy_version_conflict`，安全事件只记录布尔策略前后值。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_admin_management.py -k registration
```

Expected: FAIL，模型和 API 尚无 `require_email_verification`。

- [x] **Step 3: 新增迁移和模型**

迁移 `0037_registration_email_verification`：

```python
op.add_column(
    "registration_policies",
    sa.Column(
        "require_email_verification",
        sa.Boolean(),
        nullable=False,
        server_default=sa.true(),
    ),
)
```

降级只删除该列；默认 `true`。

- [x] **Step 4: 扩展管理 API**

`RegistrationPolicyInput` 增加 `require_email_verification: bool`，输出返回该字段。只有模式或开关实际
变化才递增一次版本，并记录 `registration_policy_updated` 审计详情。

- [x] **Step 5: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_admin_management.py
uv run alembic upgrade head
uv run alembic check
uv run ruff check src/itime/admin tests/test_admin_management.py
cd ..
git add server/src/itime/migrations/versions/0037_registration_email_verification.py \
  server/src/itime/admin/models.py server/src/itime/admin/management.py \
  server/tests/test_admin_management.py
git commit -m "feat: 管理新账号邮箱验证策略"
```

## Task 2: 邮件链接自动验证并登录

- [x] **Step 1: 写服务失败测试**

修改 `server/tests/test_auth.py`，验证只提交令牌和设备名：

```python
verified = client.post(
    f"{API}/verify-email",
    json={"token": token, "device_name": "邮件验证浏览器"},
)
assert verified.status_code == 200
assert verified.json()["user"]["email"] == email
assert client.cookies.get(COOKIE_NAME)
assert client.get(f"{API}/me").status_code == 200
```

另测并发单次消费、停用账号不创建会话、旧令牌失败、验证关闭时首次注册直接返回会话且邮件队列
为空、重复注册不能取得会话。

- [x] **Step 2: 运行测试确认失败**

```bash
cd server
uv run pytest -q tests/test_auth.py -k "verification or register"
```

Expected: FAIL，验证仍要求密码，注册仍总是排队邮件。

- [x] **Step 3: 实现验证直登**

`VerifyInput` 改为：

```python
class VerifyInput(Input):
    token: Token
    device_name: str = Field(default="网页浏览器", min_length=1, max_length=100)
```

验证事务消费令牌、设置验证时间、调用 `create_session()`，提交后 `set_session_cookie()`，返回
`session_result()`。

- [x] **Step 4: 实现关闭验证后的首次注册直登**

注册事务仅在 `new_id` 为真且策略关闭时设置验证时间并创建会话；重复注册继续返回统一受理结果，
不创建会话。响应增加 `verification_required` 和可空 `session`。

- [x] **Step 5: 验证并提交**

```bash
cd server
uv run pytest -q tests/test_auth.py tests/test_admin_management.py
uv run ruff check src/itime/auth tests/test_auth.py
cd ..
git commit -am "feat: 邮件链接验证后自动登录"
```

## Task 3: Web 自动流转和后台开关

- [ ] **Step 1: 改写浏览器失败测试**

`web/e2e/auth.spec.ts` 打开验证链接后直接断言 `/ai`，不查找密码字段。新增后台关闭验证后注册即
进入 `/ai` 的 desktop/mobile/tablet 用例。

- [ ] **Step 2: 运行受影响用例确认失败**

```bash
cd web
pnpm exec playwright test e2e/auth.spec.ts e2e/admin-management.spec.ts \
  --project=desktop
```

- [ ] **Step 3: 实现 Vue 自动验证**

`AuthPage.vue`：

- `verify-email` 不再需要密码；
- fragment 清除并加载策略后自动调用验证；
- 成功调用 `acceptSession()`、`notifySessionChange()` 和 `router.replace('/ai')`；
- 注册返回会话时同样直接进入 AI；
- 自动请求期间显示状态，失败保留重发入口。

后台组件增加原生 checkbox/toggle，策略保存一次提交模式和邮箱验证布尔值。

- [ ] **Step 4: 浏览器验证**

```bash
cd web
pnpm build
pnpm exec playwright test e2e/auth.spec.ts e2e/admin-management.spec.ts
```

Expected: Chromium desktop/mobile/tablet 通过；axe、控制台和根级溢出为零。

- [ ] **Step 5: 提交**

```bash
git add web/src/pages/AuthPage.vue web/src/components/AdminRegistrationSettings.vue \
  web/src/lib/api.ts web/src/lib/admin-management.ts \
  web/e2e/auth.spec.ts web/e2e/admin-management.spec.ts
git commit -m "feat: 自动完成邮箱验证登录"
```

## Task 4: 保存并发布 DeepSeek 配置

- [ ] **Step 1: 本地保存凭据**

只在未跟踪 `.env.local` 增加 `ITIME_DEEPSEEK_API_KEY`；确认 `.gitignore` 排除该文件，并运行：

```bash
git check-ignore .env.local
git grep -n "sk-" -- . ':!*.lock'
```

Expected: `.env.local` 被忽略；Git 文件中没有 Key。

- [ ] **Step 2: 通过应用管理合同创建配置**

为以下能力创建不可变版本：

| 能力 | 端点 | 模型 | 超时 | 最大输出 |
| --- | --- | --- | --- | --- |
| conversation | `https://api.deepseek.com` | `deepseek-flash` | 120s | 4096 |
| planning | 同上 | 同上 | 180s | 8192 |
| review | 同上 | 同上 | 180s | 8192 |
| calendar_screenshot | 同上 | 同上 | 180s | 8192 |

Token 价格使用 `USD` 峰值：输入 `0.30`、缓存输入 `0.006`、输出 `1.20` 每百万 Token。

- [ ] **Step 3: 逐能力真实合成测试和发布**

调用现有 `/api/v1/admin/ai-configs/{id}/test`。只有 `status=tested` 才调用 `/publish`；失败保留
错误阶段并修正适配器，不直接改数据库状态。

- [ ] **Step 4: 转写探测**

创建但不预先发布 `transcription` 配置，真实测试 `/audio/transcriptions`。若 DeepSeek 返回不支持，
保持 draft 并记录结果；不阻塞其他四项能力。

- [ ] **Step 5: 凭据与配置核验**

数据库只允许看到加密 Key、配置版本和发布指针；API 不回显 Key；普通日志不得出现 Key。

## Task 5: DeepSeek 真实 AI 闭环

- [ ] **Step 1: 全局对话**

创建真实用户会话，发送“把明天下午的学习任务整理成可执行计划”，验证流式正文、断线恢复、usage、
provider cost 和额度结算。

- [ ] **Step 2: 日周规划**

创建代表任务和固定日历，生成规划提案，验证唯一 `submit_itime_plan` 工具调用、服务端校验、预览、
确认、规划页和统一日历投影。

- [ ] **Step 3: 课表截图**

上传包含中文课程、星期和节次的真实测试图片，验证视觉识别作业、候选校对、部分确认、原图清理和
正式日历系列。

- [ ] **Step 4: 复盘**

创建活动日事实，生成追问与报告，验证事实/推测分离、记忆候选需确认和金额结算。

- [ ] **Step 5: UI 与失败恢复**

Chromium desktop/mobile/tablet 和真实 Edge desktop 执行页面身份、非空、overlay、控制台、API
5xx、axe、截图和至少一条实际交互。供应商失败时页面说明保存状态、恢复动作和请求编号。

## Task 6: 文档、回归和提交

- [ ] **Step 1: 服务端门禁**

```bash
cd server
uv run ruff check .
uv run pytest -q
uv build --no-sources
uv run alembic check
```

- [ ] **Step 2: 前端门禁**

```bash
cd web
pnpm build
pnpm test:e2e
```

- [ ] **Step 3: 同步文档**

更新需求规格、追踪、账号设计、AI 设计、测试方案、验收清单和 Web 实施证据。记录实际模型、
配置版本、能力测试与不支持项，不记录 Key。

- [ ] **Step 4: 最终提交**

```bash
git diff --check
git commit -m "docs: 记录邮箱体验与 DeepSeek 验证"
```
