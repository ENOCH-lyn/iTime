# iTime Web 实施与验证

更新：2026-10-02。此文件记录已落地模块和真实验证，不替代[全量验收清单](iTime-产品验收清单.md)。用户要求完整 Web 用户端与后台、逐模块提交、详细服务及真实 UI 测试。

## M1-A：服务运行基础

对应 Git 提交主题：`feat: 建立可验证的服务端运行基础`，包含本节的首次版本。尚未实现账号或用户业务 UI。

| 内容 | 实际实现 |
| --- | --- |
| 服务与依赖 | `server/pyproject.toml`、`uv.lock`：Python 3.13、FastAPI、SQLAlchemy 异步、psycopg、Alembic |
| 配置及本地环境 | `scripts/bootstrap.py`、`compose.yaml`、`.env.example`；随机本地配置、权限 0600、不覆盖旧值；PostgreSQL/Mailpit 固定镜像摘要 |
| 数据库边界 | `server/src/itime/database.py`：事务、连接池、连接/语句/锁/空闲事务超时 |
| 迁移 | `server/src/itime/migrations/`：版本基线、并发迁移锁；业务表由各模块创建 |
| 运行接口 | `main.py`：存活、数据库及迁移就绪、开发 OpenAPI；生产关闭 API 文档 |
| 错误与请求 | `errors.py`、`middleware.py`：稳定错误、保存状态、请求编号、来源/主机及实际上传体积限制 |
| 日志 | `observability.py`：JSON 路由模板/状态/延迟；不记录 query、凭据、请求正文或原始异常消息 |

2026-10-02 在 macOS、Python 3.13.14、PostgreSQL 18.6 的本地隔离环境执行：

| 命令（`server/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `uv sync --frozen` | 通过，39 个依赖包 | 锁文件可安装 |
| `uv run ruff check .` | 通过 | 代码规范与静态检查 |
| `uv run pytest -q` | **13 passed in 8.68s** | 真实 HTTP 与 PostgreSQL 集成、配置校验 |
| `uv run alembic upgrade head` | 通过 | 开发库迁移 |
| `uv build --no-sources` | 通过，生成 sdist 与 wheel | Python 分发构建 |

自动化证据源码：`server/tests/test_foundation.py`、`conftest.py`、`support_app.py`。测试自动创建 `itime_test_<随机ID>` 数据库、迁移、启动 Uvicorn、通过真实 TCP/HTTP 请求验证；最后仅清理该测试数据库。仅测试进程挂载的 `__test` 路由不进入产品部署入口。

动态覆盖：

- 存活、UTC 服务端时间、就绪、OpenAPI 和请求编号。
- 输入、未找到、未知异常错误合同；密码、query、原始异常不出现在响应或访问日志。
- 主机、Origin、跨站写请求，以及 Content-Length 和 chunked 实际体积限制。
- 拒绝测试库连接并终止现有连接后，HTTP 就绪与读请求返回 503；存活仍 200；恢复后连接池自动恢复，单次就绪故障响应低于 3 秒。
- 迁移降到 base、升到 head、重复升级与模型漂移检查；迁移版本不匹配返回 503。
- 成功提交、异常回滚、取消回滚、语句超时后的连接复用；20 个并发事务实际最多使用 2 个测试连接。

仍待验证：账号/权限、所有业务模块、AI/额度及持久化队列、完整浏览器 UI、性能容量、生产配置、备份恢复与发布。不用这 13 项基础检查代表业务用例通过。

## M2-A：邮箱账号与设备会话服务

对应 Git 提交主题：`feat: 实现邮箱账号与可撤销设备会话服务`。本节是服务模块证据；其浏览器页面在 M2-B 记录，邀请制和管理员 TOTP 尚未完成，不将 AUTH/ADM 整体标为验收通过。

实际代码：`server/src/itime/auth/`、`mail_worker.py`、迁移 `0002_accounts`。流程、工程默认值、事务与恢复合同见[账号服务设计](../设计/iTime-账号服务设计.md)。

2026-10-02 在相同本地环境、真实 Mailpit 及随机 PostgreSQL 测试库执行：

| 命令（`server/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `uv sync` | 通过，锁定新增邮件/密码/加密/邮箱校验依赖 | 47 个环境包 |
| `uv run ruff check .` | 通过 | 静态检查 |
| `uv run pytest -q` | **34 passed in 18.68s** | 21 项账号/邮件验证，加 13 项服务基础回归 |
| `uv run alembic upgrade head` | 通过 | 开发库创建账号、会话、邮件令牌、发件箱、限流桶和安全事件表 |
| `uv build --no-sources` | 通过 | sdist 和 wheel |

动态证据源码：`server/tests/test_auth.py`。通过真实 Uvicorn HTTP、真实 PostgreSQL 事务和本地 SMTP/API，验证：

- 注册→本地收信→输入注册密码验证→登录；未验证/错误密码/无效输入可辨认，公开注册不能注入管理员字段。
- 数据库仅存 Argon2 密码哈希、随机令牌摘要及待发邮件密文；发送后清空密文，API 和访问日志不回显密码、令牌、邮件正文。
- 六个并发注册只创建一个账号和一封待发验证邮件；重复注册不覆盖密码，冷却内重发不重复入队。
- 令牌过期、替换、错误用途、错误密码及重复消费；两个并发找回确认只成功一次，所有原设备失效。
- CSRF 校验、本人设备分页、他人设备隔离、远程撤销、重复退出；撤销与六个认证写请求竞争时，撤销成功后的请求均被拒绝且未写入。
- 改密始终轮换当前 Cookie，按显式选项保留或撤销其他设备；旧找回链接失效。停用状态、会话到期与管理员普通登录旁路均被拒绝。
- 失败认证持久化限流；伪造转发头不能绕过限制，窗口过期可恢复。
- 独立邮件进程启动后读取持久化发件箱；SMTP 连接失败后退避，租约未到期不抢占，到期后两个工作者只有一个发送。
- 本地 SMTP 接受 TCP 后不回复，实际在约 1 秒配置截止内退出；发送期间数据库连接占用为 0，失败保留密文供重试。
- 错误加密配置不投递，恢复原配置后同条发件记录可继续；过期邮件不会发送。
- 请求取消时，运行中的密码计算完成之前不释放并发容量；原有数据库故障/迁移往返/事务取消检查继续通过。

第一轮曾有 1 项测试断言误把 `too_short` 类型名中的 `short` 当作短密码回显；修正测试输入后通过。没有据此修改错误响应或隐藏产品问题。

尚未完成：邀请制、管理员认证页面、后续业务授权调用、离线队列及生产邮件配置。管理认证服务见 M2-C。邮件可能在“SMTP 已接收、工作者尚未记录成功”的崩溃窗口重发，保持相同 Message-ID 和一次性令牌；不宣称跨 SMTP 恰好投递一次。

## M2-B：账号与设备管理 Web 页面

对应 Git 提交主题：`feat: 完成账号与设备管理 Web 页面`。实际代码为 `web/` 的 Vue 3/TypeScript 应用、`web/e2e/auth.spec.ts` 及 `server/tests/web_environment.py`；视觉约束保存在 `.uicraft.md`。

已实现用户入口：

- 登录、注册、邮箱验证、重新发送验证邮件、找回和重设密码；邮件令牌从 fragment 读取后立即清除。
- 账号概览、修改密码、登录设备分页/刷新/退出、当前设备退出、全局退出和账号帮助。
- 浅色、深色、跟随系统及三档字号；手机、平板、电脑布局；可见标签、键盘焦点、跳转焦点、减少动效和原生确认对话框。
- 统一展示服务错误、保存状态、重试入口和请求编号；账号请求 15 秒截止，断网保留当前表单输入。
- Cookie 保持 HttpOnly；CSRF、用户对象只留内存，密码及邮件令牌不写入 Web Storage。多标签使用 `BroadcastChannel` 协调 Cookie 轮换，并防止晚到的旧身份响应覆盖新会话。

2026-10-02 在 Node.js 24.18、pnpm 11.15、Playwright Chromium 153、本地 PostgreSQL 18.6 与 Mailpit 隔离环境执行：

