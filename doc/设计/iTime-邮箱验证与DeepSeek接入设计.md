# iTime 邮箱验证与 DeepSeek 接入设计

版本：0.1；更新：2026-10-05。本文记录用户在 Web 首版验收中确认的邮箱验证体验变更，
以及本地 DeepSeek 首选 AI 服务接入方案。API Key 只保存在未跟踪本地配置和数据库加密字段，
不得写入本文、Git、日志或测试证据。

## 1. 已确认产品决定

1. 管理后台可以独立开启或关闭新账号邮箱验证。
2. 开启验证时，邮件中的一次性令牌本身就是验证凭据；打开链接后自动验证、创建普通用户会话并
   进入 `/ai`，不再要求再次输入注册密码，也不再要求验证后手工登录。
3. 关闭验证时，新账号注册成功即标记邮箱已验证、创建会话并进入 `/ai`，不排队验证邮件。
4. 找回密码仍要求邮件令牌和新密码；管理员登录仍使用独立 TOTP，不受本设计影响。
5. iTime 以 AI 时间规划为核心。DeepSeek 首先承担全局对话、日周规划、复盘和课表截图识别；
   AI 业务修改继续先生成服务端校验的提案或候选，用户确认后才写入正式业务数据。

## 2. 注册策略与数据

`registration_policies` 单行策略增加：

- `require_email_verification boolean not null default true`
- 继续复用 `version`、`updated_at`、`updated_by_user_id` 的乐观并发与审计。

管理 API 的读取结果同时返回 `mode` 和 `require_email_verification`。更新请求必须完整提交两项
策略和 `expected_version`；任一项变化时只递增一次版本，并记录包含前后策略但不含用户凭据的
安全事件。

默认保持 `true`，因此升级不会静默改变既有环境。策略切换只影响之后首次创建的新账号；已有
待验证账号仍可通过原邮件令牌验证，避免切换时批量提升账号权限。

## 3. 注册和验证事务

### 3.1 开启邮箱验证

注册保持 `202` 和统一受理响应，创建待验证账号并排队邮件。验证请求只接受：

```json
{
  "token": "<43 字符一次性令牌>",
  "device_name": "网页浏览器 · 电脑/平板"
}
```

服务端在一个数据库事务中锁定用户和令牌，确认令牌未过期、未使用、用途为 `verify` 且账号
可用，然后：

1. 消费令牌；
2. 设置 `email_verified_at`；
3. 创建普通设备会话；
4. 写 `email_verified` 与 `logged_in_after_email_verification` 审计；
5. 提交后设置 HttpOnly、SameSite=Lax、Path=/api 的会话 Cookie。

并发点击同一链接只有一个请求成功。令牌无效、账号停用或数据库提交未知时不显示虚假登录。

### 3.2 关闭邮箱验证

只有首次创建账号的注册请求可以直接登录。事务内设置 `email_verified_at`、创建隐私/记忆/
时区默认数据、消费邀请并创建设备会话，不创建验证令牌或验证邮件。重复注册继续使用统一受理
语义，不能覆盖原密码，也不能借重复请求登录已存在账号。

注册成功响应增加：

```json
{
  "status": "registered",
  "verification_required": false,
  "session": {
    "user": {},
    "session_id": "...",
    "expires_at": "...",
    "csrf_token": "..."
  }
}
```

需要验证时返回 `verification_required=true` 且不返回会话。

## 4. Web 体验

- `/verify-email#token=...` 首次读取 fragment 后立即清除地址栏，显示“正在验证邮箱”，自动调用
  API；成功后接收会话并 `replace('/ai')`。
- 无令牌、过期、已使用和停用状态保留明确错误与“重新发送验证邮件”恢复入口。
- 注册响应为 `verification_required=false` 时直接接收会话并进入 AI；否则显示收件提示。
- `/admin/registration` 在注册模式下方增加“要求邮箱验证”开关，说明关闭后新账号会直接登录；
  保存按钮一次提交完整策略。
- 不在浏览器存储邮件令牌、API Key 或 CSRF。

## 5. DeepSeek 配置

本地使用：

- OpenAI 兼容根地址：`https://api.deepseek.com`
- 模型：`deepseek-flash`
- 能力：`conversation`、`planning`、`review`、`calendar_screenshot`
- 价格：额度预留使用官方峰值价格，币种 `USD`，避免高峰时低估成本。
- Key：本地 `.env.local` 保存一份运行配置；发布配置通过现有 AI 专用 Fernet 密钥加密入库，
  读取接口只返回 `key_configured=true`。

DeepSeek 当前官方文档说明 `deepseek-flash` 支持 Chat Completions、JSON、工具调用和视觉输入，
因此现有 OpenAI 兼容适配器原则上可复用。配置必须逐能力执行应用内合成测试，只有真实成功的
版本可以发布。

DeepSeek Chat Completions 不等于提供 `/audio/transcriptions`。`transcription` 必须用当前 Key
真实测试；若端点不支持，不发布该能力，并在后台明确显示失败阶段。不能为了“完整”而跳过合成
音频门禁。

## 6. AI 核心工作流

本轮优先验证以下用户闭环：

1. 全局 AI 对话可真实流式回复。
2. 对话或规划页可生成结构化日/周计划提案，用户确认后出现在规划和统一日历。
3. 课表截图可经视觉模型生成候选，用户校对确认后写入日历。
4. 复盘可生成追问和结构化报告，记忆候选仍需用户确认。

后续 AI 动作继续复用 `Proposal / ProposalChange`，按任务、日历和执行记录逐类扩展。模型不能
直接修改任务完成状态、固定日历、额度或账号数据；服务端所有权、版本、日期、容量和冲突校验
保持权威。

## 7. 验收

- 服务：真实 PostgreSQL 迁移、策略并发、令牌单次消费、直接注册会话、停用账号拒绝、Cookie
  与 CSRF、邮件队列数量和审计。
- Web：开启/关闭验证、链接自动登录、刷新保持身份、无令牌恢复、后台开关、desktop/mobile/
  tablet、axe、控制台和 API 5xx。
- DeepSeek：应用内配置测试和发布；真实对话、规划工具调用、复盘工具调用、视觉截图识别；
  记录模型、配置版本、耗时、Token/成本和失败阶段，但不记录 Key 或完整用户正文。
- 失败/取消的 AI 作业释放额度；结构无效不创建提案；用户确认前不改正式业务数据。