| 命令（`web/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `pnpm install` | 通过，锁文件和 esbuild 构建许可生效 | 前端依赖可安装 |
| `pnpm build` | 通过，Vue 类型检查及 Vite 生产构建 | 代码分割后的生产资源可生成 |
| `pnpm test:e2e` | **27 passed in 1.2m** | 9 条账号浏览器流程 × 电脑/手机/平板三种视口 |
| `uv run ruff check .` | 通过 | 新增隔离测试服务及既有 Python 代码 |

浏览器测试自动创建随机 PostgreSQL 测试库，启动真实 API、独立邮件工作进程及生产构建预览，通过 Mailpit API 获取实际发送邮件，再在浏览器操作链接；结束后终止进程并删除测试库。视口为 1440×1000、390×844、768×1024，另检查 320px 宽和最大字号。截图写入 `/tmp/itime-ui-evidence/`，不提交产品仓库。

动态覆盖：

- 完整注册→收信→错误/正确密码验证→登录→刷新保持身份；未验证账号和重复使用邮件链接有可恢复错误。
- 重新发送替换旧链接、缺失链接、找回密码、所有设备退出和新密码重新登录。
- 改密选择保留/撤销其他设备；当前 Cookie 轮换后继续写入；另一标签页改密与旧 `/me` 延迟响应竞态。
- 两设备列表、取消确认、远程退出、当前设备退出；被撤销设备刷新后回登录。
- 网络离线和账号服务首次不可达时保留输入并可恢复；15 秒截止不显示虚假成功，提示保存状态尚未确认。
- 页面标题、非空内容、无 Vite 错误层、无相关控制台异常；键盘焦点、密码显示/隐藏、主题/字号持久化、减少动效、横向溢出和 axe 自动无障碍检查。

视觉检查使用账号页实际截图，确认桌面开放双栏、手机单栏、深色、设置侧栏、设备列表及 320px 最大字号无重叠或裁切。图片概念接口持续返回“生成中”占位图，未将占位资源用于产品；账号页面按已记录的原生设计系统实现。浏览器插件在本会话未提供可调用工具，因此按测试技能要求使用真实 Playwright Chromium。

尚未验证：真实 Edge、Firefox/Safari、实体手机/平板、外部生产 SMTP、正式浏览器最低版本及全产品导航。当前页面测试不代表邀请制、管理员认证页面、后续业务、完整离线同步或 Web 首版已经完成。

## M2-C：管理员 TOTP 认证服务

对应 Git 提交主题：`feat: 实现管理员 TOTP 与恢复认证服务`。实际代码为 `server/src/itime/admin/`、迁移 `0003_admin_auth` 和 `server/tests/test_admin_auth.py`；边界见[管理员认证设计](../设计/iTime-管理员认证设计.md)。

实现范围：

- 服务器本机 CLI 创建全新管理员及重置 TOTP；拒绝覆盖或提升既有普通账号。
- 密码再次确认后设置 TOTP，确认成功才启用；TOTP secret 加密保存，设置令牌和会话只保存摘要。
- 十个一次性高熵恢复码，只返回一次、数据库仅存带服务密钥摘要；支持 TOTP 重新生成并原子废止全部旧码。
- 独立管理 Cookie、CSRF、8 小时固定会话和认证版本；管理会话不复用普通 Cookie。
- TOTP 接受前后一个 30 秒窗口但保存最后成功计数器；同一验证码和同一恢复码并发最多成功一次。
- 管理密码修改和公开邮箱找回均撤销管理会话；CLI 重置撤销普通/管理会话并删除旧 TOTP、恢复码与设置链接。

2026-10-02 在本地隔离 PostgreSQL 环境执行：

| 命令（`server/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `uv run ruff check .` | 通过 | 管理认证及全部 Python 静态检查 |
| `uv run pytest -q tests/test_admin_auth.py` | **9 passed in 10.15s** | 管理认证专项真实 HTTP/PostgreSQL 测试 |
| `uv run pytest -q` | **43 passed in 25.18s** | 管理认证、邮箱账号及服务基础全量回归 |
| `uv run alembic current && uv run alembic check` | `0003_admin_auth (head)`，无模型漂移 | 开发库迁移与 ORM 一致 |

动态覆盖 CLI 创建、设置链接错误密码不消费、二维码 URI、TOTP 设置、密文/摘要存储、Cookie 属性、日志不泄露、普通账号拒绝、TOTP 并发防重放、恢复码并发单次消费与轮换、CSRF、重复退出、会话过期/停用、服务器重置、管理改密、公开找回撤销管理会话及禁止提升既有账号。

当前会话期限、恢复码和本机恢复流程属于可配置工程默认，用户尚未确认最终运营规则。管理认证页面见 M2-D；敏感业务操作重新验证、管理员停用/交接 UI 和全部后台业务功能仍未完成，因此不把 ADM-01 或管理后台标为验收通过。

## M2-D：管理员认证 Web 页面

对应 Git 提交主题：`feat: 完成管理员认证与安全设置页面`。实际代码为 `web/src/pages/AdminAuthPage.vue`、`AdminPage.vue`、`web/src/components/Admin*.vue`、`RecoveryCodesPanel.vue` 和 `web/e2e/admin-auth.spec.ts`。

已实现：

- `/admin/setup` 读取一次性 fragment 后立即清除地址栏，要求再次输入密码，显示真实 TOTP 二维码和手工密钥，验证成功后只显示一次恢复码。
- `/admin/login` 使用分段控件选择身份验证器或恢复码；普通登录和管理登录使用不同 Cookie、内存 CSRF 与页面状态。
- `/admin/security`、`/admin/password`、`/admin/recovery`、`/admin/appearance` 提供管理身份、会话到期、改密、恢复码轮换、主题/字号和退出入口。
- 恢复码支持复制和真实文本下载；用户确认离线保存前不能继续，离开页面会二次确认。密码、TOTP secret、恢复码和设置令牌不写入 Web Storage。
- 页面覆盖正常、输入错误、链接缺失、验证码重放、恢复码失效、未认证跳转和服务端错误；所有错误保留请求编号与保存状态。

2026-10-02 在 Playwright Chromium 153、本地随机 PostgreSQL 测试库及生产构建预览中执行：

| 命令（`web/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `pnpm install --frozen-lockfile` | 通过 | Vue、二维码与测试依赖锁定安装 |
| `pnpm build` | 通过 | Vue TypeScript 检查和 Vite 生产构建 |
| `pnpm test:e2e` | **42 passed in 1.4m** | 14 条账号/管理流程 × 电脑、手机、平板三种视口 |
| `pnpm exec playwright test e2e/admin-auth.spec.ts --grep '一次性链接'` | **3 passed in 11.1s** | 最终恢复码焦点、下载、截图和无障碍修复复验 |
| `pnpm exec playwright test e2e/admin-auth.spec.ts --grep '未认证跳转'` | **3 passed in 10.2s** | 最终 320px、大字号、深色、外观和无障碍复验 |

管理 UI 专项覆盖 TOTP 设置、二维码、错误密码/验证码、恢复码一次展示与下载、TOTP 重放拒绝、恢复码登录与轮换、管理改密撤销另一会话、未认证跳转、主题/字号持久化、320px 最大字号、横向溢出、控制台异常和 axe 检查。测试基础在随机数据库中建立独立管理员，敏感引导数据只写入权限 0600 的临时文件，结束时删除。

视觉检查使用 1440×1000、390×844 和 768×1024 的真实页面截图，核对导航、标题、二维码、恢复码列表、表单密度、深色和窄屏布局。首次实测发现错误提示消失会让按钮在按下/释放间位移并取消提交，已通过稳定反馈区修复；另修正恢复码独立结果页的一级标题和焦点。图片概念服务仍只返回生成中占位图，未用于产品或作为验收参考；页面与现有账号设计系统一致。

仍未验证真实 Edge 与实体设备。后台用户、邀请、额度、AI 配置、日志、审计和质检页面尚未实现，管理员认证页面通过不表示完整后台通过。

## M2-E：注册策略、邀请与用户状态服务

对应 Git 提交主题：`feat: 实现邀请注册与后台用户状态服务`。实际代码为 `server/src/itime/admin/management.py`、账号注册接口、迁移 `0004_user_management` 和 `server/tests/test_admin_management.py`；详细边界见[注册与用户状态设计](../设计/iTime-注册与用户状态设计.md)。

已实现：

- 开放/邀请注册策略带版本更新；策略切换和注册事务通过共享/排他锁确定先后，不把所有并发注册串行化。
- 邀请绑定规范化邮箱、单账号消费、可到期和可撤销；链接只在创建响应显示一次，数据库只存令牌摘要。
- 同一邀请注册在响应丢失后可对同一账号安全重试，不产生第二个账号、不覆盖首次密码；错误邮箱、撤销和无令牌均拒绝。
- 普通用户账号停用/恢复与 AI 冻结/恢复使用独立状态及乐观版本。停用原子撤销设备会话和未用邮件令牌；AI 冻结保留普通会话及手动功能边界。
- 用户和邀请列表支持筛选与稳定游标分页。管理员账号禁止从普通用户接口修改；所有管理动作记录操作者、目标和请求编号。

2026-10-02 在本地随机 PostgreSQL 测试库及真实 Uvicorn HTTP 中执行：

| 命令（`server/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `uv run ruff check .` | 通过 | 新增管理服务及全部 Python 静态检查 |
| `uv run pytest -q tests/test_admin_management.py` | **2 passed in 7.17s** | 邀请、并发注册、策略版本、状态分离、会话撤销、CSRF 和审计 |
| `uv run pytest -q` | **45 passed in 29.27s** | 服务基础、账号、管理认证和本模块全量回归 |
| `uv run alembic upgrade head && uv run alembic check` | `0004_user_management (head)`，无模型漂移 | 开发库迁移与 ORM 一致 |
| `uv build --no-sources` | 通过 | sdist 和 wheel |

Q-AUTH-03 仍未全部定稿：本模块要求管理员逐个指定邀请有效小时数，没有把界面建议值写成固定运营期限；没有实现申诉；AI 在途作业的取消、继续与结算留待 AI/额度状态机确定。对应注册和后台管理页面见 M2-F；本节只记录服务证据。

## M2-F：邀请注册与用户状态管理 Web 页面

对应 Git 提交主题：`feat: 完成邀请注册与后台用户管理页面`。实际代码为 `web/src/components/AdminRegistrationSettings.vue`、`AdminUserManagement.vue`、邀请注册页面接入、`web/e2e/admin-management.spec.ts` 及隔离环境数据准备。

已实现：

- `/admin/registration` 切换开放/邀请注册；创建邮箱绑定邀请、一次复制链接、按状态筛选和撤销未使用邀请。
- `/register` 根据公开策略显示注册入口；邀请 fragment 在首次进入和同页打开新链接时均立即清除，无邀请时不能在邀请制下提交。
- `/admin/users` 搜索和筛选用户，分别冻结 AI、停用账号及恢复；确认对话框明确对会话、邮件链接和手动功能的影响。
- `/account` 展示本人 AI 暂停状态；冻结 AI 不退出普通账号，停用账号则使现有页面刷新后返回登录。
- 管理列表覆盖空、加载、失败、分页和版本冲突恢复；管理员账号不提供普通用户状态按钮。

2026-10-02 在 Playwright Chromium 153、本地随机 PostgreSQL、真实 API、邮件工作进程、Mailpit 和生产构建预览中执行：

| 命令（`web/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `pnpm build` | 通过 | Vue TypeScript 检查和 Vite 生产构建 |
| `pnpm exec playwright test e2e/admin-management.spec.ts` | **9 passed in 24.2s** | 3 条邀请/状态/恢复流程 × 电脑、手机、平板 |
| `pnpm test:e2e` | **51 passed in 1.7m** | 17 条账号、管理认证和用户管理流程 × 三种视口全量回归 |
| `uv run ruff check .` | 通过 | 更新后的隔离浏览器环境及全部 Python 静态检查 |
| `uv run pytest -q` | **45 passed in 31.14s** | 服务端全量回归 |

动态覆盖邀请模式切换、邮箱错误后继续使用原邀请、真实注册和邮件入队、链接只显示一次且 fragment/Web Storage 不留令牌、邀请状态刷新、AI 冻结后普通账号继续、停用会话即时失效、恢复后重新登录、筛选空状态、网络失败恢复、两个后台页面的版本冲突、输入校验焦点、控制台异常、axe 及横向溢出。

浏览器验证使用 1440×1000、390×844、768×1024，并在每个项目中额外切换到 320×700。截图位于 `/tmp/itime-ui-evidence/*admin-management*`；实际检查确认长邮箱、邀请链接、操作按钮和移动导航没有重叠或越界。第一轮测试使用不可见 radio 的 `check()` 导致标签拦截，改为真实点击分段标签；随后同页打开邀请链接暴露 fragment 未重新消费的产品问题，已通过监听路由 hash 并立即替换历史记录修复。

仍未验证真实 Edge、实体手机/平板、正式邀请交付渠道和外部生产邮件。邀请默认运营期限、申诉及 AI 在途作业处理仍待确认；额度、AI 配置、正文/日志、审计和质检后台仍未实现。

## M3-A：目标、项目与任务核心服务

对应 Git 提交主题：`feat: 实现目标与任务核心服务`。实际代码为 `server/src/itime/tasks/`、迁移 `0005_tasks`、路由接入及 `server/tests/test_tasks.py`；字段、事务与恢复合同见[任务核心服务设计](../设计/iTime-任务核心服务设计.md)。

已实现：

- 项目/目标创建、搜索分页、任务数量统计、详情和带版本编辑；任务可不属于项目并留在收集箱。
- 任务名称、正文、用户预计耗时、四档含未设置的优先级、标签、HTTP(S) 链接、顺序步骤，以及明确完成/重新打开。
- 任务搜索和状态、项目/收集箱、优先级、标签组合筛选；稳定游标分页；列表只返回步骤进度和链接数，详情返回完整资料。
- 步骤新增、编辑、勾选、重排和移除；步骤勾完不自动完成任务。
- 最多 100 个任务的完成/重开、优先级、项目和增删标签原子批量操作。
- 所有者复合外键、任务/项目乐观版本、同账号写入顺序、批量锁顺序、请求内容摘要及 `operation_id` 结果重放；业务结果、操作记录和后续同步变更事件在同一事务提交。

2026-10-02 在本地 PostgreSQL 18.6、真实 Uvicorn HTTP 和随机测试数据库中执行：

| 命令（`server/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `uv run pytest -q tests/test_tasks.py` | **5 passed in 6.42s** | 任务专项真实 HTTP/PostgreSQL、双账号和双设备并发 |
| `uv run pytest -q` | **50 passed in 31.27s** | 服务基础、账号、后台认证/用户管理和任务全量回归 |
| `uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `uv run alembic downgrade 0004_user_management && uv run alembic upgrade head` | 通过 | 新迁移可回退并重新应用 |
| `uv run alembic current && uv run alembic check` | `0005_tasks (head)`，无模型漂移 | 开发库与 ORM 一致 |
| `uv build --no-sources` | 通过 | sdist 和 wheel |

动态覆盖：

- 相同操作编号顺序及双设备并发重试都只创建一个任务并返回相同结果；同编号不同内容被拒绝。
- 两个账号的项目、任务与筛选结果隔离；跨账号项目关联和对象读取均返回未找到。
- 标签空白规范化/去重、正文/标签/项目组合搜索、项目任务计数、三条任务跨页无遗漏。
- 完整任务编辑、陈旧版本冲突、步骤增改勾选/重排/移除；步骤完成后任务仍为未完成，只有明确完成操作改变状态。
- 双设备持同一版本并发完成只有一个成功；批量中任一陈旧版本使整批不保存，正确重试后统一提交且再次重放不重复变更。
- 数据库中任务、标签、操作记录和变更事件数量与预期一致；输入条数和正文长度受限。

本节不包含任务 Web 页面。截止时间、重复实例、跨天拆分参数、归档、任务回收站及语音/AI 校对仍未实现，需先完成 Q-TASK-01/Q-TASK-02/Q-DATA-01 的对应细化，不能将本批测试标为完整 TASK-01/TASK-02 验收通过。

## M3-B：目标、项目与任务 Web 页面

对应 Git 提交主题：`feat: 完成任务与目标 Web 页面`。实际代码为 `web/src/pages/TasksPage.vue`、`web/src/lib/tasks.ts`、任务工作区组件与样式，以及 `web/e2e/tasks.spec.ts`。登录后的默认入口已从账号设置调整为 `/tasks`，账号设置仍可从产品导航到达。

已实现：

- 桌面使用产品/项目导航、任务列表、任务详情三栏；平板使用两行导航和列表/详情双栏；手机使用列表与全屏详情切换。
- 项目和目标创建、编辑、任务数量展示及项目/收集箱筛选。
- 任务名称、正文、所属项目、优先级、预计分钟数、标签和 HTTP(S) 链接的创建与完整编辑。
- 步骤新增、改名、勾选、重新打开、上下移动及移除；步骤完成后仍要求用户单独点击任务完成。
- 名称/正文搜索、状态/优先级/标签组合筛选、稳定分页，以及完成、重开、优先级、项目、增删标签六类批量操作。
- 写入失败保留输入及服务端请求编号；网络恢复后用相同操作内容重试。任务或批量版本冲突不覆盖新数据，展示服务器最新版并要求用户明确选择保留填写重试或采用服务器内容。
- 加载、空结果、AI 冻结、离线/超时、失败、成功、并发冲突、未保存离开提醒、320px 大字号和无障碍状态。

2026-10-02 在本地隔离 PostgreSQL、真实 API/邮件进程和 Chromium 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd web && pnpm build` | 通过 | 严格 TypeScript 检查和生产构建 |
| `cd web && pnpm test:e2e` | **63 passed in 2.1m** | 21 个账号、后台和任务场景分别运行于 1440×1000、390×844、768×1024 |
| `cd server && uv run pytest -q` | **50 passed in 31.14s** | 服务端全量回归 |
| `cd server && uv run ruff check .` | 通过 | Python 静态检查 |

任务浏览器场景通过真实页面注册、Mailpit 邮件验证和登录后创建数据；覆盖项目创建/改名、完整任务创建与刷新持久化、步骤草稿跨其他步骤写入保留、步骤勾选不自动完成、步骤排序、明确完成、搜索筛选、双页面编辑冲突、批量冲突全成全败、成功批量设置优先级、列表网络失败恢复、离线创建原输入保留与恢复后单次创建。每个项目还切换至 320×700 和大字号检查横向溢出；axe 与控制台检查通过。

截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-tasks-complete-detail.png` 及对应 `*-tasks-320-large-font-list.png`。人工检查确认桌面三栏扫描关系、平板两行导航、手机单栏编辑、长表单、步骤操作和 320px 大字号无内容重叠。浏览器插件不可用，按既有测试方案使用 Playwright Chromium；尚未执行真实 Edge 和实体设备验证。

本阶段仅覆盖 M3-A 已有服务字段。截止时间、重复实例、跨天拆分/不可拆、归档、任务回收站、语音转写及 AI 校对仍未实现，不能据此把完整 REQ-TASK-01/02 标为验收通过。

## M3-C：用户时区、学期与节次服务

对应 Git 提交主题：`feat: 实现时间设置与学期节次服务`。实际代码为 `server/src/itime/calendar/`、迁移 `0006_time_terms`、账号注册时区接入、`server/tests/test_calendar_foundation.py` 和注册浏览器测试；完整语义见[时间、课表与重复规则设计](../设计/iTime-时间课表与重复规则设计.md)。

已实现：

- 注册页面读取浏览器 `Intl` IANA 时区并随新账号提交；服务端校验后保存。旧账号没有设置时明确返回未初始化和空时区，不把 UTC 当作用户选择。
- 本人时间设置读取和带版本更新；周起点固定为周一，活动日边界固定为当地 04:00，时区修改不触碰其他业务数据。
- 学期创建、详情、稳定游标列表和带版本编辑；日期范围最多 400 天，第一周锚点必须是包含开始日的周一，学期可重叠。
- 每学期完整节次表原子替换；最多 30 节，编号和名称唯一，本地结束时间晚于开始时间，相邻端点允许，重叠整批拒绝。
- 所有读取按账号隔离；时间设置和学期写入复用 CSRF、`operation_id` 内容摘要重放、乐观版本及 `change_events`，不存在和他人对象统一返回 404。

2026-10-02 在 PostgreSQL 18.6、真实 Uvicorn HTTP、生产前端构建和 Chromium 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd server && uv run pytest -q tests/test_calendar_foundation.py` | **6 passed in 9.05s** | 时间设置、学期、节次、含业务数据的迁移往返、双账号和双会话并发专项 |
| `cd server && uv run pytest -q` | **56 passed in 37.18s** | 服务端全量回归 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv run alembic downgrade 0005_tasks` 后重新 `upgrade head` | 通过 | 已有开发库迁移回退及再升级 |
| `cd server && uv run alembic check` | 无模型漂移 | `0006_time_terms (head)` 与 ORM 一致 |
| `cd server && uv build --no-sources` | 通过 | sdist 和 wheel |
| `cd web && pnpm build` | 通过 | 注册时区接入的 TypeScript 与生产构建 |
| `cd web && pnpm test:e2e` | **63 passed in 2.1m** | 21 个场景在桌面、手机、平板全量回归 |

动态覆盖首次读取、浏览器注册时区、无效 IANA 名称不回显、当地 03:59/04:00（含纽约夏令时切换日）、400 天边界、周一锚点、允许学期重叠、节次相邻/重叠/重复/带偏移时间、失败后原数据不变、无变化不增版本、操作重放、陈旧版本、跨账号 404，以及两个已登录设备持同一版本并发修改时仅一个成功。迁移专项还先写入真实时区、学期、变更事件和操作记录，再验证 downgrade 清除本功能旁路记录、重新 upgrade 可用。

本节没有实现课程、固定安排、重复系列、单次例外、occurrence 查询、日历页面或导入，因此 CAL-01、CAL-03 和 TIME-01 仍只完成基础部分，不能标为完整验收通过。

## M3-D：课程、固定安排与重复 occurrence 服务

对应 Git 提交主题：`feat: 实现固定安排与重复日历查询`。实际代码为 `server/src/itime/calendar/recurrence.py`、`time_math.py`、`series_service.py`、日历路由扩展、迁移 `0007_calendar_series` 和 `server/tests/test_calendar_series.py`。

已实现：

- 手工课程与固定安排系列创建、详情、筛选分页及带版本编辑；课程必须属于本人学期，可按连续节次或显式本地时刻设置，节次时刻保存为系列快照。
- 结构化支持不重复、每日/每 N 日、工作日、每周/每 N 周指定星期、每月指定日期及每年指定月日；课程另按学期周一锚点和有效周次展开。
- 单系列最多 366 次；日历查询采用 `[start,end)` 日期范围，最多 93 天及 2000 个 occurrence；跨午夜安排按区间相交返回。
- 本地时间使用 IANA 时区解析。DST 不存在时刻不自动平移；单次安排拒绝，重复系列保留警告 occurrence。DST 重复时刻必须从服务端返回的候选 UTC offset 中明确选择。
- 占用时间的重叠安排均保留；写入响应返回冲突列表和是否还有更多，查询结果双向标记冲突；首尾相接和不占用时间不算冲突。
- 系列写入复用 `operation_id`、账号级写入顺序、乐观版本与同步变更事件；跨账号详情、学期和修改统一不可见。

2026-10-02 在本地 PostgreSQL 18.6 和真实 Uvicorn HTTP 中执行：

| 命令（`server/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `uv run pytest -q tests/test_calendar_series.py` | **6 passed in 9.49s** | 六类重复规则、课程周次、节次、DST、跨日、冲突、权限、并发及迁移 |
| `uv run pytest -q` | **62 passed in 41.96s** | 服务端全量回归 |
| `uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `uv run alembic downgrade 0006_time_terms` 后重新 `upgrade head` | 通过 | `0007` 迁移可回退并恢复 |
| `uv run alembic check` | 无模型漂移 | `0007_calendar_series (head)` 与 ORM 一致 |
| `uv build --no-sources` | 通过 | sdist 和 wheel |

专项动态验证了：上海时区 UTC 转换、跨午夜在次日范围仍可见、相邻区间无冲突、重叠区间双方保留、空闲安排不参与冲突、隔日/工作日/双周、31 日跳月、闰日跳年、366 次上限、课程指定周、非法周次/节次、纽约 DST 缺失与重复时刻、列表游标、跨账号 404、同版本并发仅一个成功，以及含系列数据的迁移回退清理。

本提交边界不含取消/调课/覆盖例外及“本次及以后”系列拆分，后续实现见 M3-E；日历仍无用户页面，因此 CAL-01/CAL-03 尚未完整验收。

## M3-E：单次例外与系列拆分服务

对应 Git 提交主题：`feat: 实现日历例外与系列拆分`。实际代码为 `server/src/itime/calendar/exception_service.py`、日历查询/路由扩展、迁移 `0008_calendar_exceptions` 和 `server/tests/test_calendar_exceptions.py`。

已实现：

- 对系列 occurrence 创建和编辑取消、调课及内容覆盖例外，并提供本人例外列表；同一原始日期只允许一个例外。
- 取消实例仍作为历史投影返回但不再占用时间；调课保留原始 occurrence 标识并参与新时段冲突；覆盖可单独修改名称、说明、地点和占用状态。
- 例外以原始日期绑定，整个系列改时刻后仍能命中。系列重复规则若会移除已有例外日期，编辑整笔拒绝并返回受影响例外 ID。
- “本次及以后”在单一事务中截断原系列、创建继任系列并迁移分界点之后的有效例外；首个实例引导使用整个系列编辑，未来例外会被新规则孤立时不保存。
- 已发生的 occurrence 不能作为拆分点；未来恰逢 DST 缺失时刻的 occurrence 仍可作为日期分界，并在后继规则中改为有效时刻。
- 例外与系列分别保留版本，实际例外变化同步递增系列版本；操作重放、跨账号 404、双设备版本竞争和拆分单赢家沿用既有合同。

2026-10-02 在本地 PostgreSQL 18.6 和真实 Uvicorn HTTP 中执行：

| 命令（`server/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `uv run pytest -q tests/test_calendar_exceptions.py` | **8 passed in 9.39s** | 取消/调课/覆盖、DST、冲突、孤儿保护、拆分、并发及迁移专项 |
| `uv run pytest -q tests/test_calendar_foundation.py tests/test_calendar_series.py tests/test_calendar_exceptions.py` | **19 passed in 20.37s** | 时间与课表服务组合回归 |
| `uv run pytest -q` | **70 passed in 44.11s** | 服务端全量回归 |
| `uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `uv run alembic downgrade 0007_calendar_series` 后重新 `upgrade head` | 通过 | `0008` 迁移可回退并恢复 |
| `uv run alembic check` | 无模型漂移 | `0008_calendar_exceptions (head)` 与 ORM 一致 |

专项动态验证调课从原查询范围移出并在新日期出现、调课后冲突、取消后释放占用、单次覆盖将空闲改为占用、非法/歧义 DST 调课、重复例外、陈旧版本、整个系列改时刻后例外继续命中、重复规则孤儿保护、拆分后实例无遗漏无重复、未来例外迁移、拆分重放及并发拆分仅创建一个继任系列。迁移测试在存在真实例外、变更事件和幂等记录时完成回退与再升级。

本提交边界当时尚无日历用户页面和导入；后续三视口 UI 证据见 M3-I。

## M3-F：任务截止、重复实例与拆分服务

对应 Git 提交主题：`feat: 实现任务日期与重复实例服务`。实际代码为 `server/src/itime/tasks/schedule_service.py`、`schedule_router.py`、任务模型/响应扩展、共享输入基类、迁移 `0009_task_schedules` 和 `server/tests/test_task_schedules.py`。

已实现：

- 任务可配置无截止、日期型截止或定时截止。日期型截止保持日历日期，并以任务时区次日 `00:00` 的最早有效瞬间作为逾期边界；定时截止保存 IANA 时区、本地时刻和首个 UTC 时间点。
- 任务默认可拆、最小连续块 15 分钟，可分别修改。任务重复复用日历的六类结构化规则与 366 次上限，实例查询范围最多 93 天、返回最多 2000 项。
- 重复 occurrence 独立保存完成、重新打开、跳过和步骤进度；任务模板本身保持未完成，原任务完成接口及批量完成不会误改整个系列。
- 单次调期保留原 occurrence key；补做新增关联的一次性实例，不移动或自动完成原实例。DST 缺失实例仍可明确跳过/完成并保留警告，调期到无效时刻则拒绝。
- occurrence 首次产生状态时冻结步骤数量；此后模板步骤可改名和排序，但不能增删，避免历史进度分母或已完成步骤引用失效。保持已完成状态只调整步骤时不重写首次完成时间。
- “本次及以后”原子克隆任务字段、步骤、链接和标签，截断原规则，迁移未来状态并按新步骤 ID 重映射进度；已到期实例不能拆分，同一前序只允许一个直接后继。
- 查询在数据库层按起止日期索引缩小候选，同时补入调期/补做进入范围的任务；日期型任务不随显示时区漂移，定时任务按 UTC 落入用户当前时区范围。

2026-10-02 在本地 PostgreSQL 18.6 和真实 Uvicorn HTTP 中执行：

| 命令（`server/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `uv run pytest -q tests/test_task_schedules.py` | **9 passed in 13.41s** | 截止、重复实例、步骤进度、跳过/调期/补做、DST、拆分、并发和迁移专项 |
| `uv run pytest -q tests/test_tasks.py tests/test_task_schedules.py tests/test_calendar_exceptions.py` | **22 passed in 20.69s** | 旧任务接口、任务日期与日历例外组合回归 |
| `uv run pytest -q` | **79 passed in 57.74s** | 服务端全量回归 |
| `uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `uv run alembic downgrade 0008_calendar_exceptions` 后重新 `upgrade head` | 通过 | `0009` 无拆分数据时可回退并恢复 |
| `uv run alembic check` | 无模型漂移 | `0009_task_schedules (head)` 与 ORM 一致 |
| `cd web && pnpm build` | 通过 | 现有任务页面对新增响应字段的类型与生产构建回归 |
| `cd web && pnpm exec playwright test e2e/tasks.spec.ts` | **12 passed in 35.9s** | 任务页面四个真实流程 × 桌面、手机、平板 |

迁移含真实日程和实例状态时可往返；若数据库已有任务系列拆分后继，降级会明确拒绝，避免删除日程关系后把后继静默暴露为普通重复任务。专项还验证了上海/纽约/哈瓦那时区、当地 04:00 之外的日期日末、DST 缺失实例状态、重复时刻 offset、用户改时区不改既有截止、操作重放、孤儿保护、步骤冻结、完成时间稳定及顺序/并发拆分。

任务日期和重复设置的 Web 编辑器及验证见 M3-H；实例级完成、跳过、调期和补做仍待统一日历页面。

## M3-G：关联补课 occurrence 服务

对应 Git 提交主题：`feat: 实现关联补课日历实例`。实际代码为 `server/src/itime/calendar/addition_service.py`、日历模型/查询/路由扩展、迁移 `0010_calendar_additions` 和 `server/tests/test_calendar_additions.py`。

已实现：

- 每个原 occurrence 可创建一个独立补课；原课取消、调课或内容覆盖与补课记录可同时存在，补课不会移动或恢复原课。
- 补课保存自己的本地日期、墙上时刻、IANA 时区、占用状态和可选内容覆盖；创建默认采用用户当前时区，后续省略时区的更新保持原时区。
- 补课支持取消、恢复和完整编辑，不提供硬删除；无变化更新不增版本，实际变化同时递增补课和所属系列版本。
- occurrence 查询会补入实际日期落入范围但原系列已远离范围的补课，并把基础实例、例外和补课统一纳入 2000 条上限、UTC 精确范围判断及冲突计算；同系列重叠也会报告。
- 系列规则修改先验证补课来源不会孤立；未来系列拆分按来源日期迁移补课。一个前序系列只允许一个直接后继，避免顺序重复拆分产生重叠分支。
- 调课记录新增独立时区并对旧数据回填，系列后续改时区不会改变既有调课时间点。数据库同时补齐重复规则形状约束。
- 所有写入沿用账号隔离、系列写锁、乐观版本、`operation_id` 内容摘要重放和 `change_events`；有补课数据或独立调课时区无法无损降级时明确拒绝，避免静默删除或改变时间点。

2026-10-02 在本地 PostgreSQL 18.6、真实 Uvicorn HTTP 和生产构建中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd server && uv run pytest -q tests/test_calendar_additions.py` | **6 passed in 17.32s** | 补课生命周期、DST、跨午夜、范围重入、冲突、权限、并发、拆分和迁移专项 |
| `cd server && uv run pytest -q` | **85 passed in 72.56s** | 服务端全量回归 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv run alembic current && uv run alembic check` | `0010_calendar_additions (head)`，无模型漂移 | 开发库迁移与 ORM 一致 |
| `cd server && uv build --no-sources` | 通过 | sdist 和 wheel |
| `cd web && pnpm build` | 通过 | 现有前端对服务扩展的 TypeScript 与生产构建回归 |

专项动态验证了取消原课后创建补课、同系列原课冲突、取消释放占用、恢复和内容覆盖、操作重放及操作号异内容拒绝、用户改时区不改变既有补课、纽约 DST 缺失/重复时刻、跨午夜、原系列范围外补课查询、跨账号 404、双设备创建/更新单赢家、规则缩短孤儿保护、拆分迁移和顺序二次拆分拒绝。迁移测试验证有补课或独立调课时区无法无损保留时拒绝降级，满足无损条件后可回退至 `0009`、移除新版事件/幂等记录并重新升级，已有调课时区可重新回填。

本提交边界仅含服务能力；后续课表和日历 Web 页面及 UI 证据见 M3-I。

## M3-H：任务截止与重复 Web 编辑器

对应 Git 提交主题：`feat: 接入任务日期与重复设置`。实际代码为 `web/src/components/TaskScheduleEditor.vue`、`web/src/lib/task-schedules.ts`、任务详情与样式扩展，以及 `web/e2e/tasks.spec.ts` 的真实浏览器场景。

已实现：

- 已保存任务可设置无截止、日期型截止或具体日期与时刻；首次保存采用用户已初始化的浏览器 IANA 时区，后续显示并保持任务原时区。
- 可配置每日、工作日、每周指定星期、每月和每年重复，以及结束日期或次数；可拆设置和 1—1440 分钟最小连续时长单独保存。
- 日期设置沿用任务聚合版本、内容指纹和操作编号。版本冲突读取服务器最新版，允许保留当前填写并基于新版本重试，或明确改用服务器设置。
- 重复任务不展示任务级“标为完成”，避免把整个模板误当作单次实例；实例完成、跳过、调期和补做留在统一日历操作。
- 桌面保持三栏结构，平板保持列表/详情双栏，手机使用全屏详情；日期表单在窄屏重排，复选框、重复结束和保存操作保持可读可触控。

2026-10-02 在隔离 PostgreSQL、真实 API/邮件进程和 Chromium 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查和生产构建 |
| `cd web && pnpm exec playwright test e2e/tasks.spec.ts` | **15 passed in 39.5s** | 5 个任务流程分别运行于 1440×1000、390×844、768×1024 |

新增流程通过真实注册、邮件验证和任务创建，在页面保存 3 次每日定时截止，随后直接查询真实 `/api/v1/task-occurrences` 验证三个实例的本地时刻、独立版本、不可拆和 30 分钟最小块；刷新后再次核对表单持久化和重复任务完成入口。三个视口均执行 axe、控制台和根级横向溢出检查，另在 320×700 大字号下检查日期表单。截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-tasks-schedule-detail.png` 与 `desktop-tasks-schedule-320-large-font.png`。

本提交边界当时尚无统一日历及实例级操作入口；后续入口见 M3-I。归档、回收站、语音或 AI 校对仍未实现。

## M3-I：课表与统一日历 Web 页面

对应 Git 提交主题：`feat: 完成课表与统一日历 Web 页面`。实际代码为 `web/src/pages/CalendarPage.vue`、`web/src/lib/calendar.ts`、`web/src/components/Calendar*.vue`、共享产品导航、响应式日历样式及 `web/e2e/calendar.spec.ts`。

已实现：

- 任务、日历、设置之间的共享产品导航；登录后可直接进入 `/calendar`，未登录访问仍由真实会话检查返回登录。
- 首次缺少时间设置时保存浏览器 IANA 时区；时间设置页允许改用本机或明确输入其他 IANA 时区，既有系列继续保留其保存时区。
- 学期创建/编辑和完整节次表编辑；课程可选择学期、有效周次、星期及连续节次，也可使用明确时刻。
- 固定安排支持不重复、每日、工作日、每周、每月和每年规则，以及结束日期或次数、跨日和占用状态。
- 日历并行读取课程/固定安排 occurrence 与任务 occurrence；桌面使用周/月网格和详情栏，840px 以下使用日期议程与全屏详情。
- 所有定时内容按用户当前显示时区投影；日期型任务保持业务日期。跨午夜标记次日，DST 缺失时刻拒绝，DST 重复时刻要求选择明确 UTC 偏移。
- 单次取消、恢复、调课、内容覆盖、关联补课、补课取消/恢复；任务实例完成、跳过、重新打开和调期。
- “整个系列”和“本次及以后”编辑；按次数拆分自动改为剩余次数，不增加总实例。学期节次变化后编辑旧课程时保留课程快照时刻，不静默套用新节次。
- 写入保持操作号重试和聚合版本。版本冲突展示服务器与当前填写，必须明确选择覆盖或采用服务器内容；冲突安排保留双方并显示说明。
- 课程、固定安排、任务截止和取消状态均有独立视觉编码；加载、空、错误、冲突、AI 冻结和成功状态均可达。

2026-10-02 在本地隔离 PostgreSQL、真实 API/邮件进程和 Chromium 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `cd web && pnpm exec playwright test e2e/calendar.spec.ts` | **12 passed** | 4 个日历完整流程 × 1440×1000、390×844、768×1024 |
| `cd web && pnpm exec playwright test e2e/tasks.spec.ts` | **15 passed in 43.9s** | 共享导航接入后的任务页面回归 |
| `cd web && pnpm test:e2e` | **78 passed in 2.8m** | 账号、后台、任务和日历的全量 Chromium 回归 |
| `cd server && uv run pytest -q` | **85 passed in 72.29s** | 服务端真实 HTTP/PostgreSQL 全量回归 |

日历浏览器测试覆盖真实注册、邮件验证和登录，手工创建学期/节次/课程/固定安排，统一展示任务截止，冲突保留与解释，单次取消/恢复/调课、补课创建/取消/恢复，双页面系列版本冲突与草稿保留，未来系列拆分总次数不膨胀，跨时区投影、跨午夜、DST 缺失及重复时刻。三个视口均检查 axe、控制台、API 500、横向溢出；另在 320×700 最大字号下验证议程布局。截图位于 `/tmp/itime-ui-evidence/*-calendar-*.png`，并已人工检查桌面月网格、手机议程、平板单栏详情和 320px 大字号。

本节不包含 Excel、ICS、截图或订阅导入，也未实现任务实例补做、归档和回收站。真实 Edge、实体设备和全产品端到端验收仍待后续阶段。

## M3-J：Excel 与 ICS 文件导入服务

对应 Git 提交主题：`feat: 实现 Excel 与 ICS 文件导入服务`。实际代码为 `server/src/itime/calendar/import_{parsers,schemas,service,router}.py`、来源及候选模型、迁移 `0011_calendar_file_imports` 和 `server/tests/test_calendar_imports.py`。

已实现：

- 登录用户可下载真实 XLSX 模板，并以原始请求正文上传 `.xlsx` 或 `.ics`；操作号和文件摘要共同保护上传重试。原文件不落库，解析结束后仅保存受限的来源元数据和结构化候选。
- XLSX 严格限制 5 MiB、500 条、32 列、4 个工作表、压缩项及解压体积；拒绝公式、宏、嵌入对象、外部关系、危险压缩比、未知/重复列和稀疏超范围工作表。
- ICS 严格限制 2 MiB、500 个事件和组件/属性数量；支持 UTC、IANA 时区、浮动时间提示、单日全天事件、`DTEND`/`DURATION` 和可无损映射的有限重复规则。复杂 RRULE、未知时区、EXDATE/RDATE 和 `RECURRENCE-ID` 不会被静默简化，而是保留为可编辑的无效候选。
- 候选保留原始行摘要、标准化草稿、错误、警告、UID/RECURRENCE-ID、内容指纹、版本和确认结果；校对窗口为 7 天，过期后只读并要求重新上传。
- 候选可逐项校对、分批确认或明确跳过；每次最多确认 50 条。选中项在同一事务内全部写入或全部回滚，冲突保留双方并返回明细。
- 同来源格式的 UID/RECURRENCE-ID 或无 UID 内容指纹用于精确去重；同 UID 内容变化不覆盖旧安排并明确提示；与手工安排内容相同只提示疑似重复。
- 确认后的日历系列关联来源并标记 `source_type=import`；来源信息随系列和 occurrence 返回。跨账号访问统一 404，同批并发确认只有一个版本成功。

2026-10-02 在本地 PostgreSQL 18.6 和真实 Uvicorn HTTP 中执行：

| 命令（`server/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `uv run pytest -q tests/test_calendar_imports.py` | **7 passed in 15.91s** | 真实 XLSX/ICS、模板、部分确认、校对、重复、冲突、过期、所有权、并发、原子回滚、DST 与迁移 |
| `uv run pytest -q` | **92 passed in 89.80s** | 服务端全量真实 HTTP/PostgreSQL 回归 |
| `uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `uv run alembic check` | 无模型漂移 | `0011_calendar_file_imports (head)` 与 ORM 一致 |
| `uv build --no-sources` | 通过 | sdist 和 wheel |

专项动态验证了上传只产生候选、候选纠错后重新校验、多轮部分确认、选中项失败整笔回滚、确认响应冲突、同文件重传、ICS 同 UID 内容变化、操作号重放、跨账号 404、7 天过期、双设备批次版本竞争及有导入系列时拒绝有损降级。解析边界另验证跨纽约春季 DST 的 `DURATION` 保持真实时长、超长 ICS 属性只使单项失败、`RECURRENCE-ID` 不误建重复事件，以及极远稀疏 XLSX 行在迭代前拒绝。

本节仅完成服务能力；文件导入候选 Web 页面和真实 Chromium 测试将在下一提交完成。截图识别、原图清理和 ICS 订阅刷新仍属于后续独立模块。

## M3-K：Excel 与 ICS 导入校对 Web 界面

对应 Git 提交主题：`feat: 完成日历文件导入校对界面`。实际代码为 `web/src/components/CalendarImportDialog.vue`、`CalendarImportCandidateEditor.vue`、`web/src/lib/calendar-imports.ts`、日历导航与样式扩展，以及 `web/e2e/calendar-imports.spec.ts`。

已实现：

- 日历侧栏提供“导入日历”入口；同一对话框支持下载服务端生成的 Excel 模板、上传 XLSX/ICS、查看最近 5 个批次及恢复未完成校对。
- 候选列表显示有效、错误、精确重复、已确认和已跳过状态；有效项默认选择且每批最多确认 50 条，无效项可展开完整日期、时间、课程/学期、重复、时区和占用字段校对。
- 确认前日历不出现候选；部分确认和跳过后刷新真实批次状态；确认成功后立即刷新系列与 occurrence，冲突保留并显示数量。
- 候选版本冲突会保留当前填写、读取服务器最新版并展示比较，用户明确选择“基于最新版重试”或“采用服务器候选”；快速切换历史批次只接受最后一次响应。
- 上传超时沿用同一操作号重试；历史、上传和批次读取失败分别调用对应恢复动作。已完成和已过期批次不再展示编辑、跳过或确认入口。
- 桌面使用宽对话框与双列编辑表单，平板和手机使用单列全屏滚动；打开、校对及退出时维护键盘焦点。状态、图标、文本换行和控件尺寸覆盖 320px 大字号。

2026-10-02 在隔离 PostgreSQL、真实 API/邮件进程和 Chromium 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `cd web && pnpm exec playwright test e2e/calendar-imports.spec.ts` | **6 passed in 20.4s** | 2 个文件导入完整流程 × 1440×1000、390×844、768×1024 |
| `cd web && pnpm test:e2e` | **84 passed in 3.0m** | 账号、后台、任务、日历及导入全量 Chromium 回归 |

浏览器流程使用真实服务端模板文件和真实 ICS 文件，覆盖上传前不写日历、确认后 occurrence 可见、同文件重传识别重复、部分错误校对、候选版本冲突草稿保留、候选间冲突、终态禁写、焦点交接、axe、控制台及根级横向溢出。截图位于 `/tmp/itime-ui-evidence/*-calendar-import-*.png`，已人工检查桌面、平板、手机和 320×700 最大字号状态。

Excel 与 ICS 文件导入至此完成；截图识别、原媒体清理和 ICS 订阅仍在后续模块。

## M3-L：AI 配置、持久化作业与截图识别服务

对应 Git 提交主题：`feat: 实现 AI 配置版本服务`、`feat: 建立 AI 作业与临时媒体基础` 和 `feat: 实现日历截图识别服务`。实际代码为 `server/src/itime/ai/`、`server/src/itime/calendar/screenshot_*.py`、日历导入复用逻辑及迁移 `0012`—`0014`。

已实现：

- 管理员可创建不可变的截图识别配置版本；Key 使用独立 AI Fernet 密钥加密且接口不回显。配置以合成图片真实调用 OpenAI 兼容端点，成功后才能原子发布，新发布版本不会改写在途作业。
- 截图上传验证真实 PNG/JPEG/WebP、MIME、扩展名、完整解码、单帧、5 MiB 和 2000 万像素边界；上传结果是绑定配置和输入快照的持久化作业，不在 API 请求中同步等待模型。
- AI worker 使用 PostgreSQL `SKIP LOCKED` 和随机租约领取任务，网络调用不占数据库事务；总截止时间不因重试重置。限流、网络和 5xx 有界重试，取消、超时、成功与晚到输出只能一个终态生效。
- 视觉端点收到图片、用户时区、学期及节次快照；响应必须是受限 JSON。单项继续经过现有日历领域校验，部分错误不会丢失可判断候选，未经用户确认不写正式日历。
- 作业成功以唯一 `origin_job_id` 创建截图导入批次，继续复用候选校对、部分确认、精确重复和冲突返回。首次确认或跳过时清空原图密文；未处理原图 24 小时清理，候选独立保留 30 天。

2026-10-02 在本地 PostgreSQL 18.6、真实 Uvicorn HTTP 和本地 OpenAI 兼容测试端点中执行：

| 命令（`server/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `uv run pytest -q tests/test_ai_config.py` | **9 passed** | 配置加密、合成视觉请求、失败脱敏、并发发布、权限与迁移 |
| `uv run pytest -q tests/test_ai_jobs.py tests/test_image_validation.py` | **20 passed** | 租约、重试、取消、媒体清理及真实三格式图片 |
| `uv run pytest -q tests/test_calendar_screenshot_imports.py` | **5 passed** | 上传、真实 HTTP 识别、候选、确认清理、失败重试和取消竞争 |
| `uv run pytest -q` | **126 passed in 114.62s** | 截图服务接入后的服务端全量回归 |
| `uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `uv run alembic check` | 无模型漂移 | `0014_calendar_screenshot_imports (head)` 与 ORM 一致 |
| `uv build --no-sources` | 通过 | sdist 和 wheel |

测试端点接收真实 base64 图片请求并返回结构化候选，不以静态服务返回替代 worker 状态机。测试同时验证两个 worker 不重复领取、租约过期重领、取消胜过晚到成功、原图密文不等于明文、确认后正文为空、跨账号 404 及失败响应不泄漏 Key 或上游正文。

当前未配置真实第三方模型 Key，因此只证明 OpenAI 兼容协议与真实 HTTP 链路可运行，不宣称具体供应商已通过。截图 Web 上传/轮询界面、AI 金额账本、正文日志、质检及 ICS 订阅仍待后续提交。

## M3-M：AI 配置管理 Web 界面

对应 Git 提交主题：`feat: 完成 AI 配置管理页面`。实际代码为 `web/src/components/AdminAISettings.vue`、`web/src/lib/admin-ai.ts`、管理路由与导航，以及扩展后的隔离 UI 测试环境。

已实现：

- 管理后台 `/admin/ai` 提供截图识别能力的新版本表单，包含 OpenAI 兼容端点、模型、只写 Key、单次超时和最大输出；创建成功立即清空 Key，列表只显示已加密设置状态。
- 版本列表显示草稿、测试通过、当前发布和历史版本；草稿/已测试版本可重新运行合成图片测试，测试通过后发布，历史版本可回滚并原子替换当前版本。
- 配置测试失败后重新读取服务端已保存错误阶段；测试请求按配置超时延长前端等待，不使用普通 API 的固定 15 秒限制。
- 测试环境启动真实 API、独立 OpenAI 兼容 HTTP 端点和 AI worker，并预置一个已发布版本；浏览器操作不通过路由拦截伪造成功。
- 页面沿用管理后台线性设置布局，电脑使用侧栏，平板和手机使用可换行导航及单列版本项；Key、长端点和按钮不会引发横向溢出。

2026-10-02 执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与生产构建 |
| `cd web && pnpm exec playwright test e2e/admin-management.spec.ts` | **12 passed in 28.1s** | 4 个管理流程 × 1440×1000、390×844、768×1024 |

新增真实浏览器流程覆盖创建版本、Key 不回显、合成图片 HTTP 测试、发布、回滚、唯一当前版本、axe、控制台、Vite 错误覆盖层和根级横向溢出。截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-admin-management-ai-config-published.png`，已人工检查三个视口。

## M3-N：截图识别导入 Web 界面

对应 Git 提交主题：`feat: 完成截图识别导入界面`。实际代码为 `web/src/components/CalendarScreenshotImport.vue`、现有 `CalendarImportDialog.vue` 扩展、`web/src/lib/ai-jobs.ts` 及 `web/e2e/calendar-imports.spec.ts`。

已实现：

- 导入对话框以分段控件切换 Excel/ICS 文件和截图识别；截图接受 PNG、JPEG、WebP，选择后显示文件名并在浏览器侧先检查类型与 5 MiB 上限。
- 上传返回持久化作业后显示排队、识别、等待校对、失败、超时和取消状态，以及尝试次数；不展示伪进度百分比。
- 活动作业按 1—3 秒有界轮询；页面隐藏或对话框关闭时停止前端轮询但不取消服务端作业，重新进入或窗口重新获得焦点时从服务端恢复。
- 用户可显式取消排队或执行中的作业；取消操作号在未知响应时复用。成功作业可重新打开对应批次，自动复用现有候选编辑、版本冲突、部分确认和冲突提示。
- 对话框关闭后焦点回到“导入日历”入口。桌面保持宽对话框，840px 以下使用全屏内部滚动；长截图文件名、状态、操作按钮和 320px 最大字号不产生根级横向溢出。

2026-10-02 执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与生产构建 |
| `cd web && pnpm exec playwright test e2e/calendar-imports.spec.ts` | **9 passed in 31.3s** | 3 个导入流程 × 1440×1000、390×844、768×1024 |
| `cd web && pnpm test:e2e` | **90 passed in 3.2m** | 账号、后台、任务、日历、文件和截图导入全量 Chromium 回归 |

新增流程以真实 PNG 上传到隔离 API，由独立 AI worker 调用真实本地 OpenAI 兼容 HTTP 服务；浏览器在提交后关闭导入对话框，等待后台完成，再重新进入恢复作业、打开候选、确认并从日历读取 occurrence。三个视口均验证 axe、控制台、API 500、错误覆盖层和横向溢出；桌面项目另在 320×700 最大字号检查截图作业列表。截图位于 `/tmp/itime-ui-evidence/*-calendar-import-screenshot-*.png`，已人工检查。

截图识别至此完成服务、管理配置和用户校对闭环；仍未接每日额度、正文日志与质检。M3 下一项为 ICS URL 订阅。

## M3-O：ICS URL 订阅服务

对应 Git 提交主题：`feat: 实现受限日历订阅抓取器` 和 `feat: 实现 ICS 订阅刷新服务`。实际代码为 `server/src/itime/calendar/subscription_*.py`、统一 occurrence 查询扩展及迁移 `0015_calendar_subscriptions`。

已实现：

- 创建、列表、详情、修改 URL/周期、手动刷新、暂停、恢复和来源事件字段覆盖 API；所有写请求使用操作号，账号隔离读取，只显示主机及省略敏感路径、查询参数后的地址。
- 订阅 URL 使用独立密钥加密并以 HMAC 比较。生产配置要求订阅密钥与基础及 AI 密钥均不同；开发未设置时保留显式回退。
- 受限抓取器逐跳完成 URL、DNS A/AAAA、地址范围、固定 IP、Host/SNI/证书和实际 peer 校验，禁止 HTTPS 降级和环境代理；总时限及 gzip 压缩体、解压体、压缩比均有硬上限。
- 独立 worker 使用 `SKIP LOCKED`、随机租约、尝试号和 URL 修订号。网络、解压和解析不持有数据库事务；双 worker 不重复领取，URL 更新、暂停、租约过期和晚到结果不会覆盖新状态。
- 200 完整解析后原子应用，304 只更新成功检查，DNS、HTTP、超时、类型、容量、压缩和解析失败均保留最近成功快照及 occurrence。
- 来源事件按 `source_id + UID + normalized RECURRENCE-ID` 隔离，支持无限日/周/月/年重复、RDATE、EXDATE、全天、UTC、IANA 和浮动时间。RID 调时/取消、已知坏项目保旧、冲突重复保旧及缺失取消均按来源代次处理。
- 来源删除或主事件取消只取消刷新生效后的 occurrence；历史实例保留。RID 缺失只撤销未来来源单次覆盖。本地名称、说明、地点和占用覆盖优先于后续来源更新，并可逐字段重置。
- 订阅 occurrence 已接入统一 93 天/2000 条查询和冲突计算；手工创建安排时也会返回与订阅事件的冲突。

2026-10-02 在本地 PostgreSQL 18.6、真实 Uvicorn HTTP 和真实本机流式 HTTP 服务中执行：

| 命令（`server/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `uv run pytest -q tests/test_calendar_subscription_fetcher.py tests/test_calendar_subscription_parser.py tests/test_calendar_subscriptions.py` | **34 passed** | SSRF/peer/重定向/代理/gzip/超时、ICS 解析、真实 API/PostgreSQL/worker、304、失败保旧、RID、覆盖、容量、租约和迁移 |
| `uv run pytest -q` | **160 passed in 122.04s** | 订阅服务接入后的服务端全量回归 |
| `uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `uv run alembic check` | 无模型漂移 | `0015_calendar_subscriptions (head)` 与 ORM 一致 |
| `uv build --no-sources` | 通过 | sdist 和 wheel |

专项测试还验证了代理环境变量不会接管连接、IPv4-mapped IPv6 与混合公网/私网解析被拒绝、条件验证器只发往原最终 URL、网络等待期间无数据库空闲事务、来源字段差异可见及旧刷新结果被 URL 新修订淘汰。

本节先完成服务端与统一日历投影；对应管理界面和浏览器证据见下一节。尚未使用真实第三方私密订阅源测试。

## M3-P：ICS 订阅管理 Web 界面

对应 Git 提交主题：`feat: 完成 ICS 订阅管理界面`。实际代码为 `CalendarSubscriptionManager.vue`、`calendar-subscriptions.ts`、导入对话框第三种模式、订阅 occurrence 详情分支和 `calendar-subscriptions.spec.ts`。

已实现：

- “导入日历”对话框以三段控件进入订阅模式；可创建来源、查看状态及最近十次刷新、修改名称/周期/可选新 URL、手动刷新、暂停和恢复。
- 排队和执行状态按 1—5 秒有界轮询，页面隐藏或对话框关闭后停止前端轮询；后台 worker 继续执行。首次成功、304、失败且保留旧数据、无成功快照等状态使用不同明确文案。
- 来源事件支持分页、解析错误、来源取消和本地覆盖数量展示。名称、说明、地点和占用状态可逐字段启用覆盖或重置；来源变化后同时显示覆盖时来源值、当前来源值和本地值。
- 订阅 occurrence 使用可空系列标识和独立来源事件标识，详情显示“ICS 订阅”和来源取消，不再调用手工系列的取消、调课、补课或系列编辑 API；可直接返回对应来源与事件。
- UI 不展示 URL 查询、敏感路径或原始 UID。桌面使用来源/详情双栏；840px 以下改为单栏，按钮保持触摸尺寸，长主机、错误和字段值允许换行。
- UI 测试环境新增真实本机 ICS HTTP 服务和独立订阅 worker；浏览器不拦截订阅 API，也不直接伪造刷新成功。

2026-10-02 执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与生产构建 |
| `cd web && pnpm exec playwright test e2e/calendar-subscriptions.spec.ts` | **6 passed in 54.0s** | 2 个订阅完整流程 × 1440×1000、390×844、768×1024 |
| `cd web && pnpm test:e2e` | **96 passed in 3.9m** | 账号、后台、任务、日历及三类导入全量 Chromium 回归 |

浏览器流程覆盖真实创建与后台抓取、304、暂停/恢复、从日历返回来源、来源更新、本地覆盖、覆盖版本冲突、逐字段重置、刷新失败保留旧 occurrence、URL/UID 不泄漏、axe、控制台、API 500 和根级横向溢出；桌面项目另在 320×700 最大字号检查。截图位于 `/tmp/itime-ui-evidence/*-calendar-subscription-*.png`，已人工检查桌面、平板、手机和 320px 大字号。Browser 插件工具在本会话未暴露，因此按前端测试规范使用仓库 Playwright Chromium 流程。

订阅 occurrence 的用户单次例外仍待统一例外 API；真实 Edge、实体设备及第三方私密订阅源兼容性未在本地完成。
