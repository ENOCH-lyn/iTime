# iTime Web 实施与验证

更新：2026-10-05。此文件记录已落地模块和真实验证，不替代[全量验收清单](iTime-产品验收清单.md)。用户要求完整 Web 用户端与后台、逐模块提交、详细服务及真实 UI 测试。

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

仍未验证真实 Edge 与实体设备。本节完成时后台业务页面尚未实现；用户/邀请、AI 配置、日志/质检/审计和额度页面的后续证据分别见 M2-F、M3-G/M4-B、M4-C 与 M4-E。

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

仍未验证真实 Edge、实体手机/平板、正式邀请交付渠道和外部生产邮件。邀请默认运营期限、申诉及 AI 在途作业处理仍待确认；AI 配置和正文/日志/质检/审计后台的后续证据见 M3-G、M4-B、M4-C，额度后台仍未实现。

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

本节不包含 Excel、ICS、截图或订阅导入；任务实例补做、归档和回收站当时尚未实现，后续证据分别见 M3-Q、M6-A/M6-B。真实 Edge、实体设备和全产品端到端验收仍待后续阶段。

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

当前未配置真实第三方模型 Key，因此只证明 OpenAI 兼容协议与真实 HTTP 链路可运行，不宣称具体供应商已通过。截图 Web 上传/轮询、正文日志/质检及 ICS 订阅的后续证据已分别补入 M3-H、M4-C、M3-J；AI 金额账本仍待后续提交。

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

截图识别至此完成服务、管理配置和用户校对闭环；正文日志与质检、每日额度后续已分别由 M4-C、M4-E 接入。M3 后续 ICS URL 订阅证据见 M3-J。

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

## M7-A：公开产品首页

对应 Git 提交主题：`feat: 完成公开产品首页`。实际代码为 `web/src/pages/ProductPage.vue`、`PublicSiteHeader.vue`、`PublicSiteFooter.vue`、`public-site.css`、根路由及 `web/e2e/public.spec.ts`。

已实现：

- `/` 不再直接跳转登录后的任务页，而是向访客展示 iTime 产品首页；主动作统一进入注册，登录和帮助作为次级入口，未登录即可了解产品范围。
- 首屏使用代码实现的真实日计划场景作为背景，展示固定安排、任务、专注与缓冲，不依赖外部图片或装饰性插画；正文不放入卡片，并在每种视口末端露出下一节内容。
- 下方使用开放分栏和时间线说明“整理 → 安排 → 复盘”闭环，明确固定安排、任务排期、缓冲和复盘之间的区别，不加入分享、付费或 Web 提醒入口。
- 桌面完整显示五日网格与计划块；平板隐藏会穿过主文案的背景标签但保留计划块；手机保留时间网格和当前时间线，避免裁切文字。320px 最大字号下标题、按钮、时间线和页脚均无根级横向溢出。
- 公共页头与页脚复用现有主题/字号设置、注册、登录和帮助入口；当前没有公开演示、协议和隐私链接，避免尚未实现的死入口。

2026-10-02 执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与生产构建 |
| `cd web && pnpm exec playwright test e2e/public.spec.ts` | **3 passed in 10.6s** | 首页 × 1440×1000、390×844、768×1024，另含 320×700 最大字号 |
| `cd web && pnpm test:e2e` | **99 passed in 4.1m** | 账号、后台、任务、日历、导入、订阅和公开首页全量 Chromium 回归 |

专项测试验证主标题、注册与演示入口、产品场景、axe、控制台、API 500 监听和根级横向溢出。截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-public-home.png` 与 `desktop-public-home-320-large-font.png`，已人工检查；修正了背景小字对比度、页头 landmark 及平板/手机背景标签与正文重叠问题。Browser 插件工具在本会话未暴露，因此按前端测试规范使用仓库 Playwright Chromium 流程；真实 Edge 和实体设备仍未验证。

## M7-B：公开交互演示

对应 Git 提交主题：`feat: 完成公开交互演示`。实际代码为 `web/src/pages/DemoPage.vue`、`demo.css`、公共页导航扩展和 `web/e2e/public.spec.ts`。

已实现：

- `/demo` 无需登录即可使用，明确说明所有内容为当前页面的预设数据，刷新后重置，不写入服务端或浏览器持久化存储，也不冒充真实 AI 结果。
- 分段控件提供宽松、均衡、紧凑三种密度；切换后真实更新课程作业、阅读、运动与缓冲的开始/结束时间，固定课程始终保留原时间。
- 左侧待办与右侧时间线分开表达任务事实和计划块。任务只能通过明确的完成按钮改变状态，计划块本身不会自动完成任务；完成计数、删除线和时间线状态同步更新。
- “重新开始”恢复均衡密度与全部未完成状态；刷新页面同样恢复初始演示，避免访客误认为数据已保存。
- 桌面采用任务/时间线双栏工具，平板和手机按任务在前、时间线在后单列展开；320px 最大字号下分段控件、任务按钮、时间区间和页脚无横向溢出。

2026-10-02 执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与生产构建 |
| `cd web && pnpm exec playwright test e2e/public.spec.ts` | **6 passed in 17.7s** | 首页与演示 × 1440×1000、390×844、768×1024，另含两页 320×700 最大字号 |
| `cd web && pnpm test:e2e` | **102 passed in 4.0m** | 账号、后台、任务、日历、导入、订阅、公开首页和演示全量 Chromium 回归 |

浏览器流程实际切换密度并检查时间变化，明确完成任务并检查双区域状态，再刷新确认演示数据重置。三个视口均验证 axe、控制台、API 500 和根级横向溢出；截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-public-demo.png` 与 `desktop-public-demo-320-large-font.png`，已人工检查。真实 Edge 和实体设备仍未验证。

## M7-C：公开帮助、协议与隐私说明

对应 Git 提交主题：`feat: 完成公开帮助与法律说明`。实际代码为 `HelpPage.vue`、`TermsPage.vue`、`PrivacyPage.vue`、公共页样式/页脚、公开路由和 `web/e2e/public.spec.ts`。

已实现：

- 帮助中心从原账号排障扩展为首次使用、任务/日历语义、账号安全和失败恢复四组内容；可直接进入任务、日历、演示、重发验证、找回密码和设备管理。
- 用户协议明确当前为 Web 开发版本，说明服务范围、账号责任、内容来源、AI 结果确认和中断恢复；不虚构尚未确定的运营主体、正式联系方式或争议条款。
- 隐私说明如实列出当前已处理的账号/任务/日历数据、截图识别的 OpenAI 兼容端点处理、超级管理员正文访问，以及正式上线前仍需披露的供应商、地区和运营主体。
- 生命周期表逐项区分“产品规则”和“当前状态”：本节交付时识别原图清理和任务回收站已实现，日历对象回收站和注销清理仍标为待实现；M4-C、M6-G 和 M6-H 后分别按实际结果更新 AI 日志/样本、账号注销及日历系列/订阅源状态，不把需求目标冒充现状。
- 隐私表格在宽屏保持结构化表格；600px 以下转换为带字段标签的完整列表，不把“当前状态”藏在默认视口之外。所有公开页脚只链接真实路由。

2026-10-02 执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与生产构建 |
| `cd web && pnpm exec playwright test e2e/public.spec.ts` | **9 passed in 14.4s** | 五类公共页面 × 1440×1000、390×844、768×1024，另含首页、演示、隐私 320×700 最大字号 |
| `cd web && pnpm test:e2e` | **105 passed in 4.2m** | 账号、后台、任务、日历、导入、订阅和全部公开页面 Chromium 回归 |

浏览器流程真实访问帮助锚点、协议和隐私路由，核对 7 天日志、30 天样本及“已实现/待实现”状态；M6-B、M4-C、M6-G 和 M6-H 后分别更新任务回收站、正文日志/质量样本、账号注销和日历系列/订阅源回收状态。三个视口均验证 axe、控制台、API 500 和根级横向溢出；修复了可滚动表格键盘焦点，并将手机表格进一步改为完整标签列表。截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-public-privacy.png` 与 `desktop-public-privacy-320-large-font.png`，已人工检查。正式运营主体/供应商/地区仍是发布前配置与披露项；真实 Edge 和实体设备仍未验证。

## M6-A：任务归档与 30 天回收站服务

对应 Git 提交主题：`feat: 实现任务归档与回收站服务`。实际代码为迁移 `0016_task_lifecycle`、`tasks/lifecycle_service.py`、生命周期 API、任务/日历过滤、独立清理进程及 `server/tests/test_task_lifecycle.py`；完整合同见[任务生命周期设计](../设计/iTime-任务生命周期设计.md)。

已实现：

- 任务完成与归档正交；活动、已归档、回收站三个范围可独立分页筛选。默认任务列表、项目/标签计数和任务日历 occurrence 只包含活动任务。
- 归档、取消归档、删除和恢复均使用本人权限、CSRF、操作幂等、任务行锁与乐观版本。归档或回收站任务的普通编辑、完成、步骤、批量、截止、实例及拆分写入统一拒绝。
- 删除保留原归档与完成状态、步骤、链接、标签、日期/重复规则和实例事实，固定在服务端删除时间后 30×24 小时到期；恢复回到删除前活动或归档范围。
- 生命周期变化清空旧任务操作的缓存响应正文但保留操作号、范围和请求哈希。旧操作同内容重放返回“结果已过期”，不同内容仍拒绝复用，不能以响应丢失或离线重放复活任务。
- `python -m itime.tasks.cleanup_worker` 以每批 100 条和 PostgreSQL `FOR UPDATE SKIP LOCKED` 清理到期任务。正文、项目、估时、完成事实及直接关联明细被清空，任务 ID、所有者、删除时间、版本和 `purged` 事件保留为墓碑；重复任务后继外键继续有效。

2026-10-03 在本地 PostgreSQL 18.6 和真实 Uvicorn HTTP 中执行：

| 命令（`server/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `uv run pytest -q tests/test_task_lifecycle.py` | **10 passed in 8.60s** | 三种范围、计数、恢复、所有权、幂等/版本、全部写入阻断、日历隐藏、到期脱敏、双 worker 与独立进程 |
| `uv run pytest -q tests/test_foundation.py::test_migrations_round_trip_in_disposable_database tests/test_tasks.py tests/test_task_schedules.py` | **15 passed in 26.26s** | 迁移往返及原任务/日期回归 |
| `uv run pytest -q` | **170 passed in 128.56s** | 服务端真实 HTTP/PostgreSQL 全量回归 |
| `uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `uv run alembic current && uv run alembic check` | `0016_task_lifecycle (head)`，无模型漂移 | 开发库与 ORM 一致 |
| `uv build --no-sources` | 通过 | sdist 和 wheel |

专项清理测试实际建立带步骤、链接、标签、重复日程及后继系列的任务，推进到期时间后确认正文和直接关联表已清空、后继前序外键仍指向墓碑、旧操作响应为空且哈希保留。两个独立数据库连接并发领取三条到期任务时分别处理 2 条和 1 条；`--once` 在独立 Python 进程中成功执行，输出不含测试正文。

本节只记录服务与清理进程；对应任务工作区见下一节。AI 日志与质检样本后续已由 M4-C 接入；计划、报告、困难及跨聚合清理规则仍按 Q-DATA-01 / Q-PRIV-01 后续补齐。

## M6-B：任务归档与回收站 Web 界面

对应 Git 提交主题：`feat: 完成任务归档与回收站界面`。实际代码为 `web/src/pages/TasksPage.vue`、`components/Task{ListPanel,Editor,ScheduleEditor}.vue`、`lib/tasks.ts`、任务工作区样式和 `e2e/task-lifecycle.spec.ts`。

已实现：

- 任务列表使用活动、已归档、回收站三段控件切换服务端范围；搜索、状态、优先级、标签、项目和收集箱筛选继续生效。
- 非活动范围不显示新建、选择及批量操作。任务详情保留名称、正文、项目、优先级、估时、标签、日期/重复、步骤和链接，但所有写入控件只读。
- 活动任务可手动归档或经明确确认移到回收站；归档任务可取消归档或删除；回收站显示服务端清理时间和剩余天数，并可恢复到删除前活动/归档范围。
- 生命周期操作复用稳定操作号。第二页面造成版本冲突时展示服务器最新版；采用服务器内容后界面立即切换正确只读状态，不把旧草稿覆盖到墓碑。
- 桌面保持导航、列表、详情三栏；平板保持列表/详情双栏；手机使用全屏详情。320px 大字号下生命周期说明、日期、恢复按钮和全部正文可换行且无横向溢出。

2026-10-03 执行：

| 命令（`web/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `pnpm exec playwright test e2e/task-lifecycle.spec.ts` | **6 passed in 25.9s** | 2 个真实生命周期流程 × 1440×1000、390×844、768×1024，另含 320×700 最大字号 |
| `pnpm exec playwright test e2e/tasks.spec.ts` | **15 passed in 51.0s** | 原任务创建、日期、批量、并发、离线流程三视口回归 |
| `pnpm test:e2e` | **111 passed in 4.2m** | 账号、后台、任务、生命周期、日历、全部导入和公共页面 Chromium 全量回归 |

专项流程真实完成任务创建、归档、取消归档、删除确认、回收站只读查看、期限显示和恢复；另一浏览器页面制造归档版本冲突并采用服务器状态，确认完成事实不变。每个视口均检查 axe、控制台、Vite 错误层和根级横向溢出。截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-task-lifecycle-{trash-detail,conflict-adopted}.png` 及 `desktop-task-lifecycle-trash-320-large-font.png`，已人工检查三栏、双栏、手机单栏和 320px 大字号，没有内容重叠。

Browser 插件工具在本会话未暴露，因此按前端测试规范使用仓库 Playwright Chromium。日历系列与
订阅源回收站后续已由 M6-H 实现；真实 Edge、实体设备及学期、项目、AI 会话等其他独立对象回收
仍待后续模块。

## M4-A：全局 AI 多会话与可恢复文本流服务

对应 Git 提交：`04a9a60`、`0c09085`、`60955ee`、`91ca75c`。实际代码为迁移 `0017_ai_conversations`、`server/src/itime/ai/conversation_*.py`、通用作业服务和 worker 扩展，以及 `test_ai_conversations.py`、`test_ai_conversation_api.py`、`test_ai_conversation_worker.py`；合同见[全局 AI 对话设计](../设计/iTime-全局AI对话设计.md)。

已实现：

- 多个普通会话的创建、稳定游标列表、改名、归档、重新打开和所有者隔离；会话版本冲突与操作号重放不会覆盖新状态。
- 用户消息、assistant 占位消息、配置版本、作业、相关历史消息 ID 和首个事件在同一事务提交。当前会话只取有界历史，跨会话只选择少量确定性相关消息，不发送账号全部正文。
- `conversation` 与 `calendar_screenshot` 分能力配置、合成文本/图片真实测试、独立发布指针和在途版本绑定；Key 继续加密保存且不回显。
- worker 解析拆分的 OpenAI 兼容 SSE，也兼容一次性 JSON 响应；分段按时间/体积合并后先写 PostgreSQL 消息快照和递增事件，再供客户端读取。
- 空闲上游请求定期续租。租约续期、增量、完成和失败均校验原租约仍有效；双 worker 不会重复接管合法长请求。
- 429、5xx、连接和超时错误在总截止时间内有界重试。新尝试先写 `content_reset` 并递增 generation，旧尝试片段不会与新回复拼接。
- 取消与增量写入锁同一作业；取消后保留已展示的不完整文字，晚到增量和完成结果无法覆盖取消终态。
- `GET /api/v1/ai/jobs/{id}/events` 以 SSE 返回已提交事件，支持查询游标和 `Last-Event-ID`；事件 7 天清理后仍可从长期消息快照恢复。

2026-10-03 在本地 PostgreSQL 18.6、真实 Uvicorn 和本机流式 HTTP 上游中执行：

| 命令（`server/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `uv run pytest -q tests/test_ai_conversation_worker.py` | **9 passed** | 拆帧 SSE、JSON 回退、429 重试/reset、空闲续租、取消竞态、401、无效/空输出、事件续读与清理 |
| `uv run pytest -q tests/test_ai_config.py` | **10 passed** | 截图和对话能力的合成测试、发布回滚、错误与凭据保护 |
| `uv run pytest -q` | **184 passed in 148.52s** | 服务端真实 HTTP/PostgreSQL 全量回归 |
| `uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `uv run alembic current && uv run alembic check` | `0017_ai_conversations (head)`，无模型漂移 | 开发库与 ORM 一致 |
| `uv build --no-sources` | 通过 | sdist 和 wheel |

迁移专项还验证了复合所有者外键、截图/对话输入引用约束、同会话单活动作业、对话作业与生成映射延迟一致性、消息身份不可变及有数据时禁止降级。

当前限制：普通回复的每日金额额度后续已在 M4-E 接入；正文日志和质检样本已在 M4-C 接入。规划业务提案及确认应用后续已由 M5-A/M5-B 接入；普通回复及任务、日历、记忆等其他业务对象仍不会仅凭模型文字直接修改。

## M4-B：全局 AI 对话 Web 界面

对应 Git 提交：`08d88af`、`5e39908`、`633c4a7`。实际代码为 `web/src/pages/AIPage.vue`、`components/Conversation*.vue`、`lib/conversations.ts`、`ai-workspace.css`、管理 AI 配置扩展、隔离浏览器环境和 `e2e/ai-conversations.spec.ts`。

已实现：

- 登录成功默认进入 `/ai`。产品导航统一提供 AI、任务、日历和设置，账号设置可返回全局对话。
- 会话列表支持活动/已归档、新建、选择、改名、归档、重新打开和分页；生成中的会话禁止归档。
- 消息区展示用户事实、AI 文本、排队/生成、失败、超时和取消状态；纯文本渲染，不把模型内容作为 HTML 或已执行业务操作。
- 浏览器以消息快照为基线，按事件序号消费 SSE。页面隐藏、离页、刷新或 SSE 中断后重新读取服务端状态；不会为恢复再次提交用户消息。
- 发送、改名、归档均保留适用的操作号以处理未知保存状态。取消保留已展示片段，失败消息可把原用户输入放回编辑框。
- 桌面为会话栏与消息区双栏，平板缩窄会话栏，手机在会话列表和全屏消息间切换；320px 最大字号、深色、键盘、触控和减少动效均有覆盖。
- 管理后台可分段选择截图识别或全局对话能力，并分别创建、执行合成测试、发布和回滚配置。

2026-10-03 执行：

| 命令（`web/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `pnpm exec playwright test e2e/ai-conversations.spec.ts` | **9 passed in 1.1m** | 3 个对话流程 × 1440×1000、390×844、768×1024，另含 320×700 最大字号与深色 |
| `pnpm exec playwright test e2e/admin-management.spec.ts --grep '分别创建'` | **3 passed in 13.7s** | 两种 AI 能力配置的真实管理流程 |
| `pnpm test:e2e` | **120 passed in 5.2m** | 账号、后台、AI、任务、日历、导入、订阅和公开页面全量 Chromium 回归 |

浏览器环境启动随机 PostgreSQL、真实 API、邮件 worker、AI worker、本机分块 SSE 上游、订阅 worker 和生产构建预览。专项覆盖多会话、流式增量、离页恢复、单次尝试、取消晚到结果、重试失败、改名、归档/恢复、AI 冻结、键盘提交、axe、控制台和根级横向溢出。截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-ai-conversation-*.png` 与 `desktop-ai-conversation-320-large-dark.png`，已人工检查。

Browser 运行工具未在当前执行环境暴露，因此按前端测试规范使用仓库 Playwright Chromium。用户与后台额度显示后续已在 M4-E 验证，规划提案卡和确认预览后续已在 M5-B 验证；真实 Edge、实体设备、其他业务对象提案和正式第三方模型质量仍未验证。

## M4-C：AI 正文日志、质量样本与访问审计

对应 Git 提交：`ee88a79`、`9ea4123`、`abe0f12`、`43dbb5a`、`2c056a2`、`03c1f93`、`57e1a36`、`e56dbc4`、`6c6cefc`。实际代码为迁移 `0018_ai_governance`、`server/src/itime/ai/governance_*.py`、AI worker 终态接入、`retention_worker.py`、用户隐私设置 API/UI、后台日志/质检/审计 API/UI 及专项测试；合同见[AI 日志与质检设计](../设计/iTime-AI日志与质检设计.md)。

已实现：

- 每次真实供应商尝试分别保存清理后的请求和已提交响应。重试保留多条独立日志；失败、超时、取消和租约失效各自形成稳定状态，排队阶段超时也会同步完成对话消息和事件投影。
- 截图日志只保存提示词、识别上下文、媒体类型/大小/摘要和省略标记，不复制 base64 原图。日志和普通运行日志均不写平台 Key、Authorization、Cookie 或原始异常。
- 成功终态事务锁定当前用户隐私设置并至多创建一个 30 天质量样本。默认参与；关闭先提交时在途作业不新增样本，关闭后重开只影响后续成功作业。
- `python -m itime.ai.retention_worker` 分别按 7 天和 30 天限批清理日志与样本，使用 `FOR UPDATE SKIP LOCKED` 支持多进程；日志删除不级联删除仍在期限内的样本。
- `/account/privacy` 提供服务端版本化开关、操作号重放、网络未知状态对账和 7/30 天用途说明。
- 后台 `/admin/logs`、`/admin/quality`、`/admin/audit` 只在列表展示元数据。正文须以独立管理会话、CSRF 和显式用途通过 POST 读取；成功及不存在目标都先提交审计再返回结果。自由文本评审备注不进入普通列表。
- 质检支持“可接受/需改进”、可选备注和版本冲突保护。到期记录即使尚未物理清理也不能列表、读取或评审。

2026-10-03 在本地 PostgreSQL 18.6、真实 Uvicorn、本机 OpenAI 兼容 HTTP 服务及生产构建 Chromium 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd server && uv run pytest -q tests/test_ai_jobs.py tests/test_ai_conversation_worker.py tests/test_calendar_screenshot_imports.py tests/test_privacy_settings.py tests/test_ai_governance_models.py tests/test_ai_governance_lifecycle.py` | **28 passed in 45.75s** | 逐尝试日志、重试、取消、排队超时、开关终态竞态、截图去 base64、7/30 天独立清理与独立进程 |
| `cd server && uv run pytest -q tests/test_admin_ai_governance.py tests/test_admin_management.py tests/test_admin_auth.py` | **13 passed in 13.66s** | 管理认证/CSRF、元数据不泄漏、正文访问、成功/失败审计、到期锁竞态、质检版本 |
| `cd server && uv run pytest -q` | **195 passed in 169.45s** | 服务端真实 HTTP/PostgreSQL 全量回归 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv run alembic check` | 无新升级操作 | 迁移与 ORM 一致 |
| `cd server && uv build --no-sources` | 通过 | sdist 和 wheel |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `cd web && pnpm exec playwright test e2e/privacy-governance.spec.ts` | **6 passed in 30.7s** | 用户开关、网络恢复、真实 AI 产生日志/样本、管理访问、质检和审计三视口 |
| `cd web && pnpm test:e2e` | **126 passed in 5.6m** | 全部账号、后台、AI、任务、日历、导入、订阅和公开页面 Chromium 回归 |

专项浏览器流程在每个视口真实注册用户、验证邮件、切换质检参与、模拟未知保存状态后对账恢复、生成 AI 回复，再由 TOTP 管理员显式读取正文、提交质检结论并从审计页回查。每个视口检查 axe、控制台、Vite 错误层和根级横向溢出；另验证桌面切换到 320×700 最大字号深色。截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-privacy-governance-*.png` 和 `desktop-privacy-governance-quality-320-large-dark.png`，已人工检查；敏感弹窗、长邮箱、UUID、请求 JSON 和操作按钮无重叠或横向溢出。

Browser 插件工具未在当前执行环境暴露，因此按前端测试规范使用仓库 Playwright Chromium。金额额度后续已在 M4-E 验证，规划业务提案后续已在 M5-A/M5-B 接入同一治理链路，账号注销优先清理后续已由 M6-G 验证；真实 Edge、实体设备、单项彻底删除联动、其他业务提案及正式第三方模型质量仍未验证或实现。

## M4-D：AI 价格、供应商用量与平台成本

对应 Git 提交：`6bdd607`、`60ccfff`、`b6482cc`、`0858bc3`、`3dae969`、`6dcd57c`。实际代码为迁移 `0019_ai_provider_costs`、`0020_ai_provider_cost_lifecycle`，`server/src/itime/ai/{pricing,usage,cost_service}.py`、两类 provider/worker 终态接入、后台成本查询及 `/admin/costs`；合同见[AI 额度与成本账本设计](../设计/iTime-AI额度与成本账本设计.md)。

已实现：

- AI 配置版本增加币种、普通输入、缓存输入和输出价格；后台以每百万 Token 的十进制字符串输入，服务端精确转换为纳币种单位并纳入不可变配置。历史未定价版本保持 `NULL` 并明确提示，不伪造零成本。
- 对话流发送 `stream_options.include_usage=true`，兼容 usage-only SSE 和一次性 JSON；截图读取非流式 usage。非法或缺失 usage 不使有效业务结果失败，改用带版本标记的本地估算。
- 每个真实 `AIJobAttempt` 最多一条不可变 `AIProviderCost`。成功、失败、429 重试、超时、取消和租约失效分别核算；未实际发出供应商请求的尝试记录零成本，排队阶段未创建 attempt 时不造成本记录。
- provider 调用前以当前作业租约固化 `provider_started_at`；取消或租约失效先提交终态和成本，晚到 usage 无法覆盖。成本、attempt、正文日志和作业终态处于同一事务。
- 金额以十亿分之一币种单位保存，按价格版本进行整数计算；数据库和应用同时限制 Token、价格及 `BIGINT` 范围。usage 详情只保留计数白名单，不持久化供应商任意键名或正文。
- 管理后台 `/admin/costs` 支持按用户、能力、尝试状态、usage 来源和计价状态筛选，展示 Token、金额、模型/配置版本、作业和上游请求关联。页面明确失败及重试成本由平台承担，不代表用户额度扣减。

2026-10-03 在本地 PostgreSQL 18.6、真实 Uvicorn、本机 OpenAI 兼容 HTTP 服务及生产构建 Chromium 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd server && uv run pytest -q tests/test_ai_usage.py tests/test_ai_provider_cost_models.py tests/test_ai_jobs.py tests/test_ai_conversation_worker.py tests/test_calendar_screenshot_imports.py` | **27 passed in 42.17s** | 定点价格、usage 兼容/估算、流式 usage-only、截图 usage、重试、取消晚到、租约接管、迁移约束 |
| `cd server && uv run pytest -q tests/test_admin_provider_costs.py tests/test_ai_config.py tests/test_ai_governance_lifecycle.py` | **16 passed in 26.28s** | 管理鉴权、筛选/分页、精确金额、价格配置及治理回归 |
| `cd server && uv run pytest -q` | **203 passed in 172.80s** | 服务端真实 HTTP/PostgreSQL 全量回归 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv run alembic current && uv run alembic check` | `0020_ai_provider_cost_lifecycle (head)`，无模型漂移 | 开发库与 ORM 一致 |
| `cd server && uv build --no-sources` | 通过 | sdist 和 wheel |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `cd web && pnpm exec playwright test e2e/admin-management.spec.ts --grep '分别创建'` | **3 passed in 14.3s** | 三视口真实创建、测试、发布和回滚带价格配置 |
| `cd web && pnpm exec playwright test e2e/privacy-governance.spec.ts --grep '管理员显式读取'` | **3 passed in 22.0s** | 三视口生成真实成本、管理筛选、金额与正文隔离 |
| `cd web && pnpm test:e2e` | **126 passed** | 账号、后台、AI、任务、日历、导入、订阅、治理与成本页面全量 Chromium 回归 |

成本页面在 1440×1000、390×844、768×1024 三个视口检查标题、数据、筛选、axe、控制台和根级横向溢出；截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-privacy-governance-provider-costs.png`，已人工检查。

本节只记录平台供应商成本。用户额度预留、结算、释放及双端账本的后续实现与证据见 M4-E。

## M4-E：AI 用户每日额度与管理账本

对应 Git 提交：`b519640`、`49b3ceb`、`ba112db`、`aca522c`、`929a4f3`、`9dbea0a`、`8fd8add`、`4925107`、`8ac7475`、`27b9418`、`0663969`、`bc0c1a2`、`fd7a169`。实际代码为迁移 `0021_ai_user_quota`、`server/src/itime/quota/`、对话/截图接单与终态事务接入、用户 `/account/quota`、后台 `/admin/quota` 及 `web/e2e/quota.spec.ts`；合同见[AI 额度与成本账本设计](../设计/iTime-AI额度与成本账本设计.md)。

已实现：

- 每日策略采用不可变草稿/发布/退役版本。发布前检查对话与截图两类当前 AI 配置均有完整价格且币种一致；新策略只影响之后首次创建的活动日，不改写既有额度。
- 用户首次接单按服务端时间、用户 IANA 时区和当地 04:00 冻结 UTC 半开窗口。对话和截图均在创建作业的同一事务预留最大预算；操作重放不重复占用，并发接单不能透支。
- 最终成功 attempt 的成本事实与业务结果、预留结算和唯一 `usage_charge` 账本同事务提交。实际成本低于预留时释放余量，高于预留时用户最多承担预留额；失败、超时和取消释放全部预留，失败重试只计平台成本。
- worker 使用现有作业终态投影恢复到期 queued/running 作业，双进程 `SKIP LOCKED` 扫描不会遗留活动预留，也不会绕过对话消息或截图导入状态。
- 用户额度 API 及 `/account/quota` 展示当前活动日、冻结时区、当地 04:00、下次刷新、基础额度、调整、已扣、处理中与可用金额；缺少时间设置时使用浏览器 IANA 时区通过版本化接口初始化后重试。
- 后台额度 API 及 `/admin/quota` 提供策略历史、用户搜索、增减确认和账本筛选。调整使用 9 位小数定点金额、目标活动日版本、全局 operation ID、不可变调整/账本和同事务安全审计；扣减不能侵占已扣或活动预留。
- 管理写入响应丢失时前端按原 operation ID 查询账本确认，不生成重复调整；版本冲突和余额不足保留输入并刷新服务端状态。管理账号不参与用户额度调整，避免交叉管理员锁顺序。

2026-10-03 在本地 PostgreSQL 18.6、真实 Uvicorn、本机 OpenAI 兼容 HTTP 服务、AI worker 及生产构建 Chromium 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd server && uv run pytest -q tests/test_admin_quota.py tests/test_admin_quota_policy.py tests/test_quota_api.py tests/test_quota_service.py tests/test_quota_models.py` | **13 passed in 19.33s** | 策略、04:00/DST、原子预留、调整幂等/并发、负余额保护、审计回滚、权限与账本 |
| `cd server && uv run pytest -q tests/test_admin_quota.py tests/test_admin_quota_policy.py tests/test_quota_api.py tests/test_quota_service.py tests/test_quota_models.py tests/test_ai_conversation_api.py tests/test_ai_conversation_worker.py tests/test_calendar_screenshot_imports.py` | **34 passed in 42.64s** | 额度与两类 AI 接单、重试、取消、终态结算的联合回归 |
| `cd server && uv run pytest -q` | **218 passed in 193.42s** | 服务端真实 HTTP/PostgreSQL 全量回归 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv run alembic current && uv run alembic check` | `0021_ai_user_quota (head)`，无模型漂移 | 开发库与 ORM 一致 |
| `cd server && uv build --no-sources` | 通过 | sdist 和 wheel |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `cd web && pnpm exec playwright test e2e/quota.spec.ts` | **6 passed in 59.6s** | 用户与后台额度流程 × 1440×1000、390×844、768×1024，另含 320×700 大字号深色 |
| `cd web && pnpm test:e2e` | **132 passed in 6.5m** | 账号、后台、AI、额度、任务、日历、导入、订阅、治理与公开页面全量 Chromium 回归 |

专项浏览器流程真实完成普通用户注册、邮件验证、成功 AI 结算、取消/最终失败释放、时区初始化恢复、管理员 9 位小数调整、响应丢失后账本对账、策略发布、既有活动日保持旧策略、新用户使用新策略及余额耗尽拒绝。每个视口检查 axe、控制台、API 500 和根级横向溢出；截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-quota-{user-ledger,admin-ledger}.png`、`desktop-quota-user-320-large-dark.png` 和 `desktop-quota-admin-320-large-dark.png`，已人工检查金额、长邮箱、UUID、确认对话框和深色大字号无重叠或裁切。

Browser 插件技能已加载，但当前运行时没有暴露 `browser_*` 工具，因此按前端测试规范使用仓库 Playwright Chromium。规划业务提案后续已在 M5-A/M5-B 接入同一预留和结算合同；真实 Edge、实体设备、正式第三方模型账单对账、其他业务提案、支付/充值和现金钱包仍未验证或不属于本阶段。

## M5-A：AI 提案与日周规划服务

对应 Git 提交：`6f5a68e`、`3f767ac`、`56422b1`、`7b10b62`、`248cba0`、`ee99538`、`0e0790b`、`c03df9f`、`b00950a`、`81ebbe3`、`bd73509`。实际代码为迁移 `0022_ai_proposals_planning`—`0024_planning_lifecycle`、`server/src/itime/planning/`、AI 对话/worker/额度恢复扩展及规划专项测试；合同见[AI 提案与日周规划设计](../设计/iTime-AI提案与日周规划设计.md)。

已实现：

- 账号级规划输入修订锁和不可变输入快照统一读取任务、完整步骤、重复实例、固定安排、订阅投影、当前周计划和用户时区；共享/排他锁保证生成与确认不会混入并发提交。
- 通用提案、变化、周计划、不可变版本、计划块和未排项由复合所有者外键、唯一约束和数据库不可变触发器保护。用户删除仍可级联清理，不被修订触发器阻断。
- `planning` 独立 AI 能力固定调用唯一 `submit_itime_plan` 工具；可见正文与分片工具参数分离。服务端拒绝未知任务/步骤、越界日期、DST 歧义、重叠、固定占用、截止、拆分、估时守恒、密度和缓冲违规。
- 独立规划页和对话规划模式均可创建持久化作业；接单原子写快照、作业、事件和额度预留。成功原子写提案、provider 成本、额度结算、治理数据及可选对话消息，失败、超时和取消释放预留。
- 提案查询区分待确认、已应用、已拒绝、已过期和输入冲突。确认时重建当前上下文并再次校验；重复确认/拒绝和原请求重试幂等，计划与提案任一步失败均整体回滚。
- 单日确认只替换目标日并保留同周其他日期，整周确认替换完整周。手工保存复用相同硬约束，创建新的完整周版本；历史版本不可原地修改。
- 计划块提供本人范围查询并按当前显示时区投影；对话中的 planning 作业继续受同会话单活动生成和账号三作业并发上限约束。

2026-10-03 在本地 PostgreSQL 18.6、真实 Uvicorn、本机 OpenAI 兼容 HTTP 上游和双 worker 场景中执行：

| 命令（`server/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `uv run pytest -q` | **327 passed in 279.67s** | 服务端真实 HTTP/PostgreSQL 全量回归，含规划模型、输入快照、确定性校验、提案事务、版本、provider、worker、对话和额度 |
| `uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `uv run alembic upgrade head` | 通过 | 开发库升级至规划生命周期迁移 |
| `uv run alembic current` | `0024_planning_lifecycle (head)` | 开发库迁移版本 |
| `uv run alembic check` | 无新升级操作 | 迁移与 ORM 一致 |
| `uv build --no-sources` | 通过 | sdist 和 wheel |

专项覆盖迁移往返、复合所有权、历史不可变、用户级联删除、上海/纽约 DST、超过 100 候选、共享/排他锁、随机边界、双 worker 租约、重试、取消竞态、过期恢复、跨 capability 并发、额度结算及事务回滚。备用服务路由后续已由 M4-G 实现；正式第三方模型规划质量、性能和生产部署仍未验证。

## M5-B：规划、对话提案与日历 Web 界面

对应 Git 提交：`167a8b3`、`354f66c`、`b6e67da`、`552c9cb`。实际代码为 `web/src/pages/PlansPage.vue`、规划组件与 API、AI 规划模式和提案卡、统一日历计划块投影，以及 `web/e2e/planning.spec.ts`、`ai-conversations.spec.ts` 和 `calendar.spec.ts`。

已实现：

- 五项统一产品导航新增“规划”，`/plans` 支持单日/整周、任务选择、三种密度、缓冲、可规划时段、独立生成、离页恢复和显式取消。
- 提案预览明确“尚未写入计划”，展示计划块、步骤、估计、未排原因、有效期及额度结算说明；用户可确认、拒绝，过期或冲突后可重新生成。
- 当前计划支持时间、步骤和顺序手工纠错并明确保存，历史版本可查看。版本冲突保留本地草稿，显示服务器最新版和请求编号，采用服务器内容后可继续处理。
- AI 对话 composer 可切换普通回复和生成计划；规划成功后由作业输出的 `proposal_id` 展示独立提案卡，正文不解析工具 JSON，可直接进入同一规划确认页。
- 统一日历可切换计划块显示，已确认当前版本按真实日期合并到周/月视图；详情显示任务、步骤、来源和计划版本并提供规划入口，不提供任务完成操作。未确认、拒绝和过期提案不进入日历。
- 桌面、平板和手机分别采用双栏、收窄双栏或单列/全屏布局；320px 最大字号深色下仍可完成生成、预览、确认、手工编辑和日历查看。

2026-10-03 执行：

| 命令（`web/`） | 实际结果 | 范围 |
| --- | --- | --- |
| `pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `pnpm exec playwright test e2e/planning.spec.ts` | **6 passed** | 生成恢复、确认、手工版本、取消、拒绝及版本冲突 × 三种视口 |
| `pnpm exec playwright test e2e/ai-conversations.spec.ts` | **12 passed in 1.3m** | 普通对话回归及规划提案卡 × 三种视口 |
| `pnpm exec playwright test e2e/calendar.spec.ts` | **12 passed in 44.5s** | 原日历流程及确认计划块投影 × 三种视口 |
| `pnpm test:e2e` | **141 passed in 7.0m** | 账号、后台、AI、额度、任务、规划、日历、导入、订阅、治理和公开页面 Chromium 全量回归 |

浏览器流程使用随机 PostgreSQL、真实 API、AI worker、本机分块上游和生产构建预览；版本冲突增强后又分别在 desktop、mobile、tablet 定向重跑通过。每个视口检查 axe、控制台、API 500 和根级横向溢出。截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-planning-confirmed-manual-version.png`、`desktop-planning-320-large-dark.png`、`{desktop,mobile,tablet}-planning-calendar-projection.png` 及对应 AI planning card/proposal 文件，已人工检查，无重叠、裁切或空白画面。

Browser 插件技能已加载，但当前运行时没有暴露 `browser_*` 工具，因此按前端测试规范使用仓库 Playwright Chromium。执行/困难后续已由 M5-C 实现；真实 Edge、实体设备、正式第三方模型规划质量、复盘和完整首版闭环仍未验证或实现。

## M5-C：专注执行、困难记录与局部离线恢复

对应 Git 提交：`b251c9e`、`27c2c06`、`d50ca19`、`01119ac`、`0438fce`、`0045f41`、`4dab8bb`、`ae3fd3b`、`e3c7afe`。实际代码为迁移 `0025_execution_difficulties`、`server/src/itime/execution/`、任务步骤扩展、`web/src/pages/FocusPage.vue`、`DifficultyPanel.vue`、`FocusTimer.vue`、`FocusTaskProgress.vue`、`web/src/lib/focus-queue.ts` 及执行专项测试；合同见[执行与困难设计](../设计/iTime-执行与困难设计.md)。

已实现：

- 自由标题、普通任务、重复任务实例和当前计划块均可开始正计时或倒计时；账号级行锁和部分唯一索引保证 `running` 与 `paused` 合计最多一个开放专注。
- 开始、暂停、恢复、结束和放弃写入不可变事件并更新当前投影；前端按服务端时间锚点显示，刷新、离页和另一在线端可恢复。倒计时归零只进入超时显示，不自动结束专注或完成任务。
- 任务步骤可在专注内独立勾选；步骤全完成和专注结束都不改变任务终态，结束后仍需用户明确点击完成任务。
- 终态专注支持手工补记和修订，保存原值、新值、原因和版本；非法区间、超过 24 小时、开放会话修订及并发旧版本均被拒绝。
- 困难可独立或关联一个任务和一次专注，分类与感受均可空；支持编辑、删除、30 天内恢复和双 worker 到期清理，旧操作不能复活已清理正文。
- `/focus` 只为当前账号及已加载开放会话排队暂停、恢复、结束、放弃和步骤操作，队列最多 100 项并清理超过 7 天的项；未知响应复用原操作号，联网后顺序重放，冲突不会静默丢弃。
- 任务详情和当前计划块可携带对象标识进入专注；困难“带到 AI”只填普通对话草稿并移除查询参数，用户明确发送前不创建 AI 作业或额度预留。

2026-10-03 在 macOS、本地 PostgreSQL 18.6、真实 Uvicorn/Mailpit/worker、生产构建预览和 Playwright Chromium 153 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv run pytest -q` | **356 passed in 303.87s** | 服务端真实 PostgreSQL/HTTP 全量回归，含迁移、并发状态机、修订、困难回收和清理 |
| `cd server && uv run alembic upgrade head` | 通过 | 开发库升级到执行与困难迁移 |
| `cd server && uv run alembic current` | `0025_execution_difficulties (head)` | 开发库迁移版本 |
| `cd server && uv run alembic check` | 无新升级操作 | 迁移与 ORM 一致 |
| `cd server && uv run python -m itime.execution.cleanup_worker --once` | 通过 | 独立困难清理进程可单次运行 |
| `cd server && uv build --no-sources` | 通过 | sdist 和 wheel |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `cd web && pnpm test:e2e` | **171 passed in 8.2m** | 账号、后台、AI、额度、任务、规划、执行、困难、日历、导入、订阅、治理和公开页面三视口全量回归 |

浏览器流程覆盖自由/任务专注、步骤与任务完成分离、刷新恢复、补记修订、离线顺序重放、未知响应幂等恢复、开放与终态冲突、账号隔离、100 项/7 天队列边界、困难增删改恢复、任务/计划块入口和 AI 草稿。每个视口检查 axe、控制台、API 500 和根级横向溢出；截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-focus-*.png` 及 `desktop-focus-320-large-dark.png`，人工检查未见重叠、裁切、空白画面或动态布局跳动。

Browser 插件未在当前执行环境暴露，因此按前端测试规范使用仓库 Playwright Chromium。复盘与记忆、个人统计、本人数据导出、通用同步和账号注销后续分别由 M6-C、M6-D、M6-E、M6-F、M6-G 实现；困难语音、真实 Edge/实体设备、性能和生产部署仍未实现或验证。

## M6-C：单日复盘与确认式长期记忆

对应 Git 提交：`43a55e6`、`f18999b`、`4a0adbc`、`68962f9`、`0ec0cb3`、`cf1b488`、`e9cbebf`、`9daf825`、`180f395`、`283e168`、`d00f7a9`。实际代码为迁移 `0026_review_memory`、`server/src/itime/review/`、通用 AI worker 的 `review` 能力、`web/src/pages/ReviewsPage.vue`、复盘与记忆组件/API，以及 `web/e2e/reviews.spec.ts`；合同见[复盘与记忆设计](../设计/iTime-复盘与记忆设计.md)。

已实现：

- 同账号同活动日只有一个复盘会话；活动日按用户时区 04:00 切换。事实快照区分计划块、任务完成、专注和困难，允许在没有计划记录时补充事实并继续。
- 复盘短对话最多三轮单问题追问，用户可回答继续或随时跳过；问题和回答先持久化，离页后通过作业状态恢复，不依赖浏览器连接存活。
- `review` 独立 AI 能力使用目的限定的结构化工具输出。服务端校验事实来源、用户说明、AI 推测、下一步、信息缺口和记忆候选，不把模型文本直接当成业务写入。
- 报告版本不可变，重新生成只新增版本；事实或用户说明变化会使旧报告标记过期但不删除。批注独立增改删，不改变报告正文。
- 记忆候选只有用户确认后才形成长期记忆；拒绝不写入。用户可人工新增、编辑、暂停全部读取、删除、30 天内恢复；删除和暂停立即退出普通对话、规划与复盘的新请求上下文。
- 接单、作业、额度预留、报告/问题终态、供应商成本和治理记录沿用同一原子事务与租约机制。取消释放预留且保留已保存输入，AI 冻结只阻止新作业。
- `/reviews` 在桌面使用侧栏和主工作区，在手机/平板使用可滚动顶部导航与单列内容；支持活动日选择、历史版本、批注、记忆建议和独立记忆管理。

2026-10-03 在 macOS、本地 PostgreSQL 18.6、真实 Uvicorn/Mailpit/AI worker、生产构建预览和 Playwright Chromium 153 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv run pytest -q tests/test_review_models.py tests/test_review_sessions.py tests/test_review_provider.py tests/test_review_worker.py tests/test_review_annotations.py tests/test_review_cleanup.py tests/test_memory_context.py tests/test_ai_conversation_worker.py tests/test_planning_worker.py` | **40 passed in 50.33s** | 迁移、事实、会话、provider、worker、额度释放、报告、批注、记忆生命周期及跨能力上下文 |
| `cd server && uv run pytest -q` | **380 passed in 313.74s** | 服务端真实 PostgreSQL/HTTP 全量回归 |
| `cd server && uv run alembic upgrade head && uv run alembic current && uv run alembic check` | `0026_review_memory (head)`，无新升级操作 | 开发库迁移及 ORM 一致性 |
| `cd server && uv run python -m itime.review.cleanup_worker --once` | 通过 | 记忆到期清理进程可单次运行 |
| `cd server && uv build --no-sources` | 通过 | sdist 和 wheel |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `cd web && pnpm exec playwright test e2e/reviews.spec.ts e2e/planning.spec.ts --grep "daily review\|cancelled review\|生成、离页恢复"` | **9 passed in 1.3m** | 复盘主流程、取消恢复、记忆启停/删除/恢复/编辑、普通对话与规划记忆上下文 × 三视口 |
| `cd web && pnpm exec playwright test e2e/focus.spec.ts e2e/planning.spec.ts e2e/tasks.spec.ts e2e/calendar-imports.spec.ts e2e/calendar-subscriptions.spec.ts` | **63 passed in 3.4m** | 七项导航引入后的 320px 与既有核心工作区回归 |
| `cd web && pnpm test:e2e` | **177 passed in 9.0m** | 账号、后台、AI、额度、任务、规划、执行、困难、复盘、记忆、日历、导入、订阅、治理和公开页面三视口全量回归 |

浏览器流程覆盖补充事实、短追问、跳过、离页恢复、报告重生成与版本切换、批注、候选确认/拒绝、记忆启停/增改删/恢复、删除后退出检索、普通对话与规划实际收到的记忆上下文，以及取消后恢复可操作状态。每个视口检查 axe、控制台、框架错误层和根级横向溢出；截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-review-desktop-report.png` 与 `mobile-review-320-large-dark.png`，人工检查未见重叠、裁切或空白画面。

Browser 插件未在当前执行环境暴露，因此按前端测试规范使用仓库 Playwright Chromium。个人统计、本人数据导出、通用同步和账号注销后续分别由 M6-D、M6-E、M6-F、M6-G 实现；困难语音、真实 Edge/实体设备、正式第三方模型质量、性能和生产部署仍未实现或验证。

## M6-D：个人日周月统计

对应 Git 提交：`5bd7176`、`aaaabfc`、`0d79007`、`82f4f0f`、`69844d2`、`f13a238`、`bc13db1`。实际代码为 `server/src/itime/statistics/`、`server/tests/test_statistics_time_math.py`、`server/tests/test_statistics_api.py`、`web/src/pages/StatisticsPage.vue`、统计组件/API/CSS 及 `web/e2e/statistics.spec.ts`；合同见[个人统计设计](../设计/iTime-个人统计设计.md)。

已实现：

- `GET /api/v1/statistics` 按本人日、自然周或自然月返回计划、任务和专注的独立汇总与逐活动日序列；当前周/月截止服务器判定的当前活动日，未来范围拒绝读取。
- 服务端按用户当前 IANA 时区的当地 04:00 构造活动日窗口，覆盖 DST；计划块和专注跨活动日时按交集分配，人工补记或修订使用稳定整数比例分配并保持总秒数守恒。
- 计划只读取当前周计划版本并按块、任务去重；任务按当前完成或跳过投影归属；专注分别统计完成、放弃、人工补记、修订和进行中，不从步骤、计时或计划块推断任务完成。
- 聚合在查询时从权威事实计算，不新增统计表或迁移，不触发 AI、额度或同步写入；响应不返回任务、困难、复盘正文或专注事件详情。
- `/stats` 提供日/周/月切换、前后范围、回到当前、三组独立总览和趋势、04:00 口径、空状态及相关业务入口；不合成效率分或无可靠分母的百分比。
- 首次读取失败显示原因、请求编号和重试；已有结果刷新失败时保留旧结果及计算时刻。桌面、平板、手机和 320px 最大字号深色均使用稳定响应式布局。
- 八项产品导航已在所有用户工作区可达。统计 E2E 发现并修复 320px 专注页导航横向溢出，窄屏只滚动导航容器且专注计数区改为稳定八列布局。

2026-10-04 在 macOS、本地 PostgreSQL 18.6、真实 Uvicorn、生产构建预览和 Playwright Chromium 153 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd server && uv run pytest -q tests/test_statistics_time_math.py tests/test_statistics_api.py` | **10 passed** | 活动日范围、DST、整数分配、真实 PostgreSQL/HTTP、所有权和统计聚合 |
| `cd server && uv run pytest -q tests/test_statistics_time_math.py tests/test_statistics_api.py tests/test_tasks.py tests/test_task_schedules.py tests/test_plan_versions.py tests/test_focus_sessions.py tests/test_execution_revisions.py tests/test_review_sessions.py` | **43 passed** | 统计及受影响任务、规划、执行、修订和复盘事实回归 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv run alembic upgrade head && uv run alembic current && uv run alembic check` | `0026_review_memory (head)`，无新升级操作 | 统计无新增表；开发库迁移及 ORM 一致性 |
| `cd server && uv build --no-sources` | 通过 | sdist 和 wheel |
| `cd server && uv run pytest -q` | **390 passed in 329.25s** | 服务端真实 PostgreSQL/HTTP 全量回归 |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `cd web && pnpm exec playwright test e2e/statistics.spec.ts` | **6 passed in 25.5s** | 真实数据、日周月、空态、失败保旧、三种视口、axe、控制台和溢出 |
| `cd web && pnpm exec playwright test e2e/focus.spec.ts e2e/statistics.spec.ts` | **30 passed in 1.3m** | 八项导航及 320px 专注布局修复回归 |
| `cd web && pnpm test:e2e` | **183 passed in 9.3m** | 账号、后台、任务、日历、AI、规划、执行、复盘、统计和公开页面 Chromium 全量回归 |

统计浏览器流程使用随机 PostgreSQL、真实 API 和生产构建预览，创建真实任务、当前计划版本、任务完成、人工补记及放弃专注后核对响应和页面。截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-statistics-populated.png` 与 `/tmp/itime-ui-evidence/mobile-statistics-320-large-dark.png`，已人工检查，无重叠、裁切、空白画面或根级横向溢出。

Browser 插件未在当前执行环境暴露，因此按前端测试规范使用仓库 Playwright Chromium。本人数据导出和通用同步后续分别由 M6-E、M6-F 实现；完成本模块不代表困难语音、注销、后台运营指标、长历史性能、真实 Edge/实体设备、正式第三方模型质量或生产部署已完成。

## M6-E：本人完整数据导出

对应 Git 提交：`83d930b`、`7919fbd`、`d02a49f`、`59906f1`、`55dea15`、`4f91bf6`、`998e741`、`d8a7d92`、`b66ff74`。实际代码为迁移 `0027_data_exports`、`server/src/itime/data_export/`、本人导出 API、独立 worker、`web/src/components/DataExportSettings.vue`、`web/src/lib/data-exports.ts`、`/account/data` 及 `web/e2e/data-exports.spec.ts`；合同见[本人数据导出设计](../设计/iTime-本人数据导出设计.md)。

已实现：

- 持久化作业记录排队、运行、成功、失败和过期状态，保存操作 ID、尝试、租约、产物和终态；部分唯一索引保证同一账号最多一个活动作业，响应未知时重用原操作 ID 或对账活动作业。
- worker 在 PostgreSQL `REPEATABLE READ READ ONLY` 事务中按显式字段白名单读取一致性快照；顶层覆盖账号、设置、任务、日历、对话、规划、执行、复盘、额度和媒体元数据，所有子表通过所有者路径限制，不使用模型反射自动导出未来字段。
- ZIP 包含完整 `itime-data.json`、分类 CSV、日历 ICS、Markdown 摘要和 `manifest.json`。清单保存逐文件 SHA-256、大小、格式和 schema 版本；CSV 防公式注入，ICS 由结构化库生成，稳定顺序便于重复核对。
- 密码摘要、邮件/会话令牌、CSRF、AI Key 密文、内部租约、供应商请求技术正文、质量样本及其他账号数据不进入导出。本人订阅 URL 只在快照事务中解密并写入日历分类。
- ZIP 在数据库外构建后使用独立 Fernet 密钥加密保存；默认限制明文 100 MiB、归档 50 MiB。双 worker 使用 `SKIP LOCKED` 和续租避免重复终态，失效租约可恢复，有界重试不无限占用。
- 产物默认提供 24 小时本人认证下载，到期后清除密文并显示重新生成；失败和过期元数据默认保留 30 天后限批删除。会话撤销后下载立即拒绝，非本人作业统一按未找到处理。
- `/account/data` 从账号概览和设置导航可达，支持创建、离页后恢复、轮询、刷新保留旧记录、未知响应对账、失败/过期恢复和真实 Blob 下载；完整 JSON 标为权威副本，CSV/ICS/Markdown 标为便携视图，不宣称支持整包恢复。

2026-10-04 在 macOS、本地 PostgreSQL 18.6、真实 Uvicorn、独立导出 worker、生产构建预览和 Playwright Chromium 153 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd server && uv run pytest -q tests/test_data_export_models.py tests/test_data_export_snapshot.py tests/test_data_export_archive.py tests/test_data_export_worker.py tests/test_data_export_api.py` | **14 passed in 14.59s** | 迁移/配置、所有权、一致性快照、格式/摘要、容量、租约/重试、加密、清理及真实 HTTP 下载 |
| 导出及受影响服务端组合回归 | **40 passed in 27.36s** | 配置、模型、会话鉴权和相邻生命周期回归 |
| `cd server && uv run pytest -q` | **404 passed in 332.71s** | 服务端真实 PostgreSQL/HTTP 全量回归 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv run alembic upgrade head && uv run alembic current && uv run alembic check` | `0027_data_exports (head)`，无新升级操作 | 开发库迁移及 ORM 一致性 |
| `cd server && uv build --no-sources` | 通过 | sdist 和 wheel |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `cd web && pnpm exec playwright test e2e/data-exports.spec.ts` | **6 passed in 29.5s** | 两条导出流程 × 电脑、手机、平板，含真实下载解包 |
| `cd web && pnpm exec playwright test e2e/auth.spec.ts e2e/privacy-governance.spec.ts e2e/quota.spec.ts e2e/data-exports.spec.ts` | **45 passed in 2.7m** | 账号、隐私、额度与导出设置受影响回归 |
| `cd web && pnpm test:e2e` | **189 passed in 9.5m** | 全部用户端、后台和公开页面 Chromium 全量回归 |

浏览器流程创建真实任务和导出作业，等待独立 worker 完成，通过浏览器下载 ZIP，再用 `unzip -p` 读取并核对任务标记、`manifest.json` SHA-256、文件集合和敏感字段排除；同时覆盖创建响应未知后对账、刷新失败保留旧记录、设置导航与空状态。截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-data-export-ready.png` 和 `/tmp/itime-ui-evidence/mobile-data-export-320-large-dark.png`，已人工检查，无重叠、裁切、空白画面或根级横向溢出。

收口时已重启开发 API 并启动 `itime.data_export.worker`；`GET /api/ready` 返回 200，OpenAPI 包含创建/列表、详情和下载路径，`http://localhost:5173/account/data` 返回 200，`itime_test_%` 测试数据库残留为 0。

Browser 插件未在当前执行环境暴露，因此按前端测试规范使用仓库 Playwright Chromium。通用同步和账号注销随后已由 M6-F、M6-G 实现；完成本模块不代表其他对象回收、真实 Edge/实体设备、长历史性能、生产密钥轮换、备份恢复或部署已经完成。

## M6-F：通用同步与有界离线恢复

对应 Git 提交：`f2795b3`、`97061e2`、`1849982`、`ba19707`、`46ee5b6`、`0bbeb8f`、
`3d7053a`、`6f696ba`、`b19d12f`、`0c64b11`、`b2c7946`、`81ac753`、`5905702`、
`53e5ca4`、`191e3f2`。实际代码为迁移 `0028_sync_feed`、`server/src/itime/sync/`、会话事件
接入、任务步骤合并端点、`web/src/lib/{data-cache,sync,sync-queue,use-sync-refresh}.ts`、
全局状态入口、`/account/sync`、各主页面刷新适配器及 `web/e2e/sync.spec.ts`；合同见
[通用同步设计](../设计/iTime-通用同步设计.md)。

已实现：

- 每个账号拥有独立同步状态和 HMAC 签名游标；初始、正常、多页、空页、损坏、跨账号、过期及
  数据库恢复后超前游标均有明确结果。变更事件只含类型、对象 ID、动作、版本和服务端时间，
  不含任务、消息、报告、记忆、URL 或凭据正文。
- 独立清理 worker 默认保留事件 30 天，每批限 1000 条；拉取使用账号状态行共享锁，清理使用
  排他锁并按账号排序，保证读取者得到完整旧页或明确重建。清理不删除业务对象、幂等结果或墓碑。
- 普通会话的创建、改名/归档、消息接单和成功/失败/取消终态均发聚合事件；既有任务、日历、
  计划、执行、困难、复盘、记忆及设置事件继续沿用原事务。
- 普通任务步骤和重复实例步骤使用专用端点安全收敛独立布尔值。其他字段变化可在当前版本重放，
  但步骤删除、任务归档/回收、重复模板变化和实例终态返回冲突，不以最后写入覆盖。
- Web 可见页默认每 2 秒拉取，成功写、联网、聚焦和跨标签广播会立即触发；每个标签保存自己的
  账号游标。相关页面自动刷新未编辑区域，本标签刚完成的写入不会反向打断当前操作。
- 当前已认证 SPA 会话把最近成功 GET 结果以临时内存密钥 AES-GCM 加密后写入 IndexedDB；
  仅真实离线时回退，HTTP 失败不被旧缓存掩盖。刷新后旧密钥不可恢复，登录、切账号和显式退出
  都清理旧账号数据。
- 普通/重复任务步骤和已有专注操作分别使用固定 schema 的账号级队列，各最多 100 项、最长
  7 天；网络未知结果保留原操作 ID，恢复后顺序重放。冲突保留当前及后续项，用户可查看对象或
  明确丢弃，不会静默丢失。
- `/account/sync` 与产品导航图标展示在线、同步中、已同步、待传、冲突和失败状态，显示最后
  成功时间、最近服务端变化、缓存回退、待传数及请求编号；无待传内容时不展示危险清理动作。

2026-10-04 在 macOS、本地 PostgreSQL 18.6、真实 Uvicorn、同步清理 worker、生产构建预览和
Playwright Chromium 153 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| 同步专项及受影响服务端组合回归 | **56 passed in 63.23s** | 迁移/回退、ORM、游标、隔离、清理竞争、会话事件、普通/重复步骤合并及相邻领域回归 |
| `cd server && uv run pytest -q` | **412 passed in 350.88s** | 服务端真实 PostgreSQL/HTTP 全量回归 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv run alembic upgrade head && uv run alembic current && uv run alembic check` | `0028_sync_feed (head)`，无模型漂移 | 开发库迁移与 ORM 一致性 |
| `cd server && uv run python -m itime.sync.cleanup_worker --once` | 通过 | 独立同步事件清理进程受控执行 |
| `cd server && uv build --no-sources` | 通过 | sdist 和 wheel |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `cd web && pnpm exec playwright test e2e/sync.spec.ts` | **18 passed** | 六条真实同步流程 × 电脑、手机、平板 |
| 账号、任务、日历、规划、专注、复盘、统计及同步受影响 Chromium 回归 | **117 passed in 5.3m** | 同步接入页面和相邻流程 |
| `cd web && pnpm test:e2e` | **207 passed in 10.0m** | 全部用户端、后台和公开页面 Chromium 全量回归 |

浏览器流程使用两个真实 browser context 登录同一账号，以 5 秒断言上界核对跨端任务出现；
离线连续勾选后恢复网络，核对服务器最终状态、其他字段未回退及队列归零。另覆盖加密缓存不含
明文、HTTP 500 不回退缓存、损坏/过期游标重建、普通与重复步骤、终态冲突保留、显式退出取消
与确认清理、键盘、粗指针、减少动效、axe、控制台和根级横向溢出。截图位于
`/tmp/itime-ui-evidence/{desktop,mobile,tablet}-sync-status.png`、
`/tmp/itime-ui-evidence/mobile-sync-status-320-large-dark.png` 和
`/tmp/itime-ui-evidence/desktop-sync-conflict.png`，已人工检查，无重叠、裁切、空白画面或
状态歧义。

收口时已重启开发 API 并启动 `itime.sync.cleanup_worker`；`GET /api/ready` 返回 200，
OpenAPI 包含同步变更、状态和两类步骤端点，`http://localhost:5173/account/sync` 返回 200，
未认证同步状态请求返回 401，`itime_test_%` 测试数据库残留为 0。

Browser 插件未在当前执行环境暴露，因此按前端测试规范使用仓库 Playwright Chromium。账号
注销和公开链接随后分别由 M6-G、M4-F 实现；完成本模块不代表其他对象回收、困难语音、其他业务
对象提案、正式模型质量、M8 性能分位数、真实 Edge/实体设备、备份恢复或生产部署已经完成。

## M6-G：7 天可撤销账号注销

对应 Git 提交：`6136fc2`、`b11e891`、`71200e1`、`f1a7861`、`e74fe9b`、`882483b`、
`1edb509`、`852043f`、`99541b0`。实际代码为迁移 `0029_account_deletions`、
`server/src/itime/account_deletion/`、认证/后台/导出/同步等相邻领域接入、
`web/src/components/AccountDeletionSettings.vue`、登录恢复页和
`web/e2e/account-deletion.spec.ts`；合同见[账号注销设计](../设计/iTime-账号注销设计.md)。

已实现：

- 普通用户在 `/account/data` 重新输入完整注册邮箱和当前密码后申请注销；管理员账号被保护。
  申请事务创建 7 天到期作业、冻结账号、递增认证代次并撤销全部设备，响应丢失时可通过正确
  凭据登录重新读取同一截止时间，不会重复创建作业。
- 申请同步停止 queued/running AI 作业及额度预留、导出作业及已有密文、订阅刷新、开放专注、
  未发送账号邮件和临时媒体。各 worker claim 只选择活动用户，旧 lease 的晚到结果不能重新写入
  回复、提案、报告、样本、扣额、导出产物或订阅投影。
- 正确密码才显示“账号正在注销”和截止时间；错误密码保持统一凭据错误。到期前撤销会恢复原
  用户和持久业务数据，只创建一个新会话，已终止的临时工作不自动复活；找回密码不会隐式撤销。
- 独立 `itime.account_deletion.worker` 以租约领取到期作业，按用户再作业的顺序锁定，在同一
  事务中清除邀请和审计关联、写最小完成回执并删除用户根行。失败完整回滚并指数退避，多 worker
  使用 `SKIP LOCKED`；完成回执不含邮箱、原用户 UUID、正文或对象数量。
- Web 申请成功后通过 `BroadcastChannel` 清理所有标签页的当前账号内存身份、IndexedDB、
  同步游标及任务/专注待传队列。后台只读显示“注销中”和剩余期限，不能代用户撤销或延长。
- 隔离 E2E 只在 `tests.support_app` 提供一次性本人到期控制票据，需活动会话和 CSRF 签发，
  不接受 `user_id` 且不进入生产应用；测试使用真实常驻注销 worker，不直接调用清理函数。

2026-10-04 在 macOS、本地 PostgreSQL 18.6、真实 Uvicorn、Mailpit、AI/订阅/导出/同步/注销
worker、生产构建预览和 Playwright Chromium 153 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| 账号注销专项服务端组合回归 | **16 passed in 22.99s** | 迁移/回退、状态约束、申请/撤销、并发、晚到结果、双 worker、故障回滚、全 owner 表清理和同邮箱重注册 |
| 认证、后台、AI、规划、复盘、订阅、导出及专注受影响服务端回归 | **72 passed in 65.81s** | 相邻权限、作业终态、额度、快照和在途停止 |
| `cd server && uv run pytest -q` | **430 passed in 371.65s** | 服务端真实 PostgreSQL/HTTP 全量回归 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv run alembic upgrade head`、`current`、`check` | `0029_account_deletions (head)`，无模型漂移 | 开发库迁移与 ORM 一致性 |
| `cd server && uv run python -m itime.account_deletion.worker --once` | 通过 | 无到期作业时受控退出 |
| `cd server && uv build --no-sources` | 通过 | sdist 和 wheel |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `cd web && pnpm exec playwright test e2e/account-deletion.spec.ts` | **6 passed in 39.4s** | 两条完整注销流程 × 电脑、手机、平板 |
| 账号、后台、导出、隐私、公开页及同步受影响 Chromium 回归 | **87 passed in 3.7m** | 注销接入页面和相邻流程 |
| `cd web && pnpm test:e2e` | **216 passed in 10.9m** | 全部用户端、后台和公开页面 Chromium 全量回归 |

真实到期流程先创建持久任务和可下载导出，申请后验证第二设备与全部标签本地状态失效；正确凭据
撤销后任务仍在而导出临时文件不可下载。再次申请并由受控测试端点推进到期后，真实 worker 删除
账号；旧凭据仅得到统一 401，探针确认旧用户、旧邮箱及所有 `owner_id` 行均为 0、最小回执为 1。
同邮箱随后完成真实邮件验证和重新注册，得到新 UUID，旧任务不可读取。额外验证测试端点拒绝
任意 `user_id` 和错误 CSRF。

截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-account-deletion-dialog.png`、
`/tmp/itime-ui-evidence/{desktop,mobile,tablet}-account-deletion-pending.png`、
`/tmp/itime-ui-evidence/mobile-account-deletion-320-large-dark.png` 和
`/tmp/itime-ui-evidence/desktop-admin-management-account-deletion.png`。已逐张人工检查，邮箱、
截止时间、按钮、导航和危险操作层级无重叠、裁切、空白画面或横向溢出。

收口时开发 API、Vite 和 `itime.account_deletion.worker` 已运行；`GET /api/ready` 返回 200，
OpenAPI 包含申请与撤销路由，`http://localhost:5173/account/data` 返回 200，`itime_test_%`
测试数据库残留为 0。实施计划明确要求仓库 Playwright，因此使用 Chromium 执行真实浏览器流程。
公开链接随后由 M4-F 实现。本模块不代表其他对象单项回收、困难/任务语音、其他业务对象提案、
正式模型质量、M8 性能分位数、真实 Edge/实体设备、备份恢复删除台账重放或生产部署已经完成。

## M4-F：任务公开链接受限读取

对应 Git 提交：`0faf20d`、`0f66948`、`9deea1b`、`057c1d5`、`f24713b`、`e87b9ec`、
`069ddc7`、`349f48d`、`f6bf74d`、`dea8457`、`9503cac`、`456d8e7`、`e459084`、
`03812a9`、`ea2e0a8`、`1df3c90`、`02b0660`。实际代码为迁移
`0030_public_task_links`、`server/src/itime/network/restricted_http.py`、
`server/src/itime/public_links/`、任务/规划/对话/导出/注销接入、
`web/src/components/TaskEditor.vue`、任务 API/CSS 及 `web/e2e/public-links.spec.ts`；合同见
[公开链接读取设计](../设计/iTime-公开链接读取设计.md)。

已实现：

- 新增任务链接或修改 URL 后异步排队；同 URL 改标题/顺序保留链接 ID 和成功快照，URL 变化递增
  revision、清除旧正文并使晚到结果失效。本人可通过
  `POST /api/v1/tasks/{task_id}/links/{link_id}/refresh` 手动刷新，30 秒内重复刷新受限。
- ICS 订阅与公开链接共用受限 HTTP 传输，生产/开发只接受 HTTPS，并逐跳校验 URL、全部 DNS
  结果和实际 peer；不使用系统代理、Cookie、用户凭据或平台 Key。跳转、连接/总时间、压缩体、
  解压体和压缩比均有硬上限。
- HTML/XHTML/纯文本经结构化解析、字符集回退、NFKC 和隐藏节点过滤生成确定性标题、摘要与
  有界正文。摘要不是 AI 生成结论；任务页显示状态、来源主机、取得时间和哈希。
- 独立 `itime.public_links.worker` 使用数据库租约，单账号同时最多一个 running，10 分钟最多
  领取 20 次；可重试失败按 1/5 分钟退避，最多 3 次。写回二次检查账号、任务、链接 revision、
  attempt 和 lease；归档、回收、删除或注销后旧请求不能恢复正文。
- 刷新失败保留最近成功快照并明确显示；成功或最终失败发出 `task_link` 失效事件，其他标签页
  重读任务详情而不覆盖正在编辑的草稿。
- 规划把候选任务来源复制进不可变 `PlanningInputSnapshot`；普通对话按相关性选择最多三个来源
  并复制进 `ChatGeneration.public_source_context`。两种 provider 都把网页标为不可信资料，
  页面中的指令不能扩大权限、调用工具或直接修改业务对象。
- 本人导出包含用户可见来源、摘要、正文、哈希和状态但排除租约；任务到期清理和账号注销级联
  清除正文并使在途 claim 失效。

2026-10-04 在 macOS、本地 PostgreSQL 18.6、真实 Uvicorn、受控 HTTP 来源、公开链接/AI 等
业务 worker、生产构建预览和 Playwright Chromium 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| 公开链接专项及受影响服务端组合回归 | **189 passed** | 共享网络、日历兼容、迁移/回退、解析、抓取、租约、API、规划/对话快照、导出、注销与相邻任务流程 |
| `cd server && uv run pytest -q` | **488 passed in 388.08s** | 服务端真实 PostgreSQL/HTTP 全量回归 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv run alembic upgrade head`、`current`、`check` | `0030_public_task_links (head)`，无模型漂移 | 开发库迁移与 ORM 一致性 |
| `cd server && uv run python -m itime.public_links.worker --once` | 通过 | 无待处理链接时受控退出 |
| `cd server && uv build --no-sources` | 通过 | sdist 和 wheel |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `cd web && pnpm test:e2e -- e2e/public-links.spec.ts` | **4 passed, 2 skipped** | 成功/刷新流程三视口；深度失败与 AI 注入矩阵只在 desktop 执行一次 |
| 任务、规划、AI、导出、注销及日历受影响 Chromium 回归 | **55 passed, 2 skipped** | 公开资料接入页面和相邻业务流程 |
| `cd web && pnpm test:e2e` | **220 passed, 2 skipped in 11.6m** | 全部用户端、后台和公开页面 Chromium 全量回归 |

浏览器流程通过真实页面创建任务并观察 `queued → running → ready`，核对摘要、来源、时间、哈希
和手动刷新；另覆盖失败保留旧摘要、第二标签同步、链接编辑使旧响应失效，以及真实 AI 上游只收到
带独立不可信边界的网页资料。每个视口检查 axe、控制台、Vite 错误层和根级横向溢出。截图位于
`/tmp/itime-ui-evidence/{desktop,mobile,tablet}-public-links-{ready,refreshed}.png`、
`/tmp/itime-ui-evidence/desktop-public-links-{failed-with-stale,ai-untrusted-source}.png` 和
`/tmp/itime-ui-evidence/desktop-public-links-320-large-dark.png`，已人工检查无重叠、裁切或
空白画面。

实现与回归过程中修正了同账号双 worker 串行领取、PostgreSQL/应用时钟毫秒竞态、空成功快照
约束、HTML 换行清洗、窄屏来源状态布局，以及完整日历回归在周日跨周选日的问题。Browser
DOM/控制台检查与 Playwright 截图均完成；Browser 截图工具超时时未把未取得的证据记为通过。

收口时已用当前代码重启开发 API、AI/导出 worker 并启动 `itime.public_links.worker`，Vite
继续运行；`GET /api/ready` 返回 200，OpenAPI 包含链接刷新路由，
`http://localhost:5173/tasks` 返回 200。环境核查发现 6 个中止浏览器回归留下且无活动连接的
`itime_test_%` 数据库，按测试资源命名边界清理后残留为 0；开发数据库未删除或重建。

`REQ-LINK-01` 首版范围已完成。任务实例补做 Web 入口随后由 M3-Q 实现；本模块不代表任务/
困难语音、其他对象单项回收、其他业务对象提案、正式公网兼容性、生产受限出口、正式模型质量、
M8 性能分位数、真实 Edge/实体设备、备份恢复或生产部署已经完成。

## M3-Q：重复任务实例关联补做 Web 入口

对应 Git 提交：`dcb8d83`、`36c4128`。实际代码为
`web/src/lib/task-schedules.ts`、`web/src/components/CalendarOccurrencePanel.vue`、
`web/src/calendar.css` 和 `web/e2e/calendar.spec.ts`；既有服务端合同为
`POST /api/v1/tasks/{task_id}/occurrences/makeup`，设计见
[任务核心服务设计](../设计/iTime-任务核心服务设计.md)。

已实现：

- 统一日历只在重复任务的基础实例显示“安排补做”；补做实例、非重复任务、课程/固定安排和计划
  块不显示该入口，课程“补课”与任务“补做”保持不同领域语义。
- 日期型任务只提交补做日期；定时型任务提交当地日期和时刻，DST 重复时刻沿用现有 UTC 偏移
  选择。请求携带任务版本和稳定 `operation_id`，响应未知时相同输入可安全重试；版本冲突重新
  读取任务详情并保留当前表单。
- 成功后统一日历重读服务端事实。原实例状态保持不变，新实例显示“关联补做 · 原实例日期”，
  可独立完成、跳过、重新打开、调整截止日期和同步步骤，但不能继续创建链式补做。
- 新表单使用 `h1 → h2` 语义层级；实施时同步修正同一详情面板既有调课、覆盖、补课和任务改期
  表单的标题跳级，视觉字号保持不变。

2026-10-04 在 macOS、本地 PostgreSQL 18.6、真实 Uvicorn/Mailpit、生产构建预览和
Playwright Chromium 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| 任务日期、生命周期、统计与导出受影响服务端回归 | **26 passed in 23.01s** | 关联补做、任务清理、统计投影与导出快照 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv run pytest -q` | **488 passed in 384.42s** | 服务端真实 PostgreSQL/HTTP 全量回归 |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| 补做专项 Chromium | **3 passed in 18.5s** | 完整补做流程 × desktop、mobile、tablet |
| `cd web && pnpm exec playwright test e2e/calendar.spec.ts` | **15 passed in 49.6s** | 日历、任务补做、课程补课、系列冲突和 DST 三视口回归 |
| `cd web && pnpm test:e2e` | **223 passed, 2 skipped in 12.0m** | 全部用户端、后台和公开页面 Chromium 全量回归 |

浏览器流程从真实注册、邮件验证、任务创建和重复设置开始，在基础实例创建关联补做，查询服务端
确认原实例仍为 `open`，再打开补做实例核对来源并独立完成。三个视口均检查 axe、控制台、Vite
错误层和根级横向溢出；desktop 另在 320×700 最大字号深色下展开表单。截图位于
`/tmp/itime-ui-evidence/{desktop,mobile,tablet}-calendar-task-makeup-{form,completed}.png`
及 `/tmp/itime-ui-evidence/desktop-calendar-task-makeup-form-320-large-dark.png`，已人工检查
无重叠、裁切、空白画面或操作混淆。

Browser 插件也在 `http://localhost:5173` 真实完成注册、邮件验证、登录、重复任务创建、日历
跳转和补做提交；页面身份、DOM、成功状态、无 Vite 覆盖层及无横向溢出均通过。截图接口因浏览器
渲染器节流不可用，视觉证据使用上述 Playwright 截图；控制台唯一警告来自 Browser 插件自身的
EventEmitter listener 上限，不是应用脚本。该临时账号已从本地开发库清理。

收口时 `GET /api/ready` 与 `http://localhost:5173/calendar` 均返回 200，`itime_test_%` 测试
数据库残留为 0。任务实例补做的首版 Web 范围已完成；任务/困难语音与 AI 校对、正式
Edge/实体设备、性能和生产部署仍未完成或验证。

## M5-E：订阅来源更新后的已确认计划冲突

对应 Git 提交：`abcd2d3`、`0cb2b60`、`7080bcd`、`82daba6`、`7910377`、`5b4adcd`、
`9c115c2`。专项
设计见[订阅来源计划冲突设计](../设计/iTime-订阅来源计划冲突设计.md)，实际代码为迁移
`0031_plan_source_conflicts`、`server/src/itime/planning/source_conflict_service.py`、订阅
刷新/计划 API/导出集成，以及 `web/src/pages/{PlansPage,CalendarPage}.vue` 和对应测试。

已实现：

- 订阅 worker 在同一事务和 planning revision 锁内比较当前/未来计划周的来源更新前后投影。
  新增、移动、取消或占用状态变化创建来源级复核记录；名称、说明、地点、304、刷新失败和本地
  `busy=false` 不制造调度冲突。
- `PlanSourceConflict` 按当前计划版本、来源和成功代次唯一，保存有界前后时段与实际重叠块；
  同来源后续变化 supersede 旧开放记录，容量超限保存不伪造明细的保守复核状态。
- 来源更新不改写计划块或计划版本。`POST
  /api/v1/plans/{plan_id}/source-conflicts/resolve` 以稳定操作 ID、当前计划版本和精确冲突 ID
  幂等确认“保留当前计划”；新整周计划版本把旧记录标记为已重排。
- 冲突创建和处理复用 `plan` 同步事件；当前计划、范围计划块和本人 JSON 导出返回同一事实。
  账号注销通过 owner/plan/source 组合外键级联清理，不把来源 URL、UID、ICS 正文或租约写入
  冲突记录。
- 规划页显示来源、代次、检出时间、前后时段、实际重叠数量、进入日历、按最新来源重新生成和
  保留当前计划；统一日历显示范围提示，只给真正重叠的计划块标记冲突，并可按 query 直接打开
  对应订阅来源。

2026-10-04 在 macOS、本地 PostgreSQL 18.6、真实 Uvicorn/Mailpit、受控 ICS HTTP 来源、
真实订阅 worker、生产构建预览和 Playwright Chromium 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| 迁移/模型专项 | **15 passed in 55.13s** | 0031 升降级、约束、ORM、同步/导出/注销 head 兼容 |
| 来源冲突与既有订阅/规划输入专项 | **22 passed in 17.34s** | 移动、取消、失败、覆盖、容量、投影与 worker |
| 处理 API、版本、同步、导出和注销受影响回归 | **61 passed in 54.83s** | 幂等、跨账号、并发、新版本、change feed、JSON 导出与级联 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv build --no-sources` | 通过 | sdist 与 wheel |
| `cd server && uv run pytest -q` | **499 passed in 398.28s** | 服务端真实 PostgreSQL/HTTP 全量回归 |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| 来源冲突专项 Chromium | **3 passed in 36.2s** | 保留与重新生成完整处理流程 × desktop、mobile、tablet |
| 订阅、日历、规划受影响 Chromium | **30 passed in 2.5m** | 三页面全部三视口回归 |
| `cd web && pnpm test:e2e` | **226 passed, 2 skipped** | 228 项收集；全量状态 `passed`，两项仍为 public-links 非桌面深度场景 |

真实浏览器流程从注册、邮件验证、时区设置、ICS URL 创建和当前计划创建开始；来源第二代把
11:00—12:00 移到 09:30—10:30 后，三个视口均看到“第 1 版计划没有被自动改动”、前后时段和
一个实际重叠块。从提示进入日历可直接打开来源，计划块详情显示来源重叠；明确保留后提示消失且
服务端计划版本和块内容不变。来源第三代取消后重新出现“未与计划块重叠”的复核提示；三个视口
随后均从该提示显式重新生成、确认第 2 版并验证旧冲突消失。

每个视口检查 axe、控制台、Vite 错误层和根级横向溢出；desktop 另在 320×700 最大字号深色下
检查。截图位于
`/tmp/itime-ui-evidence/{desktop,mobile,tablet}-calendar-subscription-plan-source-conflict.png`
及
`/tmp/itime-ui-evidence/desktop-calendar-subscription-plan-source-conflict-320-dark-large.png`，
已人工检查无内容重叠、裁切或空白画面。

Browser 插件在 `http://localhost:5173` 真实完成临时账号登录和 AI→规划导航；页面标题为
“规划 · iTime”，主内容非空，无 Vite 覆盖层、横向溢出或控制台错误。插件截图调用因
`IDE Command timeout` 未取得证据，视觉结论只采用上述 Playwright 截图；临时账号随后从本地
开发库删除。

收口时 Alembic 为 `0031_plan_source_conflicts (head)` 且 `alembic check` 无漂移，
`GET /api/ready`、`http://localhost:5173/plans` 和 `/calendar` 均返回 200，
`itime_test_%` 数据库残留为 0。`REQ-CAL-03` 的首版范围已完成；订阅 occurrence 用户单次例外、
任务/困难语音与 AI 校对、正式 Edge/实体设备、性能和生产部署仍未完成或验证。

## M3-R：订阅 occurrence 单次例外

对应 Git 提交：`5b2dc0d`、`dada25b`、`2b4c703`、`ff4d11f`、`c9b3c5a`、`ef7fef6`、
`bce18a6`。专项设计见
[订阅 Occurrence 单次例外设计](../设计/iTime-订阅Occurrence单次例外设计.md)，实际代码为迁移
`0032_subscription_exceptions`、`server/src/itime/calendar/subscription_exception_service.py`、
订阅投影/API、本人导出和账号注销接入，以及
`web/src/components/CalendarOccurrencePanel.vue` 和
`web/e2e/calendar-subscriptions.spec.ts`。

已实现：

- `CalendarSubscriptionException` 以订阅主事件和稳定来源 occurrence key 唯一定位，组合外键
  约束本人归属。支持单次取消、定时/全天移动和名称/说明/地点/占用覆盖；DELETE 重置真正恢复
  当前来源，不使用伪覆盖。
- 来源主事件、来源 RECURRENCE-ID、字段级本地覆盖和单次例外按固定顺序投影。来源刷新不改写
  例外；来源取消后，本地移动仍保持 scheduled，并单独返回
  `source_status=source_cancelled` 供界面解释。
- 普通查询先按最多 2000 个已展开组合 key 精确加载例外，再按移动 UTC 区间索引补入原日期不在
  当前范围的实例；来源规则暂时移除 key 时例外休眠，不生成孤立安排。
- 创建、更新和重置使用 `operation_id`、来源版本及例外版本。例外写入和订阅 worker 都先锁
  `CalendarSource`；双会话创建、来源刷新和更新/重置竞争不会产生重复或最后写入覆盖。
- 实际变化递增来源版本并发出 `calendar_source` 无正文同步事件；新表触发 planning input
  revision。本人 JSON 保存全部例外字段，CSV 增加例外行，ICS 用最终 RECURRENCE-ID 覆盖来源
  修订；账号注销通过外键级联清理。
- 统一日历详情为订阅实例提供取消、调整时间、覆盖内容、重置为来源和来源管理。全天移动使用
  排他结束日期；来源取消但本地移动保留时显示明确来源状态。

2026-10-04 在 macOS、本地 PostgreSQL 18.6、真实 Uvicorn/Mailpit、受控 ICS HTTP 来源、真实
订阅 worker、生产构建预览和 Playwright Chromium 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| 迁移模型与 planning trigger 专项 | **9 passed** | 0032 升降级、CHECK、唯一键、组合所有权外键、级联及 revision |
| 订阅例外服务专项 | **4 passed** | 取消/重置、定时/全天移动、覆盖、DST、RID、刷新保留、来源取消、跨账号、并发和幂等 |
| 日历、订阅、规划输入受影响服务组合 | **50 passed in 53.93s** | 统一投影、冲突链接、既有系列/例外/补课、来源计划冲突 |
| 导出、同步和注销生命周期组合 | **15 passed in 13.20s** | JSON/CSV/ICS、无正文来源事件及账号级联 |
| `cd server && uv run pytest -q` | **507 passed in 415.27s** | 服务端真实 PostgreSQL/HTTP 全量回归 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv build --no-sources` | 通过 | sdist 与 wheel |
| `cd server && uv run alembic current`、`check` | `0032_subscription_exceptions (head)`，无模型漂移 | 开发库迁移与 ORM 一致性 |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| 订阅例外专项 Chromium | **3 passed in 33.0s** | 取消、重置、移动、刷新保留与来源取消 × 三视口；desktop 含全天及 320px |
| `calendar-subscriptions.spec.ts` | **12 passed in 1.7m** | 订阅既有与新增流程全量三视口 |
| 受影响 Chromium | **62 passed**；并行高负载下 1 个既有 tablet 规划提示超时，空载复跑 **1 passed** | 注销、订阅、日历、导出、规划、同步 |
| `cd web && pnpm test:e2e` | **229 passed, 2 skipped in 12.5m** | 231 项收集；两项仍为 public-links 非桌面深度场景 |

真实浏览器流程从注册、邮件验证、时区设置和受控 ICS URL 创建开始；三个视口依次取消并重置同一
occurrence，再把它移动到新日期。来源下一代改名后本地移动保持，来源随后整体取消后仍显示
“来源已取消，本地移动仍保留”。desktop 另把来源全天事件移动为新的全天日期区间，并在
320×700 深色最大字号下复核最终详情。

三个视口均检查 axe、控制台、API 500、Vite 错误层和根级横向溢出。截图位于
`/tmp/itime-ui-evidence/{desktop,mobile,tablet}-calendar-subscription-occurrence-exception.png`
及
`/tmp/itime-ui-evidence/desktop-calendar-subscription-occurrence-exception-320-dark-large.png`，
已人工检查无内容重叠、裁切、空白画面或按钮溢出。本会话未暴露 Browser 插件工具，因此按前端
测试规范使用仓库 Playwright 完成渲染和交互验证。

收口时开发 API 已按当前代码重启，`GET /api/ready` 返回 200；Vite `/calendar` 可访问，
`itime_test_%` 数据库残留为 0。订阅 occurrence 单次例外的首版范围已完成；任务/困难语音与 AI
校对、其他对象单项回收、正式第三方订阅兼容性、Edge/实体设备、性能和生产部署仍未完成或验证。

## M4-G：AI 能力级备用服务路由

对应 Git 提交：`9356109`、`3df1c6b`、`8ed03f3`、`14f429a`、`680e3de`、`f2f1aa6`、
`8f2b532`。专项设计见[AI 备用服务路由设计](../设计/iTime-AI备用服务路由设计.md)，实际代码为
迁移 `0033_ai_fallback_routes`、`server/src/itime/ai/routing_service.py`、通用 claim/成本与
额度服务、截图/对话/规划/复盘四类接单及 worker，以及
`web/src/components/AdminAISettings.vue` 和对应测试。

已实现：

- 每项 AI 能力保留一个已发布主配置，并可设置最多两个同能力、最近测试成功且价格完整的备用
  配置。后台支持保存、重排、清空、把备用提升为主，以及基于 `routing_version` 的并发冲突恢复；
  Key 不回显，备用中的不可变版本不能重新测试。
- `AIJobConfigRoute` 在新作业接单时冻结每个 attempt 的实际配置。无备用展开为
  `[主, 主, 主]`，一个备用为 `[主, 备用 1, 备用 1]`，两个备用为
  `[主, 备用 1, 备用 2]`；首个 attempt 开始后数据库触发器阻止修改。
- 只有网络、429、5xx 和可重试断流进入下一条路由；401/403、端点策略、解密、结构、空输出和
  业务校验错误直接终止。各 attempt 共享原 `AIJob.deadline_at`，对话重试继续使用 generation
  reset 隔离旧片段。
- provider cost 通过 `(job_id, attempt_number)` 读取实际路由配置和冻结价格。用户接单预留取
  整条主备路由不同配置估算中的最大值，成功只按最终成功 attempt 结算，失败尝试仍由平台承担。
- 后台“调用顺序”显示主服务及两个备用选择，配置列表显示“主服务/备用 1/备用 2”标签。版本
  冲突后重读服务端路由但保留管理员当前草稿，可基于最新版再次保存。

2026-10-04 在 macOS、本地 PostgreSQL 18.6、真实 Uvicorn/Mailpit、受控 OpenAI 兼容 HTTP
上游、真实 AI worker、生产构建预览和 Playwright Chromium 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| 主备路由受影响服务组合 | **64 passed in 115.42s** | 迁移、管理并发、claim、成本、额度及四类 worker |
| `cd server && uv run pytest -q` | **514 passed in 433.09s** | 服务端真实 PostgreSQL/HTTP 全量回归 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv build --no-sources` | 通过 | sdist 与 wheel |
| `cd server && uv run alembic current`、`check` | `0033_ai_fallback_routes (head)`，无模型漂移 | 开发库迁移与 ORM 一致性 |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| 主备管理与真实切换专项 Chromium | **3 passed in 32.9s** | 完整流程 × desktop、mobile、tablet |
| 管理与对话受影响 Chromium | **30 passed in 2.1m** | 两个完整规格 × 三视口 |
| `cd web && pnpm test:e2e` 首轮 | **228 passed, 2 skipped, 1 failed in 13.2m** | 唯一失败为既有 tablet 订阅详情短暂未刷新 |
| 失败用例空载复跑 | **1 passed in 18.9s** | `calendar-subscriptions` 本地覆盖流程 |
| `cd web && pnpm test:e2e` 完整复跑 | **229 passed, 2 skipped** | 231 项收集；最终状态 `passed`、无失败 ID |

真实浏览器流程先创建、合成测试并发布 conversation 主配置，再创建两个备用，保存并调换顺序，
把备用提升为主，并模拟另一管理员更新路由以验证 409 后保留草稿。随后真实注册用户并发送
“备用切换测试”：主模型返回 429，第二次尝试由冻结备用模型成功回复“备用服务已接管并完成
回复。”；后台成本页显示主模型 attempt 1“等待重试”和备用模型 attempt 2“成功”。

desktop、mobile、tablet 均检查页面标题、非空内容、目标交互、axe、控制台、API 500、Vite
错误层和横向溢出；desktop 另在 320×700 深色最大字号下复核后台。截图位于：

- `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-admin-management-chat-fallback-route.png`
- `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-admin-management-fallback-user-response.png`
- `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-admin-management-fallback-provider-costs.png`
- `/tmp/itime-ui-evidence/desktop-admin-management-chat-fallback-route-320-large-dark.png`

截图已人工检查，无空白、重叠、裁切或横向溢出。本会话未提供 Browser 插件，故按前端测试规范
使用仓库 Playwright。首轮全量唯一失败时，订阅列表已显示完成和 1 条事件，但详情短暂保留空态；
相同 tablet 用例空载复跑及第二次完整全量均通过，未修改无关订阅实现。

收口时开发 API 与 AI worker 已按当前代码重启，`GET /api/ready` 返回 200，OpenAPI 包含
`/api/v1/admin/ai-configs/routing/{capability}`，Vite `/admin/ai` 可访问，`itime_test_%`
数据库残留为 0。AI 备用服务路由首版范围已完成；本模块收口时，正式第三方供应商质量、
数据处理地区、Edge/实体设备、M8 性能/恢复和生产部署尚未完成或验证。

## M6-H：日历系列与 ICS 订阅源 30 天生命周期

对应 Git 提交：`29f760e`、`2ba1380`、`24f742f`、`036da5e`、`4f7fd34`、`08b72a4`、
`f7eeacb`、`e5b6ca0`、`d0f0d53`、`2f7e033`、`e85633b`、`fff4e7f`。专项设计见
[日历生命周期设计](../设计/iTime-日历生命周期设计.md)，实际代码包括迁移
`0034_calendar_lifecycle`、`server/src/itime/calendar/lifecycle_service.py`、
`cleanup_worker.py`、系列/订阅路由与投影过滤、同步和本人导出，以及
`web/src/components/CalendarTrashDialog.vue`、日历详情/订阅管理入口和
`web/e2e/calendar-lifecycle.spec.ts`。

已实现：

- 手工、XLSX/ICS 文件和截图确认形成的 `CalendarSeries`，以及 ICS `CalendarSource`，均使用
  `active / trashed / purged` 聚合生命周期。删除期限固定为服务端删除时刻后 30×24 小时；
  默认列表、occurrence、冲突、规划输入、复盘和 AI 上下文立即排除回收对象。
- 删除与恢复使用本人权限、CSRF、`operation_id`、请求摘要和乐观版本；重复删除不延长期限，
  同操作号异内容拒绝，活动对象重复恢复冲突，已清理对象返回 410。回收对象的编辑、拆分、
  例外、补课、刷新和字段覆盖入口统一阻断。
- 删除系列保留原 ID、重复规则、学期关系、例外和补课，恢复后重新投影。订阅暂停与删除正交：
  删除先推进 `fetch_revision`、使租约/刷新结果失效并隐藏来源；恢复活动订阅重新排队，恢复
  暂停订阅仍保持暂停。恢复造成当前计划重叠只创建复核提示，不修改计划版本或计划块。
- `itime.calendar.cleanup_worker` 每类每批最多 100 条，使用 PostgreSQL
  `FOR UPDATE SKIP LOCKED`。系列清理脱敏名称、说明、地点、时间、重复和来源并删除直接例外/
  补课；订阅清理删除事件、覆盖、单次例外和刷新明细，清空加密 URL、主机、验证器、摘要及
  错误正文；两类都先清除旧操作响应并保留最小墓碑和无正文 `purged` 同步事件。
- 本人完整 JSON 包含尚可恢复对象及生命周期字段；分类 CSV/Markdown 使用独立回收站分类，
  ICS 只导出活动安排，已清理墓碑不带正文。账号注销仍优先于单项 worker，既有计划、执行和
  复盘快照保持不可变。
- `/calendar` 的活动系列详情提供明确删除确认；订阅管理分开“暂停”和“移到回收站”；统一
  “日历回收站”按安排/订阅展示只读详情、到期时间和恢复入口。跨标签同步可刷新已打开的
  回收站及恢复后的日历，网络失败、未知提交结果和版本冲突均保留可恢复状态。

2026-10-04 在 macOS、本地 PostgreSQL 18、真实 Uvicorn/Mailpit、受控 ICS HTTP 来源、真实
订阅/日历清理 worker、生产构建预览和 Playwright Chromium 中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| 日历生命周期及受影响服务组合 | **103 passed** | 迁移、系列/订阅 API、投影、清理、规划、复盘、同步、导出与注销 |
| `cd server && uv run ruff check src tests` | 通过 | 全部服务端源码与测试静态检查 |
| `cd server && uv run pytest -q` | **532 passed in 455.28s** | 服务端真实 PostgreSQL/HTTP 全量回归 |
| `cd server && uv build` | 通过 | `itime_server-0.1.0.tar.gz` 与 `itime_server-0.1.0-py3-none-any.whl` |
| `cd server && uv run alembic upgrade head/current/check` | `0034_calendar_lifecycle (head)`，无新升级操作 | 开发库迁移与 ORM 一致性 |
| `cd server && uv run python -m itime.calendar.cleanup_worker --once` | 通过 | 独立清理入口可执行 |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| 日历生命周期及受影响 Chromium | **86 passed, 4 skipped** | 日历、导入、订阅、规划、导出、同步三视口与 desktop 深度场景 |
| `cd web && pnpm exec playwright test` 首轮 | **248 passed, 6 skipped, 1 failed** | 唯一失败为订阅刷新过快时选中详情保留旧事件 |
| 竞态修复后精确顺序复测 | **11 passed** | desktop 日历生命周期后紧接订阅完整流程 |
| `cd web && pnpm exec playwright test` 最终复跑 | **249 passed, 6 skipped in 14.3m** | 255 项收集；6 项为非 desktop 明确跳过的真实 worker、慢上游或深度公开链接场景 |
| `cd web && pnpm exec playwright test e2e/public.spec.ts` | **9 passed in 18.4s** | 生命周期公开说明更新后的 desktop/mobile/tablet 回归及 320px 隐私页 |

首轮全量测试中，刷新可在前端轮询首次观察到 `queued/running` 前完成，列表 generation 已前进，
但选中详情仍保留旧事件。`CalendarSubscriptionManager.pollSubscriptions` 现同时比较选中来源的
前后 generation；需要重读详情时在异步返回后再次核对 `pollRevision`，防止旧轮询覆盖新选择。
修复先按原失败顺序通过 11 项复测，再通过完整 255 项收集，不以空载单用例代替全量证据。

真实浏览器覆盖直接删除安排、暂停与删除订阅的差异、活动/暂停订阅恢复、网络失败、未知响应、
版本冲突、恢复后的计划重叠、跨标签刷新、真实到期清理、敏感正文从 API/UI/导出消失、未到期
对象保留、第二浏览器上下文及慢 ICS 迟到结果拒绝。desktop、mobile、tablet 均检查 axe、
控制台、Vite 错误层和根级横向溢出；desktop 另执行 320×700 深色最大字号。截图包括：

- `/tmp/itime-ui-evidence/{mobile,tablet}-calendar-lifecycle-trash.png`
- `/tmp/itime-ui-evidence/desktop-calendar-lifecycle-trash-320-dark-large.png`
- `/tmp/itime-ui-evidence/desktop-calendar-lifecycle-{direct-delete,purged}.png`
- `/tmp/itime-ui-evidence/desktop-calendar-lifecycle-subscription-{direct-delete,restored}.png`
- `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-public-privacy.png`
- `/tmp/itime-ui-evidence/desktop-public-privacy-320-large-font.png`

截图已人工检查，无空白、重叠、裁切、按钮移位或横向溢出。本会话未暴露 Browser 插件，因此
按前端测试规范使用仓库 Playwright 完成渲染、交互和截图验证。

收口时开发 API、`itime.calendar.subscription_worker`、`itime.calendar.cleanup_worker` 和 Vite
已按当前代码重启；`GET /api/ready` 返回 200，OpenAPI 包含系列与订阅恢复路由，
`http://localhost:5173/calendar` 返回 200，`itime_test_%` 数据库及测试进程/临时导出残留为
0。日历生命周期首版范围已完成；学期、节次、项目、AI 会话等其他独立对象的回收、困难/任务
语音与 AI 校对、正式第三方订阅兼容性、真实 Edge/实体设备、M8 性能与备份恢复删除重放、生产
部署仍未完成或验证。

## M6-I：任务名称与困难正文语音转写

对应 Git 提交：`3e20ff7`、`d14bdb5`、`5c90161`、`4810ab1`、`e040986`、`cb076cd`、
`c4bc9d8`、`96e9554`、`f489e00`、`ef6f94c`、`f25ff27`、`d3b0c7b`、`3e3768b`。专项设计见
[任务与困难语音转写设计](../设计/iTime-任务与困难语音转写设计.md)，实际代码包括迁移
`0035_voice_transcriptions` 与 `0036_transcription_quota_usage`、
`server/src/itime/ai/{audio_validation,transcription_router,transcription_service,transcription_provider}.py`、
通用 AI worker/额度/成本/治理接入，及
`web/src/components/VoiceTranscriptionControl.vue`、
`web/src/lib/{audio-recorder,pcm-wav,voice-transcriptions}.ts`。

已实现：

- 浏览器只在用户点击后请求麦克风，通过 AudioWorklet 连续混合声道并下采样为 16 kHz
  单声道 PCM16；主线程仅组装规范 44 字节 WAV 头。录制最短 250 ms、最长 60 秒、最大 2 MiB，
  60 秒自动停止；组件卸载、取消及设备中断会断开节点并停止全部媒体轨道，音频不写
  localStorage、IndexedDB 或同步队列。
- `POST /api/v1/voice-transcriptions` 在事务外校验和加密音频，再以本人 operation ID 幂等接单；
  同一账号最多 3 个活动作业、10 分钟最多 20 次，部署级队列默认 500。查询按本人和 purpose
  隔离，页面刷新或第二页面可恢复最近未解决结果。
- 独立 `transcription` 配置使用 OpenAI 兼容 `/audio/transcriptions` multipart 调用，
  支持合成 WAV 测试、发布、回滚和最多两个备用。429/503/网络/超时可切换，401/403、配置及
  输出错误终止；作业租约和终态门禁拒绝取消后的晚到结果。
- 音频成本按服务端实测 `duration_ms` 和冻结的每分钟价格向上取整。接单按主备最高价格预留，
  成功只结算最终成功 attempt；失败和取消释放用户预留，已经发出的失败尝试仍记录平台成本。
- 转写文字只进入独立可编辑草稿。任务名称和困难正文均由用户明确替换或追加，之后仍须使用原
  业务保存动作；转写不会推断日期、项目、分类、情绪或自动创建业务对象。归档/回收站任务及
  困难回收站不显示录音入口。
- 应用或丢弃立即清除录音；失败、超时和取消终态清除无恢复价值的音频；未解决录音和临时文字
  最迟 24 小时清理。本人导出仅包含未过期文字和安全元数据，账号注销优先取消作业并清理媒体。
  技术正文日志和可关闭质量样本可保留规范化文字，但不保存音频字节。

2026-10-04 在 macOS、本地 PostgreSQL 18、真实 Uvicorn、AI worker、受控 OpenAI 兼容
multipart 上游、Chromium 假麦克风和生产构建预览中执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| 语音转写服务专项组合 | **46 passed in 41.79s** | WAV、迁移、接单、worker、主备、成本、解决/清理与注销 |
| 合成探针与配置定向复测 | **20 passed, 11 deselected in 9.52s** | 规范探针 WAV、转写配置创建/测试/发布 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv run pytest -q` | **581 passed in 494.43s** | 当前服务端真实 PostgreSQL/HTTP 全量回归 |
| `cd server && uv build --no-sources` | 通过 | sdist 与 wheel |
| `cd server && uv run alembic upgrade head/current/check` | `0036_transcription_quota_usage (head)`；无模型漂移 | 开发库迁移 |
| `cd server && uv run python -m itime.ai.worker --once` | 通过 | 无待处理作业时正常退出并执行清理入口 |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| 语音转写专项 Chromium | **22 passed, 14 skipped in 1.7m** | 三视口主流程与 desktop 故障/恢复/60 秒/320px 深度场景 |
| `cd web && pnpm exec playwright test --reporter=dot` | **274 passed, 20 skipped in 16.2m** | 294 项收集；最终状态 `passed` |

真实浏览器覆盖任务和困难录音、编辑草稿、替换/追加、原字段并行编辑、业务保存失败保留文字、
权限拒绝、重复录音、60 秒自动停止、组件卸载停止 track、同 operation ID 上传重放、第二页面
恢复、处理中取消和晚到结果拒绝。desktop、mobile、tablet 均执行任务/困难主流程；desktop
另执行故障矩阵与 320px 最大字号深色检查。各视口核对 axe、控制台、Vite 错误层和横向溢出。
截图位于：

- `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-voice-transcription-task-ready.png`
- `/tmp/itime-ui-evidence/desktop-voice-transcription-task-ready-320-large-dark.png`
- `/tmp/itime-ui-evidence/desktop-admin-management-transcription-config-320-large-dark.png`

截图已人工检查，无空白、重叠、裁切或按钮文字溢出。Browser 插件技能可用但当前运行时未暴露
浏览器工具，因此按前端测试规范使用仓库 Playwright 完成渲染、交互、控制台和截图验证。

收口时开发库为 `0036_transcription_quota_usage (head)` 且 `alembic check` 无模型漂移，
一次性 AI worker 正常退出，`GET /api/ready` 返回 200，OpenAPI 包含三个语音转写路由，
`http://localhost:5173/tasks` 返回 200，`itime_test_%` 数据库
残留为 0；开发 API、AI worker、订阅/日历清理、公开链接、数据导出 worker 和 Vite 已按当前
代码运行。任务/困难纯语音转写首版范围已完成；本模块收口时，多任务/属性语音解析、正式
第三方模型质量、真实 Edge/实体设备、M8 性能与备份恢复、生产部署尚未完成或验证。

## M7-D：完整 Web 用户闭环与真实 Chrome/Edge 兼容

对应 Git 提交：`dcc6891`、`56854a4`、`f8181b0`、`ad8dcf0`、`73586ab`、`23e4bee`、
`1778c53`、`952c567`、`8cca2fd`、`406d31f`。实施计划见
[M7 完整 Web 闭环实施计划](../计划/iTime-M7完整Web闭环实施计划.md)，核心场景位于
`web/e2e/full-user-journey.spec.ts`；`web/playwright.config.ts` 保留默认仓库 Chromium，
并支持显式选择本机浏览器 channel 或 executable path。

已实现和验证的单一用户旅程：

- 主端通过可见 UI 注册账号、读取真实 Mailpit 验证邮件并登录，设置 Asia/Shanghai 时区和
  04:00 活动日；随后创建项目、60 分钟任务及两个步骤。
- 第二个独立 BrowserContext 不复制主端 Cookie，重新登录同一账号并打开任务详情。主端生成
  AI 日规划预览，确认后才写入第 1 版计划，并从统一日历的计划块进入专注。
- 主端勾选步骤、记录一条困难并结束专注；页面先明确显示任务未完成，只有用户点击“完成任务”
  后才写入完成事实。第二端在 5 秒上界内刷新到完成状态。
- 主端复盘核对 1 个计划块、1 个完成任务、1 个专注条目和 1 条困难，补充事实后跳过追问并生成
  第 1 版报告；第二端随后读取同一报告，并在统计页看到计划块、完成任务和专注会话各 1。
- 全流程不拦截核心业务请求或伪造成功响应；随机测试库、真实 Uvicorn、Mailpit、AI worker 和
  确定性 OpenAI 兼容上游共同提供服务端事实。每个视口检查 API 5xx、控制台异常、Vite 错误
  层、根级横向溢出和 axe。

完整场景在联调中发现并修复两个真实产品缺陷：

1. 第二端已经打开的干净任务详情不会采用远端完成状态。`TaskEditor` 现在只接受严格更新的
   服务端版本，同时保留本地脏草稿和离线投影版本；同步专项增加跨设备刷新回归。
2. 本地任务草稿存在时，远端更新提示在手机布局中不可见。冲突提示改由任务编辑器的可见成功
   状态区域承载，生命周期测试使用真实脏草稿复现并覆盖明确采用服务器版本。

此外，专注开放状态冲突测试曾让同一 BrowserContext 的后台同步抢先刷新，无法稳定制造目标
版本冲突。测试改为独立上下文，并在远端暂停完成前阻断主端同步，再重放本地结束操作；产品
行为未因稳定测试而放宽。该场景连续 10 次通过，三视口专注专项 24 项通过。

真实 Edge 全量回归继续发现并修复三类边界问题：订阅首次刷新在极窄竞态下可能只更新来源摘要
而保留空事件详情；订阅删除版本冲突后成功重读未清除旧错误；原生日期输入的程序化 `blur`
与统计夹具跨周一 04:00 边界均属于测试交互/时间基准假设。前两项已修复为完成态详情重读和
成功重读清除错误，后两项分别改用真实点击离焦和同一当前活动日夹具；相关 Edge 场景分别完成
18 次、30 次和 15 次重复验证。

2026-10-04 在 macOS、本地 PostgreSQL 18、Mailpit、隔离 Uvicorn/worker、生产构建预览及
以下浏览器中执行：

| 命令/环境 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv build --no-sources` | 通过 | sdist 与 wheel |
| `cd server && uv run pytest -q` | **581 passed in 501.89s** | 当前服务端真实 PostgreSQL/HTTP 全量回归 |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与 Vite 生产构建 |
| `pnpm exec playwright test e2e/full-user-journey.spec.ts` | **3 passed** | Chromium desktop、mobile、tablet 完整闭环 |
| `ITIME_E2E_BROWSER_CHANNEL=chrome ... --project=desktop` | **1 passed** | Google Chrome **149.0.7827.54** 核心闭环 |
| `ITIME_E2E_BROWSER_EXECUTABLE_PATH=...Microsoft Edge ... --project=desktop` | **1 passed** | Microsoft Edge **154.0.4258.53** 核心闭环 |
| 完整闭环、同步与生命周期组合 | **30 passed** | 跨设备刷新、脏草稿提示和核心旅程 |
| `cd web && pnpm test:e2e` | **277 passed, 20 skipped in 16.9m** | 297 项收集；三个 Chromium 视口全量回归 |
| `ITIME_E2E_BROWSER_EXECUTABLE_PATH=...Microsoft Edge ... pnpm test:e2e` | **277 passed, 20 skipped in 17.7m** | Microsoft Edge **154.0.4258.53**；297 项收集；desktop、mobile、tablet 全量回归 |

20 个跳过项均为测试内显式限定 desktop 的到期清理、慢上游、浏览器麦克风故障及 320px 深度
场景；相同业务的移动和平板主流程实际执行。完整闭环截图位于：

- `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-full-journey-review-complete.png`
- `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-full-journey-second-device-result.png`

六张本轮全量回归截图已人工检查，主端复盘和独立第二端结果均非空，电脑、手机、平板没有内容
重叠、裁切、按钮移位或根级横向溢出。Browser 插件未在当前环境提供，因此按前端测试规范使用
仓库 Playwright；真实 Chrome/Edge 结果不是用内置 Chromium 替代。Edge 全量运行的
`.last-run.json` 为 `passed`，失败证据目录为空。

收口时 `itime_test_%` 数据库数量和名称列表均为 0，没有孤立 Playwright、
`tests.web_environment` 或 4173 预览进程；`GET /api/ready` 与
`http://localhost:5173/tasks` 均返回 200。`E2E-01`、真实 Chrome desktop 核心流程和真实
Edge 三视口全部页面兼容验证已完成；学期、项目、AI 会话删除语义仍待产品确认且当前无删除
旁路。M7-D 收口时，实体设备、正式最低版本矩阵、正式第三方模型质量、其他业务对象提案、
M8 性能/容量/备份恢复及生产部署尚未完成，不能据此宣称 Web 首版已经发布。

## M8-A：固定资源性能与容量基线

对应 Git 提交从 `282c79c build: 添加性能测试运行镜像` 到
`2a9a1aa test: 隔离负载下浏览器用例`；每个运行边界、数据集、负载、门禁、采样、异步场景和
UI 验证均独立提交。实际代码为 `server/performance/`、`server/Dockerfile.performance`、
`compose.performance.yaml`、`server/tests/test_performance_*.py`、
`web/playwright.performance.config.ts` 和 `web/e2e/performance-under-load.spec.ts`。

### 参考环境与数据

2026-10-05 在以下本地隔离环境执行：

| 项目 | 实际值 |
| --- | --- |
| 主机 | macOS 26.6.2（25G83），arm64 |
| 容器运行时 | Colima 0.10.3；Docker Engine 29.5.2；Compose 5.3.1 |
| 被测资源 | 2 vCPU；4,094,382,080 bytes 内存；负载、协议桩、Vite 和浏览器在边界外 |
| 服务 | PostgreSQL 18.6；2 个 Uvicorn worker；2 个 AI worker；邮件/订阅/公开链接/导出各 1 个 worker |
| 数据库 | `max_connections=50`；API、worker、迁移/采样/维护理论连接预算 20；实测峰值 17 |
| 工具 | Python 3.13.14；uv 0.11.29；Locust 2.46.7；Node.js 24.18.0；pnpm 11.15.0；Playwright 1.63.0 |
| 浏览器 | Chrome for Testing 153.0.8010.12；Google Chrome 149.0.7827.54；Microsoft Edge 154.0.4258.53 |

标准数据集固定种子 `20261005`，创建 100 个账号，其中 50 个供负载使用，并包含 15,000 个任务、
29,400 个步骤、40,000 条对话消息、9,000 个专注会话及 150,000 条同步事件。重历史数据集使用
10 个互不复用的活动账号，并包含 10,000 个任务、19,600 个步骤、50,000 条对话消息、50,000
条订阅事件、3,650 个专注会话及 100,000 条同步事件。所有账号、凭据、上游和数据库均为本次
随机隔离运行创建，不访问开发业务数据或公网服务。

### 最终一键基线

`cd server && uv run python -m performance.run baseline` 在干净提交 `cef08e1` 依次执行八个场景；
每个 `summary.json` 均为 `passed: true`，没有 failure 或 warning。普通 Locust 聚合分位数包含
登录；饱和场景为 30 个独立一分钟窗口，表内采用 100 用户代表档各路由最大分位数；AI、队列和
维护使用各自结构化业务结果，不把不同事件强行合并为一个延迟。

| 场景 / run ID | 有效时长与负载 | 实际请求/吞吐 | P50/P95/P99 | CPU / 内存 / 连接 |
| --- | --- | --- | --- | --- |
| steady `c4d6f805` | 900 s，20 用户 | 9,991，11.115 req/s，0 失败 | 9/19/23 ms | 9.0% / 24.0% / 28% |
| peak `0065f99a` | 300 s，50 用户 | 8,326，27.830 req/s，0 失败 | 8/17/28 ms | 13.4% / 24.0% / 30% |
| history `d3ced98f` | 600 s，10 个独立重账号 | 2,738，4.567 req/s，0 失败 | 聚合 25/730/990 ms；日历路由 440/870/1,400 ms | 34.0% / 28.8% / 32% |
| saturation `51b71e51` | 10—100 用户，每档 3×60 s | 30 窗口共 54,962，0 失败 | 100 用户档路由最大 12/21/28 ms | 17.2% / 24.9% / 30% |
| ai-isolation `edb93be4` | 60 s 基线 + 300 s；20 普通用户 + 20 AI 作业 | 普通请求 3,342；AI 观测 120；均 0 失败 | 普通 10/20/23 ms；AI 聚合 240/2,800/3,300 ms | 9.4% / 25.1% / 30% |
| queues `30e69fb1` | 三类各至少观察 30 s；20 普通用户 | 普通请求 989，0 失败 | 各路由最大 15/24/72 ms | 11.8% / 24.9% / 34% |
| maintenance `15f4538b` | 60 s 基线 + 60 s 维护；20 用户 | 基线/维护请求 659/666，均 0 失败 | 维护窗路由最大 15/24/36 ms | 8.5% / 17.8% / 24% |
| soak `f644da29` | 3,600 s，20 用户 | 39,932，11.095 req/s，0 失败 | 10/19/23 ms | 9.1% / 24.2% / 32% |

容量阶梯的 10、20、30、40、50、60、70、80、90、100 用户档和全部 30 个窗口均完成；最低
50 用户门禁通过，100 用户为本次测试上限内最高稳定档，首个不稳定档和真实容量边界均未观察到。
因此只能陈述“至少通过 100 个带思考时间的本地并发用户”，不能将 100 写成系统极限或生产
容量承诺。

AI 隔离场景创建 20 个不同 owner 的作业，最终恰有 20 个成功作业、20 次成功尝试、20 条
供应商成本、20 个已结算预留和 20 条用量账本；queued/running/failed/cancelled/timed_out、
活动预留和不一致作业均为 0。数据库领取 P95 为 2,741.282 ms，端到端 P99 为 3,164.963 ms；
普通读/写 P95 相对无 AI 基线为 1.000/0.792 倍。该结果使用受控本机 OpenAI 兼容上游，只验证
队列、流、隔离、终态和金额，不证明正式模型质量或公网时延。

队列场景分别创建订阅、公开链接和导出积压各 50 条：排空时间为 13.537、13.339、4.093 秒，
吞吐为 3.694、3.749、12.216 jobs/s，全部成功且无遗留 queued/running/failed。维护场景在
普通请求持续运行时清理任务、日历系列、困难、记忆各 100 条及同步事件 1,000 条，总耗时
0.472 秒；普通读/写 P95 相对基线为 0.815/1.091 倍。60 分钟耐久内存增长为 0.603%，没有
持续 CPU 高位、连接池超时、5xx、超过 2 秒锁等待、队列积压或业务不变量异常。

所有最终场景的连接峰值不超过 17/50，采样最大间隔 1 秒，连接池超时、5xx 和超过 2 秒锁等待
均为 0。`pg_stat_statements` 只保存 query ID 和统计，不保存 SQL 正文；所有最终运行中最高
采样平均执行时间为 28.518 ms。重历史场景累计出现 310,472 个临时块写入，已作为 M8-C
生产候选环境复核 `work_mem`、索引和查询计划的观测项；本次重历史 HTTP 门禁仍通过。

### 负载下真实浏览器

`ui-load` 最终运行 `4402349a` 基于干净提交 `2a9a1aa`：20 个背景用户持续真实 HTTP 请求，
浏览器完成后提前结束，约 39 秒内共 452 个请求、0 失败；普通业务路由最大 P95/P99 为
20/30 ms，CPU 均值 11.5%、内存峰值 23.6%、连接峰值 28%。Browser 插件未在当前执行环境
暴露，因此按前端测试规范使用仓库 Playwright：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `pnpm test:e2e:performance` | **3 passed** | Chromium desktop、mobile、tablet；任务创建与跨端同步、日历、规划、专注、复盘、统计 |
| `ITIME_E2E_BROWSER_EXECUTABLE_PATH=.../Microsoft Edge pnpm exec playwright test --config playwright.performance.config.ts --project=desktop` | **1 passed** | 真实 Microsoft Edge 154 desktop 同一负载流程 |

四项浏览器流程均检查 axe、控制台、API 5xx、Vite 错误层、根级横向溢出和 5 秒跨端可见。
截图位于：

- `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-performance-under-load.png`
- `/tmp/itime-ui-evidence/desktop-edge-performance-under-load.png`

四张截图已人工检查，页面非空，任务、日历、计划、专注、复盘、统计和同步状态无重叠、裁切或
异常位移。视口验证不替代实体手机或平板。

### 失败、修复与全量回归

实测失败没有通过放宽门禁、缩小数据或跳过请求解决：

- history `44e0ebee` 的日历查询 476/476 返回 422，原因是长窗口展开超过 API 的 2,000
  occurrence 安全上限；`5402c92` 将负载改为仍覆盖重历史的七天窗口，最终 `d3ced98f` 通过。
- history `867aa417` 暴露首版夹具实际只有 5 个活动重账号，无法满足 10 用户不复用合同；
  `bc4639a` 为 10 个重历史账号生成独立数据，随后完整复跑。
- saturation `de34ed9a` 因 Docker 返回科学计数法单位 `1e+03kB` 导致采样器退出；
  `ae564a3` 增加解析回归，最终 `51b71e51` 完成全部 30 个窗口。
- `6574272` 固定跨午夜日历测试日期，`065bb55` 固定性能 worker 时间基准，`2a9a1aa` 隔离
  默认与性能 Playwright 产物；这些修复均保持业务门禁不变。

最终验证：

| 命令 | 实际结果 |
| --- | --- |
| `cd server && uv run pytest -q tests/test_performance_*.py` | **89 passed, 2 skipped** |
| `cd server && uv run ruff check .` | 通过 |
| `cd server && uv build --no-sources` | 通过 |
| `cd server && uv run alembic current` / `uv run alembic check` | `0036_transcription_quota_usage (head)`；无模型漂移 |
| `cd server && uv run pytest -q` | **670 passed, 2 skipped in 584.94s** |
| `cd web && pnpm build` | 通过 |
| `cd web && pnpm test:e2e` | **277 passed, 20 skipped in 16.9m**；Chromium 三视口 |

测试结束后 `uv run python -m performance.run verify-clean` 返回
`{"remaining_resources":[]}`；开发 PostgreSQL 中 `itime_perf_%`、`itime_test_%` 均为 0，
性能 context 中 `itime-perf-*` 容器、卷和网络均为 0。独立进程扫描随后发现并终止了一个已脱离
精确运行清单的 `performance.upstream` 进程；再次执行运行清单、数据库、Docker 和进程检查后，
Locust、性能 Vite、协议桩及其他性能资源均为 0。`verify-clean` 继续只核对带精确 run ID 的清单，
避免跨 checkout 扫描或清理同名进程。

M8-A 完成 `PERF-01` 和受控本地范围的 `PERF-02`，并为 `NFR-PERF-01/NFR-CAP-01` 提供本地
参考数据。M8-B 每日备份、RPO/RTO、24 小时恢复和删除台账重放，M8-C 生产候选网络/资源、
真实邮件与 AI 供应商、月度成本、告警、域名/TLS、隐私披露、发布与回滚，以及正式模型质量和
实体设备仍未完成；不能据此宣称 Web 首版已经发布。

## M7-E：AI 课表统一导入与 Agent 闭环

对应实施提交 `0da01a9`、`542d10f` 及本节收口提交，实施计划见
[AI 课表统一导入实施计划](../计划/iTime-AI课表统一导入实施计划.md)。本模块将日历入口与
AI 会话附件统一到同一个 `CalendarImportBatch`、AI 作业、候选校对和确认事务，不建立第二套
仅供演示的数据链。

实际完成范围：

- 日历“导入日历”使用 Element Plus 分段控件统一文件、图片、受控网址、文字和 ICS 订阅；
  XLSX/ICS 直接解析，图片、网页正文和文字经服务端 AI 结构化，全部进入同一候选编辑器。
- AI 会话支持图片、XLSX、ICS 附件，以及自然语言和公开网址 Agent 工具；消息卡可确认、调整、
  取消或跳转日历，AI 结果不能绕过候选校对和用户确认。
- 作业可离页继续、轮询、取消和从服务端恢复；重复、无效、需校对、冲突及部分确认状态保持
  可见。公开网址继续使用受限读取，不携带浏览器 Cookie 或平台凭据。
- 数据库约束修订为 `0041_ai_import_source_check`，明确拒绝无媒体且缺少
  `source_type` 的截图能力作业，并修复 PostgreSQL `CHECK` 中 `NULL` 绕过。
- 真实模型回归暴露并修复 Agent 忙碌日历中的 Python `date/time` 无法 JSON 序列化问题；
  上下文现在使用 ISO 8601。首屏会话新建同时等待认证和 CSRF，避免返回用户首次点击竞态。

2026-10-08 在 macOS、本地 PostgreSQL 18、真实 Uvicorn/AI worker、Vite、DeepSeek 和
Playwright 中执行：

| 命令/环境 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd server && uv run pytest -q` | **776 passed, 6 skipped in 822.95s** | 当前服务端 PostgreSQL/HTTP/worker 全量；跳过性能与真实恢复专项 |
| `cd server && uv run ruff check .` | 通过 | 全部 Python 静态检查 |
| `cd server && uv build --no-sources` | 通过 | sdist 与 wheel |
| `uv run alembic current` / `uv run alembic check` | `0041_ai_import_source_check (head)`；无模型漂移 | 当前开发库与模型 |
| `cd web && pnpm build` | 通过 | Vue TypeScript 与 Vite 生产构建 |
| Chromium 普通全量 | **295 passed, 20 skipped in 16.9m** | 315 项收集；desktop、mobile、tablet |
| 导入/规划/隐私三视口增量 | **27 passed in 2.7m** | 订阅、规划冲突和隐私失败恢复 |
| AI 会话三视口增量 | **15 passed in 1.5m** | 多会话、提案、恢复、取消与认证首屏 |
| `pnpm test:e2e:deepseek` | **1 passed in 19.3s** | 真实 DeepSeek desktop：对话、规划、图片/文字课表、确认和复盘 |
| Microsoft Edge 154.0.4258.62 关键路径 | **24 passed in 2.1m** | 会话课表、日历五类导入、完整用户闭环 × 三视口 |

普通全量将 `deepseek-live.spec.ts`、`recovery-after-restore.spec.ts` 与性能场景从默认集合分离，
分别由显式配置和运行环境执行，避免缺少真实账号或恢复夹具时产生伪失败。20 个普通全量跳过项
仍是用例内限定 desktop 的到期清理、慢上游、麦克风故障和 320px 深度场景；移动和平板主流程
实际执行。

收口期间完整回归还修正了订阅 worker 完成后详情重读、窄屏来源自动同步、规划版本冲突等待、
隐私失败后对账、额度结算等待、复盘取消夹具、任务冲突定位器和可恢复 503 删除重试。这些修改
没有放宽业务断言；最终 Chromium 全量、当前后端全量、真实 DeepSeek 和真实 Edge 关键路径均
独立通过。

Browser 插件在本次环境未提供，因此按前端测试规范使用仓库 Playwright。Edge channel 默认只
检查 `/Applications`，本机 Edge 实际位于用户应用目录，最终使用
`ITIME_E2E_BROWSER_EXECUTABLE_PATH` 指向真实浏览器，不以内置 Chromium 替代。

本模块当前没有未解决的功能阻断；系统化正式模型质量评测、实体设备、M8-B 恢复及 M8-C
生产候选验证仍是独立交付项，不能由本次真实 DeepSeek 单场景或本地浏览器结果替代。

## M7-F：Chat-first 界面重构与视觉门禁

对应提交 `a65327c`、`3566022`、`096cb8d`、`0257cf6`、`95657ef`，设计与实施计划见
[Chat-first 界面重构设计](../设计/iTime-ChatFirst界面重构设计.md)和
[Chat-first 界面重构实施计划](../计划/iTime-ChatFirst界面重构实施计划.md)。

实际完成范围：

- 登录后 `/ai` 直接进入 Chat-first 工作区。桌面为 64px 产品窄栏、会话侧栏和消息主区；
  768px 平板及手机把会话历史、产品入口和账号操作收进抽屉。
- 一级产品入口收敛为 AI、任务、日历、专注，规划、复盘、统计位于“更多功能”；设置、帮助和
  退出位于账号区域。跨页面 E2E 使用统一语义导航助手，不再依赖旧 `menuitem` 结构。
- assistant 正文经 `markdown-it` 和 DOMPurify 清理后渲染标题、列表、表格、引用、链接和
  代码块；user 正文保持纯文本，不从 Markdown 推断或执行业务操作。
- 消息视口和 composer 使用独立 grid 行；滚动跟随、读取更早消息、回到最新、八行输入、附件、
  窄高度、取消、失败重试、任务提案和课表导入卡均保持可达且互不覆盖。
- `/settings` 合并账号安全、偏好、AI 与隐私、数据与同步和账号操作。密码使用 Dialog，设备、
  额度、同步和数据使用 Drawer；旧 `/account/*` 地址继续重定向到对应设置区段。

2026-10-09 在 macOS、生产构建预览、隔离 PostgreSQL/API/Mailpit/worker、Playwright
Chromium 和真实 Microsoft Edge 中执行：

| 命令/环境 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd web && pnpm build` | 通过 | Vue TypeScript 与 Vite 生产构建 |
| Chromium Chat-first 与设置专项 | **19 passed, 2 skipped in 2.8m** | desktop、mobile、tablet；同步冲突仅在 desktop 生成一次 |
| 修复后移动/平板受影响专项 | **14 passed in 1.6m** | 后台用户流、抽屉导航、离页恢复、复盘和统计 |
| `cd web && pnpm test:e2e` | **317 passed, 22 skipped in 20.7m** | 339 项收集；三个 Chromium 视口普通全量 |
| Microsoft Edge 154.0.4258.62 关键路径 | **28 passed, 2 skipped in 3.4m** | Chat-first、统一设置和 AI 课表导入 × 三视口 |

22 个普通全量 skip 均为用例内既有的 desktop-only 到期清理、慢上游、麦克风故障、320px 深度
场景或桌面固定证据在 mobile/tablet 不重复执行；三个视口的主业务流程均实际运行。Edge 的两个
skip 仅为同步冲突证据在手机和平板不重复生成，不能解释为 Edge 手机或平板未运行。

视觉专项创建 20 个包含中文、英文、超长文本和无空格长串的会话，并向目标会话写入 30 条消息。
几何断言直接检查会话标题可见宽度、标题与操作按钮边界、消息视口与 composer 边界、最后消息
与 composer 的 16px 间距、八行和附件后的完整高度、根级横向溢出及意外 `overflow:hidden`
裁切。运行同时监听控制台 warning/error、页面异常、API 5xx、Vite overlay，并执行 Axe。

最终人工查看的主要证据位于：

- `/tmp/itime-ui-evidence/desktop-chat-first-{normal,markdown,proposals,timetable,failure}.png`
- `/tmp/itime-ui-evidence/desktop-chat-first-320-large-attachment.png`
- `/tmp/itime-ui-evidence/mobile-chat-first-{normal,proposals,timetable}.png`
- `/tmp/itime-ui-evidence/tablet-chat-first-drawer.png`
- `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-settings-normal.png`
- `/tmp/itime-ui-evidence/desktop-settings-sync-conflict.png`
- `/tmp/itime-ui-evidence/mobile-settings-danger.png`

逐张检查标题、导航层级、消息排版、Markdown 表格/代码、不可信 HTML 文本、提案和课表卡、
composer、附件、按钮换行、抽屉、同步冲突及注销危险区。最终 Chromium 与 Edge 截图均未发现
仍可修复的遮挡、裁切、重叠、根级横向滚动或异常位移。

验证过程中发现并修复的真实问题没有通过放宽断言解决：

- 长会话标题原为固定单行，窄屏会与菜单争用空间；改为任意断行，标题栏高度随内容增长。
- 消息滚动区缺少键盘焦点和名称；增加 `tabindex` 与“消息历史”无障碍名称。
- 320px 最大字号下八行输入最初未完整展开；修正 composer 几何后逐行检查 1—8 行可见。
- 会话范围选中态在 Element Plus 过渡中被 Axe 读到 `4.16:1`；固定品牌前景/背景并取消颜色
  过渡后达到 WCAG AA。
- 旧测试把新产品链接当作 `menuitem`，且平板抽屉打开时可能命中被遮罩的桌面轨道；统一导航
  助手优先限定到可见抽屉，再选择主入口或“更多功能”，完整回归通过。

Browser 插件未在当前执行环境暴露，因此按前端测试规范使用仓库 Playwright 和 `view_image`
逐张审查。实体手机/平板软键盘、Safari、Firefox、系统读屏器和触控实体设备仍未执行；本次短
视口聚焦与滚动恢复只证明浏览器模拟合同，不能替代实体设备。M8-B、M8-C、系统化正式模型质量
和生产发布状态没有因本模块改变。

## M7-I：全站统一 AppShell

对应提交为本节交付的 `feat`/`test`/`docs` 三次切片，与 M7-F 共用
[Chat-first 界面重构设计](../设计/iTime-ChatFirst界面重构设计.md)的「全站统一 AppShell」节。

本次范围：将 AI、任务、日历、规划、专注、复盘、统计和设置八页统一到同一个 `AppShell`
壳。桌面保持 64px 产品窄栏；需要次级侧栏（会话、项目、学年、规划、专注视图、复盘视图、
统计视图）时，壳固定渲染 260px 的 `secondary` 槽；无次级侧栏的页面（设置）直接留给 main
使用。小于 960px 时所有页统一塌陷为顶部栏加 Element Plus Drawer，抽屉内渲染主导航、次级
槽（如会话/任务项目/专注视图）和账号区域，不再出现 AI 窄栏、其它页宽栏的割裂。设置页顶部
保留“返回 AI 对话”返回链接，确保用户随时回到首页。

实际完成范围：

- 新建 `web/src/shell/AppShell.vue`、`web/src/shell/useAppShell.ts`、
  `web/src/shell/app-shell.css`，集中维护 grid 几何、Drawer 行为、`AppRail` 与账号退出。
- `AIPage`、`TasksPage`、`CalendarPage`、`PlansPage`、`FocusPage`、`ReviewsPage`、
  `StatisticsPage`、`SettingsPage` 全部改为通过 `AppShell` 外层渲染，页内保留业务主区并
  通过 `secondary`/`drawer-secondary` 具名插槽注入次级侧栏；专注页新增
  `FocusSecondaryNav.vue` 作为统一次级入口。
- 旧 `TaskWorkspaceNavigation`、`CalendarNavigation`、`PlanNavigation`、`ReviewNavigation`、
  `StatisticsNavigation`、`ConversationHeader` 瘦身为 `secondary-nav` 模式，共用一套
  侧栏行高、Hover、Selected、aria 规则，不再各自造轮。
- `AppRail` 使用 Element Plus `ElDropdown` 承载“更多功能”，`AccountMenu` 使用
  `ElPopover`；一级入口保持 AI/任务/日历/专注。

2026-10-09 在 macOS、生产构建预览、隔离 PostgreSQL/API/Mailpit/worker、仓库 Playwright
Chromium 中执行：

| 命令/环境 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd web && pnpm build` | **通过 (3.17s)** | Vue TypeScript 与 Vite 生产构建 |
| UX-07 全站 AppShell × 三视口 | **3 passed in 71s** | desktop/mobile/tablet 覆盖 8 页 |
| Chat-first 视觉 + 统一设置 + UX-07 全视口 | **22 passed, 2 skipped in 3.0m** | 8 条用例 × 3 视口 |
| 核心页回归 full-user-journey + 任务 + 规划 + 日历 + 专注 + 复盘 + 统计 | **26 passed in 1.6m** | 桌面 Chromium |

UX-07 用例对 `/ai`、`/tasks`、`/calendar`、`/plans`、`/focus`、`/reviews`、`/stats`、
`/settings` 八页执行相同的壳断言：桌面断言 `aside.app-rail` 可见；mobile/tablet 断言顶部
“打开导航”按钮可见、点击后打开 `aria-label="导航"` 抽屉再按 ESC 回收。每页同步执行 Axe
零违规、`pageerror`、Console error/warning、API 5xx 零错误监听，并把 `/tmp/itime-ui-evidence/
{viewport}-ux-07-{path}.png` 的截图留作证据。因外壳在三视口下形状不同，Axe 规则沿用
Chat-first 的 `color-contrast` 豁免（Element Plus Dropdown 悬浮态过渡色，之前已通过真机核
实对比度，不属于本次改动），其余规则一视同仁。

视觉证据：

- `/tmp/itime-ui-evidence/desktop-ux-07--ai.png` …
  `/tmp/itime-ui-evidence/desktop-ux-07--settings.png`
- `/tmp/itime-ui-evidence/mobile-ux-07--ai.png` …
  `/tmp/itime-ui-evidence/mobile-ux-07--settings.png`
- `/tmp/itime-ui-evidence/tablet-ux-07--ai.png` …
  `/tmp/itime-ui-evidence/tablet-ux-07--settings.png`

逐张比对侧栏宽度、顶部栏存在性、抽屉打开/关闭及设置页的返回链接位置，三视口和八页展现同一
个 AppShell 外观。

验证过程中处理的真实问题：

- AI 页之前自带独立的三栏布局，与其它页壳几何不一致；统一改为 `AppShell` + `secondary`
  插槽后，桌面 64px/260px/主区在 8 页上完全一致，不再出现用户反馈的「AI 侧边栏小、其它
  页宽」现象。
- 设置页之前没有返回 AI 的入口，新增 `.settings-return-nav` 粘性返回条，桌面和手机均
  通过 Axe 验证并保留在截图中。
- `TaskWorkspaceNavigation` 等旧侧栏自带整栏背景、边框和内边距，与 AppShell 的
  `secondary` 槽会双重包裹；全部改为 `secondary-nav` 内容段落，几何由 AppShell 统一
  负责。

未覆盖范围与剩余风险：

- Microsoft Edge 真实浏览器在当前执行环境未暴露（`msedge` 不在 PATH，`Microsoft Edge.app`
  未找到）。已改以 Chromium `--channel=msedge` 之外的方式，但本次未重新采集 Edge 真机
  截图；Chat-first 节的 Edge 关键路径仍是最近一次有效证据。
- Safari、Firefox 跨浏览器与实体设备（手机/平板 + 真实软键盘 + 系统读屏）仍未执行。
- CSS 旧壳规则（`.task-workspace`、`.calendar-workspace`、`.plan-workspace`、
  `.review-workspace`、`.statistics-workspace`、`.focus-workspace` 等整栏规则）在
  `web/src/*.css` 中仍存在但没有 DOM 使用，属于技术债，暂保留以减少本次 Diff。
- M8-B 备份恢复、M8-C 生产发布、系统化正式模型质量仍未执行，不因本模块变动。

## M7-J：Chat-first 单栏导航与范围收敛（S1）

对应 2026-10-10 用户授权的[范围与 AI 融合重构 v2](../需求/iTime-范围与AI融合重构v2.md)
及 ADR-013。S1 阶段只收敛前端导航与入口，不动数据模型和服务端 CRUD；S2 阶段再承载 AI
function tools 与长期记忆（ADR-014），S3 阶段再承载日历分钟精度与事件执行状态。

### 本次范围

- `AppRail` 一级导航收敛为**单栏五入口**：新对话、日历、任务、规划、个人中心。顶部
  产品窄栏固定渲染这五个入口；下方滚动区继续承载 AI 会话历史，与 ChatGPT 的信息架构
  对齐。≤960px 继续塌陷为顶部"打开导航"按钮 + Element Plus Drawer，抽屉按同一顺序
  渲染。
- 旧路由回退：`/focus→/ai`（删除独立专注计时页入口）、`/stats→/me/stats`（统计并入
  个人中心）。`/me/:tab(stats|security|preferences|privacy|data|appearance|account)`
  由 `MePage` 统一承载，`/me/stats` 使用 ElSelect（day/week/month）切换，不走 URL
  query，也不走 `role=tab` 语义。
- 规划页改为**只读列表 + 展示**：保留查看、修改元信息、删除接口和 UI；"生成/重排"
  入口全部迁走，`PlansPage` Hero 改为 `RouterLink to="/ai?prompt=帮我生成本周规划"`，
  点击直接带 prompt 跳到 `/ai`。
- 复盘页改为**只读结果展示**：`ReviewsPage` 保留历史复盘摘要列表、详情、编辑/删除
  接口；Hero 入口 `RouterLink to="/ai?prompt=来复盘今天"`。独立复盘发起表单组件
  在本次 S1 前端层下线，服务端 `POST /api/v1/reviews:start` 作业保留以兼容 S2
  function tool 复用。
- Playwright 适配：删除 `focus.spec.ts`、`tasks.spec.ts` 中专注段断言、`voice-transcriptions.spec.ts`
  两个困难语音测试、`performance-under-load.spec.ts` 专注段、`full-user-journey.spec.ts`
  瘦身；`statistics.spec.ts` 重写为 `/me/stats` 两个 case；`planning.spec.ts`、
  `reviews.spec.ts`、`deepseek-live.spec.ts` 中 AI 生成/写报告断言 skip 并标 S2 pending。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `16d533e` | `feat(web): 收敛导航为 Chat-first 5 入口单栏` | AppRail、MePage、PlansPage/ReviewsPage Hero、路由回退 |
| `c686223` | `test(e2e): 调整导航与引导断言适配 Chat-first` | Playwright 用例适配，专注段用例作废，S2 pending 标记 |
| S1 commit 3 | `docs: 记录 Chat-first 导航与规划/复盘入口验证` | 测试方案 v0.3、本节、ADR-013 已在 commit `f39902f` 定稿 |

### 验证记录

- 2026-10-10 `cd web && pnpm exec vue-tsc --noEmit`：**0 error**。
- 2026-10-10 Playwright chromium 冒烟（本节 commit 3 执行结果待补）：
  `ux-07-app-shell`、`planning`、`reviews`、`tasks`、`full-user-journey`、
  `settings-page`、`performance-under-load` 的 chromium 项目。
- 2026-10-10 `cd web && pnpm build && pnpm exec playwright test tasks full-user-journey settings-page --project=desktop --reporter=line`：
  **9 passed**。修复 S1 单栏导航遗留三处：`tasks.spec.ts:55-60` 改走
  `navigateToProduct` 兜底匹配 menuitem；`settings-page.spec.ts:98` 把
  `getByText(address)` 范围限定在 `#account` 内绕开 AppShell 底部二次 email
  渲染；`full-user-journey.spec.ts:178+192` 把 `/完成任务/任务已完成/` 的旧文案
  改成 `标为完成/任务已明确标为完成/重新打开`，第二设备补「所有」状态过滤。
  同步修 `.task-list > li.active` 下 `.task-meta/.task-labels` 子元素颜色对比度
  不足（#64716a→var(--text)），Axe 恢复 0 violation。
- Edge/Chrome 真实桌面回归、Safari/Firefox、实体设备仍未执行。

### 剩余风险

- `server/src/itime/ai/provider.py`、`server/src/itime/review/job_service.py` 本工作区
  仍为未暂存改动，按 AGENTS.md 第 3 条和用户授权范围保留，不在 S1 内落盘。
- 规划/复盘/日历/任务/困难的 CRUD API 保留；S2 需要在此之上实现 function tools 并
  统一通过 AI 对话触发写入预览-确认。
- 独立专注计时组件 `FocusPage.vue`、`FocusSecondaryNav.vue` 仍在仓库中但入口关闭；
  是否移除代码留待 S2/S3 一次性清理，避免破坏既有数据迁移和审计链条。

## M7-K：S2-D1 长期记忆搜索与 ai_tool 来源扩展

对应 [AI 工具链与长期记忆](../需求/iTime-AI工具链与长期记忆.md) 第 4 节与 [ADR-014](../架构/iTime-ADR架构决策文档.md#adr-014ai-function-tools-与长期记忆)。S2 阶段先把长期记忆基础设施铺平，不动对话编排和工具注册；function tools 与写预览通道在 S2-D2/S2-D3 落。

### 本次范围

- 迁移 `0042_memory_ai_tool`：`memories` 加三列 `tags TEXT[]`（server default `ARRAY[]::text[]`，CHECK 卡在 10 个以内；每个 tag 长度在 Pydantic 层约束）、`last_used_at TIMESTAMPTZ`、`source_conversation_id UUID`；替换 `ck_memories_source_type` 允许 `ai_tool`；替换 `ck_memories_source` 允许 `(source_type='ai_tool' AND source_proposal_id IS NULL AND source_conversation_id IS NOT NULL)`；加 GIN 索引 `ix_memories_tags` 与条件索引 `ix_memories_owner_last_used`（`WHERE deleted_at IS NULL AND purged_at IS NULL`）。
- `Memory` 模型追加三列并同步 __table_args__；`memory_result` 输出 `tags`、`last_used_at`、`source_conversation_id`；`create_memory`/`update_memory`/`confirm_memory_proposal` 全部补齐新字段。
- `memory_service.search_memory_by_query`：按 `memory_settings.enabled` 短路；关键词走 `ILIKE title/content` 与 `tags.any(query)`；排序 `last_used_at DESC NULLS LAST → updated_at DESC → id DESC`；命中后批量 `UPDATE memories SET last_used_at=now()`；空 query 回落最近记忆；`limit` 1–10。
- `memory_service.save_ai_tool_memory`：供 S2-D2 的 `save_memory` function tool 直接复用，`source_type='ai_tool'` + `source_conversation_id` 强绑定。
- `MemorySearchInput`：`query` ≤ 200、`limit` 1–10；`MemoryFields` 新增 `tags: list[str<=24]`（最多 10、去重、不允许空字符串）。
- 新路由 `POST /api/v1/memory:search`（authorized 读）。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| 本节 commit | `feat(server): 长期记忆 search_memory 与 ai_tool 来源扩展` | 迁移、模型、schemas、service、router、测试 |

### 验证记录

- 2026-10-10 `cd server && uv run alembic upgrade head`：通过（临时库 `itime_migcheck_*` 上验证 `0042_memory_ai_tool` 升级）；再 `alembic downgrade -1 && alembic upgrade head`：通过，确认 up/down 可逆。
- 2026-10-10 `uv run pytest tests/test_memories.py -x -q`：**7 passed**，含既有 3 项（设置回放/CRUD 生命周期跨租户/校验+游标）与新增 4 项：
  - `test_memory_search_ilike_tags_order_and_touches_last_used`：中文内容关键词命中、英文 tag 命中、空 query 回落、`last_used_at` 批量更新、trashed 不召回；
  - `test_memory_search_disabled_returns_empty`：`memory_settings.enabled=false` 时 search 返回空；
  - `test_memory_search_rejects_oversized_input`：`query>200` 与 `limit>10` 返回 422；
  - `test_memory_tags_round_trip_and_validation`：tags 创建/清空/重复/超量全链路。
- 2026-10-10 `uv run pytest tests/test_memory_context.py tests/test_review_sessions.py tests/test_review_annotations.py tests/test_review_models.py tests/test_review_cleanup.py tests/test_review_provider.py -x -q`：**19 passed**，确认既有 review proposal 与 memory_context 读通路不受回归。
- `uv run ruff check .` 待在 S2-D3 完整收尾时跑一次；本次只扩展 memory 模块，Diff 较窄。

### 未覆盖范围与剩余风险

- 对话编排仍走 `conversation_service.select_relevant_context` 的旧上下文注入，`search_memory` 仅通过 `POST /memory:search` 对外提供；function tools 调用链路、SSE `tool_called` 事件、`AIJob.tool_calls` 审计字段均在 S2-D2/S2-D3 补齐。
- `source_type='ai_tool'` 的记忆目前只能通过 `save_ai_tool_memory` 服务函数产生，没有对应 HTTP 入口；等 D2 的 `save_memory` function tool 落地后由对话流通过"写预览-确认"创建，不单开管理员 API。
- 前端 `/me/长期记忆` 仍为 S1 遗留 UI，没有 tags 显示与搜索框；在 S2-E 阶段统一更新。
- `server/src/itime/ai/provider.py`、`server/src/itime/review/job_service.py` 继续保持未暂存，按 AGENTS.md 第 3 条和用户授权。

## M7-L：课表工作台首页重构

对应 2026-10-20 用户反馈（前端不好用、课表应为主页、任务要能设周期、AI 应为主力）与[课表工作台设计](../设计/iTime-课表工作台设计.md)。本次只重写登录后的首页与入口，不动服务端数据模型。

### 本次范围

- 新增 `/timetable` 课表工作台：周次切换、学期周次、分钟定位课表、重叠分栏、当前时间线、点击空白新建、点击事件打开详情抽屉。
- 左侧今日任务：逾期/今天/本周/无日期分组；快速添加（默认今天）；勾选完成；“更多选项”支持日期、重复（每天/工作日/每周）和预计用时。
- 右侧 AI 入口：四个示例、自然语言输入、跳转 `/ai?prompt=...`；`AIPage` 自动创建或复用会话并发送首条消息，发送后清理 URL 参数。
- 登录、邮箱验证、账号恢复后的跳转从 `/ai` 改为 `/timetable`；产品导航新增“课表”，品牌链接指向课表。
- 新增课表工作台样式与响应式：桌面三列、≤1380px AI 面板下移、≤980px 单列、≤720px 课表横向滚动。
- 新增 favicon，避免页面请求 `/favicon.ico` 产生 404。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `2a97df6` | `feat(web): 新增课表工作台首页与任务/AI 入口` | 新页面、课表组件、两个对话框、路由、登录跳转、AI 预填、导航 |

### 验证记录

- 2026-10-20 `cd web && pnpm build`（`vue-tsc --noEmit && vite build`）：通过，TimetablePage 产物 31.09 kB（gzip 11.15 kB）。
- 2026-10-20 真实 API + Chromium（本机 Chromium 1228，Playwright 1.63）注册新账号、验证邮箱、创建学期与 11 条课程、5 条任务后：
  - `/timetable` 渲染本周课程、当天列高亮、学期第 8 周；
  - 点击“高等数学”打开详情抽屉，显示 2026-10-19 08:00–09:40、教三 201，并提供编辑/删除；
  - 点击周五 10:30 空白块，填写“社团例会/学生活动中心”后保存，新块立即出现在课表；
  - 在左侧输入“交互测试：借教室”回车，任务立即出现在“今天”分组；
  - 控制台无报错（favicon 修复后）。
- 2026-10-20 真实登录表单：`/login` 提交后跳转 `/timetable`，页面标题“课表 · iTime”，无页面错误。
- 2026-10-20 新用户学期引导：注册新账号后 `/timetable` 显示“先设置学期”；点击“去设置”打开复用 `CalendarSettingsDialog` 的“时间与学期”，填写 2026–2027 学期并保存，提示“学期与节次已保存”，引导消失，随后“添加课程”对话框可正常打开；页面错误 0。
- 2026-10-20 任务详情：点击任务标题打开抽屉，显示截止/重复/预计用时；点击“推迟到明天”后任务进入“本周”并提示成功；对无日期任务执行删除（确认后）移入回收站并从列表移除；页面错误 0。
- 2026-10-20 深色模式：以 `data-theme=dark` 重新加载 `/timetable`，课表、任务、AI 面板与事件块正常显示，页面错误 0。
- 截图：桌面 1480×1000、详情抽屉、交互后、手机 390×844，保存在任务证据目录 `tasks/itime/persistent/preview/`。

### 未覆盖范围与剩余风险

- M7-L 时 AI 仍只把提示词带入对话；M7-M 已接入既有 Agent 的两项工具（任务计划与课表导入），`ai/tools` 的通用写预览工具仍未接入。
- 课表暂不支持拖拽移动、拉伸改时长、跨周课程的单次例外与假期周；这些依赖 S3 的日历分钟精度与执行状态。
- 旧 `/tasks`、`/calendar`、`/plans`、`/reviews` 页面尚未换成新视觉；顶部导航只覆盖课表、任务、AI、复盘。
- Playwright 既有用例大量断言登录后 URL 为 `/ai`（33 处）且 `ux-07-app-shell` 仍按旧五入口导航检查；本次未更新，跑全量 E2E 前需要逐项适配。

## M7-M：课表首页内联 AI 写入

对应 2026-10-20 用户提供 DeepSeek Key 后要求的“AI 真正操作功能”。本次使用既有对话 Agent 的两项工具，把 AI 从“跳转对话”变为首页内联预览与确认；不改服务端数据模型。

### 本次范围

- `/timetable` 右侧 AI 面板提交后不再跳页：创建会话、发送消息、轮询 AI 作业，面板显示运行状态。
- 任务安排：渲染 `AgentProposalMessageCard`，确认后调用 `/api/v1/ai/action-proposals/{id}/apply`，创建任务与计划块；`AgentProposalMessageCard` 新增 `applied` 事件，首页收到后刷新课表与任务。
- 周期性课表：显示“查看并确认课表导入”，弹窗内复用 `TimetableImportMessageCard`；确认后写入日历系列，关闭弹窗刷新课表。
- 课表新增计划块投影（`/planning/blocks`）：以“计划”样式显示，点击可查看并跳转 `/plans`。
- 任务分组考虑计划块：无截止日期的 AI 计划任务按计划日期归入“本周”，显示排期，不再落入“无日期”。
- AI 失败（如与固定课程缓冲冲突）在卡片或面板内显示服务端错误；不静默写入。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| 本节 commit | `feat(web): 课表首页内联 AI 预览与确认` | TimetablePage、AgentProposalMessageCard、计划块、任务分组 |

### 验证记录

- 本地以管理员能力配置 DeepSeek（`conversation` 与 `calendar_screenshot`，`deepseek-chat`，`https://api.deepseek.com/v1`），Key 仅加密存于本地数据库，未写入仓库或文档；每日额度策略配置为 USD 5。
- 2026-10-20 真实 AI 任务计划：输入“明天晚上九点开组会，预计一小时”，AI 返回提案（2026-10-21 21:00–22:00），确认后创建任务与计划块；课表出现“组会”计划块，任务进入“本周”。
- 2026-10-20 冲突保护：15:00–16:00 的组会提案在应用时被 `plan_fixed_conflict` 拒绝（与 16:00 体育课的 15 分钟缓冲冲突），符合“先预览后确认、失败不写入”的约束。
- 2026-10-20 真实 AI 课表导入：输入“从这段文字导入……每周五 18:00-19:00 游泳，持续到这学期结束”，AI 生成 1 条候选；确认后写入每周五系列，课表出现“游泳”。
- 2026-10-20 首页内联流程：任务提案确认后 URL 仍为 `/timetable`，页面错误 0；课表刷新后出现计划块。
- `cd web && pnpm build`：通过；TimetablePage 产物 38.69 kB（gzip 13.26 kB）。

### 未覆盖范围与剩余风险

- 仍只接入 `propose_itime_task_plan` 与 `propose_itime_timetable_import`；`ai/tools` 的任务/规划/复盘/记忆通用写工具、SSE 工具轨迹、`AIJob.tool_calls` 审计仍属于 S2-D3。
- `calendar_screenshot` 当前复用 DeepSeek 文本模型，只验证了文字课表导入；图片/截图识别需要视觉模型，本次未验证，可能失败。
- 计划块与任务完成状态未联动：在首页完成任务后，计划块仍保留到下次计划版本更新。
- 每次首页 AI 请求都会新建会话；会话列表会累积测试会话，后续需要归并或自动清理。
- AI 配置与额度通过本地数据库直接写入完成验证；正式环境仍应走管理员界面，避免绕过审计。

## M7-N：周次、节次与任务周期编辑

对应“合理课表应用 + 任务可设周期”的缺口：周次只能前后翻、节次表未进入课表、任务只能新建不能编辑。

### 本次范围

- 课表周次改为下拉选择：按学期首周与结束日期生成 1..N 周，可直接跳转；保留上一周/下一周/今天。
- 课程对话框新增开始周/结束周；课程只写入所选周次的 `week_numbers`，开始日期由起始周与所选星期推导，避免中途加课只从当前周开始。
- 学期详情通过 `GET /terms/{id}` 加载节次（列表接口不含 `periods`）；有节次时时间轴显示“第 N 节 + 开始时间”，并按节次开始位置绘制横线；无节次时保持小时刻度。
- 任务支持编辑：详情抽屉增加“编辑”，复用任务对话框，预填标题、日期、重复方式、重复结束日期与预计用时。
- 重复方式扩展为每天/工作日/每周/每月/每年；重复任务必须提供结束日期（或保留已有次数），修复此前每周任务因缺少结束条件被服务端拒绝的问题。
- 编辑任务时保留原 `until`/`count`；修改重复方式时按月/年日期重建 day_of_month/month。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| 本节 commit | `feat(web): 周次跳转、节次轴与任务周期编辑` | TimetableBoard、TimetablePage、QuickTaskDialog |

### 验证记录

- 2026-10-20 通过 API 为“2026 秋季学期”写入 8 个节次（08:00–17:40）；重新加载 `/timetable` 后时间轴显示第 1–8 节及开始时间，课表按节次横线对齐。
- 2026-10-20 周次下拉选择第 9 周：日期切换为 10/26–11/1，课程按重复规则正常出现。
- 2026-10-20 课程周次范围：新建“测试半学期课”第 3–5 周、周二 13:00–14:00；第 3、5 周出现，第 2、6 周不出现；测试后删除。
- 2026-10-20 编辑任务“交互测试：借教室”为标题加“（已编辑）”、每周二、至 2027-01-15；保存后数据库 `task_schedules` 为 `weekly / [2] / until=2027-01-15`，详情显示“每周 二 · 至 1月15日”。
- `cd web && pnpm build`：通过；TimetablePage 产物 43.33 kB（gzip 14.47 kB）。

### 未覆盖范围与剩余风险

- 计划块仍按时间定位，节次较多且午休较长时会出现较大空白；后续可评估压缩非教学时段或提供“按节次等高”视图。
- 编辑任务日期不会同步已经生成的计划块；需要在后续计划版本机制中处理。
- 任务完成状态与计划块未联动；重复任务完成仍按整条任务处理，不区分单个 occurrence。
- 课表仍未支持拖拽移动、拉伸时长、单次例外与假期周。

## M7-O：AI 修改与完成任务

对应“AI 要能操作大部分功能”的下一步：在既有的任务计划/课表导入之外，让 AI 能修改或完成已有任务，并继续走预览确认。

### 本次范围

- Agent 新增 `propose_itime_task_update`（标题/描述/估时/优先级）与 `propose_itime_task_complete` 两个工具；`task_id` 只能来自服务端上下文，模型不能编造。
- 工具结果写入 `ai_action_proposals`（复用 `update_task`/`complete_task` 白名单），AIJob 输出 `proposal_id` + `proposal_kind=task_action`。
- 新增 `/api/v1/ai/tool-proposals/{id}`、`/apply`、`/reject`：应用前校验所有权、pending、过期、版本、任务可编辑性；更新任务并发出同步变更，完成调用既有 `complete_task` 服务。
- 前端新增 `TaskActionProposalCard`：显示任务名与变更（估时、优先级、标题），确认/拒绝；首页 AI 面板和 `/ai` 对话都能渲染。
- 应用成功后首页刷新课表与任务；卡片保留“已应用”状态。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `590b69a` | `feat(ai): 支持 AI 修改与完成任务并预览确认` | provider 工具、action proposal apply/reject、router、卡片、测试 |

### 验证记录

- 2026-10-20 真实 DeepSeek：输入“把「写高数第 8 周作业」的预计用时改成 90 分钟”，AI 返回修改提案；确认后任务详情显示预计 90 分钟，数据库同步更新。
- 2026-10-20 真实 DeepSeek：输入“帮我完成「背 50 个英语单词」这个任务”，AI 返回完成提案；确认后数据库 `tasks.status=completed`，任务从首页列表移除。
- 2026-10-20 回归：任务计划提案仍可生成并拒绝（`task plan regression OK`）；首页未出现页面错误。
- `uv run pytest tests/test_agent_provider.py tests/ai/test_action_proposals.py -q -W ignore::pytest.PytestUnraisableExceptionWarning`：**7 passed**（Windows 下 psycopg 析构告警需忽略，逻辑通过）。
- `uv run ruff check .`：通过；`pnpm build`：通过。

### 未覆盖范围与剩余风险

- 暂不支持 AI 修改任务的日期/重复规则、删除任务、修改/删除课程或计划块；这些需要各自的确认与应用逻辑。
- 重复任务的“完成某一次”仍需要 occurrence 上下文，当前工具只支持非重复任务，重复任务会返回服务端错误。
- 卡片中的任务名来自创建提案时的快照；若任务在确认前改名，应用仍按 ID 生效，预览可能与最新标题不一致。

## M7-P：AI 删除任务

补齐任务“增删改”中的删除：AI 生成删除预览，用户确认后走与手动删除相同的 30 天回收站语义。

### 本次范围

- 迁移 `0044_ai_action_delete_task`：`ai_action_proposals` 的 `tool_name` 白名单加入 `delete_task`；降级时先清理该工具的历史提案，再恢复旧约束。
- Agent 新增 `propose_itime_task_delete`；解析、AIJob 输出与卡片沿用 `task_action` 通道。
- 应用逻辑：校验所有权、可编辑/可读、pending、过期和版本；写入 `deleted_at`、`purge_after=+30 天`，停止公开链接抓取并发出同步变更，复用任务墓碑语义。
- `TaskActionProposalCard` 支持删除标题、回收站说明与“确认删除”。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `24ae466` | `feat(ai): 支持 AI 删除任务并移入回收站` | 迁移、provider 工具、apply、卡片、测试 |

### 验证记录

- 2026-10-20 真实 DeepSeek：输入“把「复习数据结构：链表与栈」这个任务删掉”，AI 返回删除提案；确认后任务从首页移除，数据库记录回收站时间。
- 迁移 `0044` 执行 `downgrade -1` 与 `upgrade head` 均成功（降级会清理 `delete_task` 历史提案以避免约束冲突）。
- `uv run pytest tests/test_agent_provider.py tests/ai/test_action_proposals.py -q -W ignore::pytest.PytestUnraisableExceptionWarning`：**7 passed**；`ruff` 与 `pnpm build` 通过。

### 未覆盖范围与剩余风险

- 删除是软删除，30 天后由既有 cleanup worker 物理清理；AI 不提供“永久删除”。
- 仍不支持 AI 修改任务日期/重复规则、修改/删除课程与计划块。

## M7-Q：AI 修改任务截止日期

在任务修改工具上增加截止日期，覆盖“把这件事挪到明天/23 号”的高频操作。

### 本次范围

- `AgentTaskUpdate` 增加 `due_date`；至少一个变更字段的校验同步扩展。
- 应用 `update_task` 时，如果带 `due_date`：锁定 `task_schedules`，仅允许 `recurrence_frequency='none'` 且已有 `time_zone` 的任务；用 `resolve_task_schedule`/`task_schedule_values` 写回 `start_date`、`due_kind=date` 等字段，保留 splittable 与最小块，任务版本与同步变更统一提交。
- `TaskActionProposalCard` 显示“截止日期改为 M月D日”。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `92fdccb` | `feat(ai): 支持 AI 修改任务截止日期` | provider schema、apply、卡片 |

### 验证记录

- 2026-10-20 真实 DeepSeek：输入“把「整理心理学导论笔记」的截止日期改到 10 月 23 日”，AI 生成修改提案；确认后卡片显示“已应用”，任务列表变为 `30 分钟 · 10/23`。
- 重复任务（如每周任务）请求改日期会返回 409 `ai_recurring_task_date_unsupported`，提示先在任务页调整重复规则。
- `pytest tests/test_agent_provider.py tests/ai/test_action_proposals.py`：**7 passed**；`ruff` 与 `pnpm build` 通过。

### 未覆盖范围与剩余风险

- 不支持 AI 修改重复任务的日期/重复规则；不支持 AI 修改或删除课程/计划块。
- 修改日期会走非重复任务的简单排期路径，不处理 occurrence 补做/调期记录（重复任务已拒绝）。

## M7-R：任务工作台重写

旧 `/tasks` 页面视觉与首页不一致，且首次进入需要面对大量筛选/批量操作。按用户“UI 可重写”的授权，把 `/tasks` 换成轻量任务工作台，旧页面保留为高级管理。

### 本次范围

- 新增 `TaskWorkspacePage`：顶部与课表一致的品牌/导航/添加任务/AI/账号；今天/本周/全部/已完成筛选；标题搜索；快速添加（默认今天）。
- 任务列表按逾期/今天/本周/以后分组，显示估时、重复、优先级与截止日；勾选完成/重新打开；点击打开详情抽屉（完成、编辑、推迟到明天、删除、高级管理）。
- 列表编辑复用 `QuickTaskDialog`；支持每天/工作日/每周/每月/每年与重复结束日期；保留原有 count/until 语义。
- 路由拆分：`/tasks` → 新工作台，`/tasks/full` → 旧 `TasksPage`；新页面提供“高级管理（步骤/标签/回收站）”入口。
- 响应式：≤760px 单列、筛选按钮均分、隐藏次要 AI 链接。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `e87d1fa` | `feat(web): 重写任务工作台并保留高级管理入口` | TaskWorkspacePage、路由 |

### 验证记录

- 2026-10-20 真实服务 + Chromium：登录进入 `/tasks`，快速添加“新任务页测试”，打开详情编辑为“新任务页测试（已编辑）”，保存成功；勾选完成后切换到“已完成”可见。
- 桌面 1440×1000 与手机 390×844 截图均正常，页面错误 0。
- `pnpm build`（含 `vue-tsc`）通过。

### 未覆盖范围与剩余风险

- 新工作台不展示项目/标签/步骤/回收站，统一通过 `/tasks/full` 的高级管理处理。
- 旧 `/tasks/full` 仍使用旧 AppShell 视觉，后续需要统一或逐步迁移高级功能。
- 任务搜索为前端过滤当前加载的 50 条，不是服务端全量搜索。

## M7-S：AI 删除课程

课表的“新增/导入/手动编辑”之外，补上 AI 删除整门课程，继续走预览确认。

### 本次范围

- `busy_calendar` 上下文增加 `series_id` 与 `location`，模型只能引用服务端给出的系列 ID。
- Agent 新增 `propose_itime_course_delete`；工具结果写入 `ai_action_proposals`，AIJob 输出 `proposal_kind=course_action`。
- 迁移 `0045_ai_course_delete`：白名单加入 `delete_calendar_series`；降级先清理该工具历史提案。
- 应用逻辑复用 `calendar.lifecycle_service.trash_calendar_series`：整组重复安排进入 30 天回收站，停止公开订阅/链接的可见性，并发出 `calendar_series` 同步变更。
- 前端新增 `CourseActionProposalCard`：显示课程名、“确认后整门课程（含重复安排）移入回收站”和确认/拒绝；首页 AI 面板与 `/ai` 对话都能渲染，应用后刷新周课表。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `2fb3e87` | `feat(ai): 支持 AI 删除课程并预览确认` | provider、迁移 0045、apply、课程卡片、测试 |

### 验证记录

- 2026-10-20 真实 DeepSeek：输入“把「游泳」这门课从课表里删掉”，AI 返回课程删除提案；确认后周五 18:00-19:00 的课程块消失，数据库 `calendar_series.deleted_at` 非空、version=2。
- 迁移 `0045` 的 `downgrade -1` 与 `upgrade head` 均成功（降级会清理 `delete_calendar_series` 历史提案）。
- `pytest tests/test_agent_provider.py tests/ai/test_action_proposals.py`：**7 passed**；`ruff` 与 `pnpm build` 通过。

### 未覆盖范围与剩余风险

- 只支持删除整门课程，不支持 AI 修改课程时间/地点/星期，也不支持单次例外删除。
- 删除后恢复仍走日历页/高级管理，AI 未提供恢复工具。
- 课程删除提案依赖 busy_calendar 中的 series_id；若模型未引用该字段会返回校验错误。

## M7-T：AI 修改课程时间与地点

课表“课程增删改”的最后一块：AI 可以改课程名、地点和每日上课时间，星期与周次范围保持不变，仍然预览确认。

### 本次范围

- Agent 新增 `propose_itime_course_update`：`series_id` 来自 busy_calendar，字段为 `name`、`location`、`starts_at`、`ends_at`，不支持星期/周次。
- 迁移 `0046_ai_course_update`：白名单加入 `update_calendar_series`；降级清理该工具历史提案。
- 应用逻辑从现有 series 构造 `SeriesFields`/`SeriesRecurrenceInput`，重新调用 `resolve_series_draft` 与 `series_values`，校验时间、学期与周次后写回并发出 `calendar_series.updated`。
- 课程卡片同时支持修改/删除：修改显示“课程名/地点/开始/结束”变更，确认按钮为“确认修改”。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `f1cd653` | `feat(ai): 支持 AI 修改课程时间与地点` | provider、迁移 0046、apply、课程卡片、测试 |

### 验证记录

- 2026-10-20 真实 DeepSeek：输入“把「心理学导论」改到 15:00-16:00，地点改为文科楼 305”，提案确认后课程块移到 15:00，地点更新；再改地点到 307 也成功。
- 修复点：课程系列历史数据带 `recurrence_until`，而课程 recurrence 不允许通用结束条件；更新时对 `kind=course` 清空 until/count 再校验。
- 数据库确认：`starts_at=15:00:00`、`ends_at=16:00:00`、`location=文科楼 307`、version 增加。
- 迁移 `0046` 的 `downgrade -1` 与 `upgrade head` 均成功。
- 后端 `pytest ...`：**7 passed**；`ruff` 与 `pnpm build` 通过。

### 未覆盖范围与剩余风险

- 不支持 AI 修改课程星期、周次范围、单次例外或调课。
- 课程历史 `recurrence_until` 不会在本次更新中清掉（`series_values` 未覆盖），但后续更新会继续按 course 规则清空 until/count 再校验。
- 课程时间变更不会主动检查与其它课程的冲突，仍按现有 series 校验语义处理。

## M7-U：AI 修改任务重复规则与重复实例完成

补齐“周期任务”的 AI 编辑和日常完成：AI 可以把任务改成周期任务，首页勾选重复任务时完成的是本周 occurrence。

### 本次范围

- `AgentTaskUpdate` 增加 `repeat`、`repeat_weekdays`、`repeat_until`、`repeat_count`；至少一个变更字段，until/count 互斥，非每周不能带星期。
- 应用重复规则时锁定 `task_schedules`，拒绝已有 occurrence 状态的任务；按现有 due date/time 构造 `TaskScheduleFields`，用 `resolve_task_schedule`/`task_schedule_values` 写回 frequency/interval/weekdays/until/count。
- `TaskActionProposalCard` 显示重复规则、重复星期、重复至/次数。
- 首页 `completeTask` 对重复任务调用 `listTaskOccurrences` + `updateTaskOccurrenceStatus` 完成本周实例；完成后 `loadWeek`/`loadTasks`，本周列表用 open occurrence 过滤，不再把该任务当作本周待办。
- 任务工作台同样按 occurrence 完成重复任务。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `26159cf` | `feat(ai): 支持 AI 修改任务重复规则` | provider、apply、卡片 |
| `a703d47` | `fix(web): 重复任务按实例完成并刷新本周列表` | 首页/任务工作台 occurrence 完成与过滤 |

### 验证记录

- 2026-10-20 真实 DeepSeek：输入“把「整理心理学导论笔记」改成每周五重复，重复到 12 月 31 日”，提案显示每周五/重复至 12/31；确认后数据库 `recurrence_frequency=weekly`、`recurrence_weekdays=[5]`、`recurrence_until=2026-12-31`。
- 真实浏览器勾选该重复任务，页面提示“本次重复任务已完成”，任务从本周列表消失；数据库 occurrence `2026-10-23` 状态为 completed。
- 后端 `pytest ...`：**7 passed**；`ruff` 与 `pnpm build` 通过。

### 未覆盖范围与剩余风险

- 已有 occurrence 状态的任务不能由 AI 改重复规则，需先在高级任务页处理。
- 任务工作台完成重复实例后任务行仍会保留（仅首页按本周 occurrence 过滤）；后续应统一。
- 重复任务完成窗口目前取“今天起 14 天”，更远的实例需翻周后再完成。

## M7-V：核心体验最终验收

2026-10-20 对“课表 + 任务 + AI 主导”目标做一次跨页面最终验收。

### 覆盖范围

- **课表**：学期/周次/节次轴、周次下拉、课程按周次范围创建/编辑/删除、文本 AI 课表导入、AI 修改课程名/地点/时间、AI 删除整组课程。
- **任务**：新建/编辑/完成/删除、截止日期、每天/工作日/每周/每月/每年重复、重复结束日期/次数、计划块展示、重复任务按 occurrence 完成。
- **AI**：首页内联面板创建会话并轮询；任务计划、任务修改/完成/删除/重复规则、课程修改/删除、课表导入均先预览后确认；`/ai` 完整对话复用同类卡片。
- **前端**：`/timetable` 与 `/tasks` 重写为新工作台；`/tasks/full` 保留步骤/标签/项目/回收站等高级管理。
- **数据**：真实 PostgreSQL、真实 DeepSeek、真实 Chromium；非 mock。

### 最终验证记录

- Playwright 最终 smoke：登录 → `/timetable` 周视图 → 下一周/上一周 → `/tasks` 搜索 → `/ai` → `/tasks/full` → `/reviews`，页面错误 **0**；截图 `persistent/preview/final-timetable-2026-10-20.png`、`final-tasks-2026-10-20.png`。
- 后端 `pytest tests/test_agent_provider.py tests/ai/test_action_proposals.py`：**7 passed**；`ruff check .` 通过。
- `pnpm build`（含 `vue-tsc`）通过。
- 迁移 `0043`→`0046` 均已 up/down 验证；提案白名单与软删除语义可逆。
- 真实 DeepSeek 已验证：任务计划/课表导入/改估时/完成任务/删除任务/改截止日/改重复规则、课程改时间地点/删课程；证据截图见 `tasks/itime/docs/03-课表工作台重构.md`。

### 明确未纳入本次目标

- 旧 `/ai` 完整对话页、`/calendar`、`/plans`、`/reviews` 仍是旧视觉；核心工作台已重写，但这些高级页尚未统一。
- 课表拖拽移动/拉伸、单次例外、假期周、图片/截图课表识别。
- AI 对计划块/复盘/记忆的写预览（S2-D3 的通用工具链），以及已有 occurrence 的任务改重复规则。
- 任务工作台完成重复实例后行未立即隐藏（首页已按本周 occurrence 过滤）。

**结论**：本次 objective 的核心范围已实现并通过真实服务/浏览器验证；上述条目是后续增强，不阻塞“课表 + 任务 + AI 主导”核心体验。

## M7-W：本地 OCR 图片课表导入

图片课表是用户提出的重点。当前 DeepSeek chat 是文本模型，直接发图片会失败，因此增加本地 OCR 预处理，把图片转成文本后再走既有 AI 导入审核。

### 本次范围

- 新增 `calendar/ocr_service.py`：RapidOCR（onnxruntime，本地 CPU）中文识别，按行/列排序输出，失败时明确返回 `OcrUnavailable`。
- `recognize_ai_import` 对图片先 OCR；识别成功则改为 text 导入，不再向文本模型发送 image_url；OCR 失败才回退原图片路径。
- 图片候选归一化：
  - 课程有节次和时间时清空显式时刻，避免 `period_time_conflict`。
  - 图片没有课程周次时，按当前学期全周导入并写入警告。
  - 固定安排没有结束日期时，默认到当前学期结束日。
- 首页 AI 面板新增“图片课表”按钮与隐藏 file input，上传后轮询 AI job，成功自动打开候选审核弹窗。
- 新依赖 `rapidocr-onnxruntime` 写入 `server/pyproject.toml` 与 `uv.lock`。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `3195fa7` | `feat(calendar): 本地 OCR 图片课表导入并自动补全周次` | OCR、provider 回退、候选归一化、首页入口 |

### 验证记录

- 2026-10-20 真实 Chromium：登录 `/timetable` → 点击“图片课表” → 选择从课表工作台截取的 PNG → 页面显示“正在识别图片…” → 审核弹窗打开。
- 结果：13 个候选，11 个可确认，2 个需校对；页面错误 0；截图 `persistent/preview/timetable-image-import-2026-10-20.png`。
- OCR 输出包含“高等数学/线性代数/数据结构/大学英语/心理学导论”等课程与时间、教室；AI 还原出周一至周五课程。
- 后端 `ruff` 通过；`pnpm build` 通过；未执行图片候选的最终确认，避免向当前测试课表写入重复课程。

### 未覆盖范围与剩余风险

- OCR 对低分辨率、手写、复杂合并表头仍有误差；图片只显示当前周时会按整学期导入，需要用户审核。
- 不是视觉模型方案；图片里的颜色/跨行合并等版式信息会丢失。
- 2 个候选仍需校对，说明归一化不能覆盖所有固定安排/重复规则组合。
- 口述/语音课表尚未接入；下一步将增加语音输入并复用同一导入链路。

## M7-X：AI 单次例外（取消/调整一次课程）

课表重复系列需要“只改这一次”的能力。AI 现在可以取消或调整某个 occurrence，并用预览确认。

### 本次范围

- Agent 新增 `propose_itime_occurrence_exception`：参数为 `series_id`、`occurrence_date`、`action`（`cancelled`/`moved`），可选 `moved_start_date`/`moved_starts_at`/`moved_ends_at`；模型不能改整组。
- 迁移 `0047_ai_occurrence_exception` 把 `create_calendar_exception` 加入提案白名单。
- 应用逻辑：校验系列/occurrence、检查已有例外，构造 `ExceptionCreate`，复用 `find_occurrence_date`、`validate_exception_fields`、`exception_values` 创建 `calendar_exceptions`，并 bump series 版本、发出同步变更。
- `CourseActionProposalCard` 支持“取消本次/调整本次”，显示原日期与调课目标，提示“只影响这一次”。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `58e46eb` | `feat(ai): 支持 AI 取消或调整单次课程` | provider、迁移 0047、apply、卡片 |

### 验证记录

- 2026-10-20 真实 DeepSeek：输入“把这周五的「心理学导论」取消一次，只取消这一次”，生成“取消本次”提案；确认后周课表中该事件显示为已取消（`board-event cancelled`），其它周次不受影响。
- 2026-10-20 真实 DeepSeek：输入“把这周四的「大学英语」调到下午 4 点，只调这一次”，确认后 10-22 的课从 08:00–09:40 移到 16:00–17:40，周四原时段消失，其它周次不变。
- 数据库确认：`calendar_exceptions` 新增取消（10-23 心理学导论）与调课（10-22 大学英语）记录。
- 迁移 `0047` 的 down/up 成功。
- 后端 `pytest ...`：**7 passed**；`ruff` 与 `pnpm build` 通过。

### 未覆盖范围与剩余风险

- 已有例外的 occurrence 不能再次由 AI 创建例外，需要编辑现有例外。
- 例外只支持取消/移动，不支持只覆盖名称/地点；整组修改仍走课程更新工具。

## M7-Y：口述课表输入

在首页 AI 面板增加“口述”入口，用浏览器语音识别把自然语言课表转成输入文字。

### 本次范围

- 使用 `window.SpeechRecognition`/`webkitSpeechRecognition`，语言 `zh-CN`，interimResults 打开。
- 识别结果追加到 AI 输入框；用户可修改后再点“交给 AI”，不直接写入数据。
- 监听中按钮显示“正在听…”，再次点击停止；权限拒绝/不支持/网络错误有中文提示，不影响手动输入。
- 组件卸载时停止识别。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `5db0292` | `feat(web): 首页 AI 面板支持口述课表` | 麦克风按钮、Web Speech API、错误提示 |

### 验证记录

- 2026-10-20 Chromium（`--use-fake-ui-for-media-stream`）：按钮存在并可进入“正在听…”状态，页面错误 0；截图 `persistent/preview/timetable-voice-input-2026-10-20.png`。
- **未验证真实语音转写**：headless Chromium 无麦克风/语音服务，只能验证 UI 与启动路径；需要在有麦克风的真实 Edge/Chrome 中人工说一句“每周三 19:00 健身”确认转写。

### 未覆盖范围与剩余风险

- 依赖浏览器语音服务与网络；Firefox/部分环境不支持，失败时只能手动输入。
- 不做服务端转写、不上传音频；隐私风险较低但识别质量取决于浏览器。
- 识别文本仍会走 AI 提案确认，不会直接改课表。

## M7-Z：统一全站顶栏与旧页面外壳

旧 `/ai`、`/calendar`、`/plans`、`/reviews` 仍用侧栏 AppShell，与新工作台不一致。将 AppShell 改为统一顶栏布局，旧页面无需逐个重写即可获得同一导航/品牌/账号视觉。

### 本次范围

- `AppShell` 改为品牌 + 横向导航 + AI/头像/退出动作 + 内容区；有 secondary 时左侧显示会话/复盘工具栏，否则内容全宽。
- `ProductNavigation` 增加“复盘”，并把首页顶栏与旧页面顶栏统一到同一组件。
- 移动端：导航收进抽屉，会话 secondary 也进抽屉；AI 页面 `ai-main` 高度适配顶栏，修复移动端标题被挤成竖排。
- `ConversationHeader` 增加 `no-back` 布局，避免没有返回按钮时标题只占 44px。
- 新工作台 `/timetable`、`/tasks` 顶栏补上“日历 / 规划”，与旧页面导航项对齐；移动端导航可横向滚动，头像承担“我的”入口。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `fc0101b` | `feat(web): 统一全站顶栏导航与旧页面外壳` | AppShell、导航、AI 头部、复盘入口 |

### 验证记录

- 2026-10-20 Chromium 桌面 1440：`/ai`、`/calendar`、`/reviews`、`/plans` 均显示同一顶栏，页面错误 0；截图 `ai-shell-2026-10-20.png`、`calendar-shell-2026-10-20.png`、`reviews-shell-2026-10-20.png`、`plans-shell-2026-10-20.png`。
- 2026-10-20 新工作台导航补齐“日历/规划”后，桌面与 390×844 移动端均正常、页面错误 0；截图 `timetable-nav-unified-2026-10-20.png`、`timetable-nav-mobile-2026-10-20.png`、`tasks-nav-unified-2026-10-20.png`。
- 移动端 390×844：`/ai` 顶栏、欢迎区、输入框正常；打开导航抽屉可看到全部导航与会话列表；页面错误 0；截图 `ai-shell-mobile-2026-10-20.png`、`ai-shell-mobile-drawer-2026-10-20.png`。
- `pnpm build` 通过。

### 未覆盖范围与剩余风险

- 顶栏统一了，旧页面内容区（日历表格、复盘表单等）仍保留各自旧样式细节。
- 新工作台导航为课表/任务/AI/日历/规划/复盘 + 头像，旧页面多一个显式“我的”；功能入口已对齐。

## M7-AB：AI 调整学习计划块

补齐“计划”侧的工具链：AI 可以移动/缩放一个已有学习计划块，不改任务本身。

### 本次范围

- `busy_calendar` 之外新增 `plan_blocks` 上下文（plan_id、block_id、task、日期、起止时间）。
- 新增 `propose_itime_plan_block_update`：`plan_id`、`block_id`、可选 `local_date`/`starts_at`/`ends_at`。
- 应用时读取当前计划，把目标块改成新时间，其余块和未排项原样保留，构造 `ManualPlanVersionInput` 调用 `create_manual_plan_version`；服务端做固定日程冲突与 revision 校验。
- 复用任务提案卡片渲染“调整计划块”，确认/拒绝走同一 tool-proposal apply/reject。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `0b18e60` | `feat(ai): 支持 AI 调整学习计划块` | provider、apply、上下文、卡片 |
| `065447a` | `chore: 移除临时计划块验证脚本` | 清理 |

### 验证记录

- 2026-10-20 真实 DeepSeek：输入“把周三晚上 9 点的「组会」计划块挪到 8 点半，时长一小时”，生成调整提案；确认后计划块从 21:00–22:00 移到 20:30–21:30，其它块不变。
- 数据库确认：当前 plan version=4、source=manual，组会块 `local_starts_at=20:30`。
- 冲突路径：先尝试移到 20:00，服务端返回 `plan_fixed_conflict`（健身 19:00–20:00 的缓冲不足），提案未被应用。
- 后端 `pytest ...`：**7 passed**；`ruff` 与 `pnpm build` 通过。

### 未覆盖范围与剩余风险

- 只支持移动/缩放单个计划块，不支持新增/删除块或批量重排。
- 每次调整都会重建整周手动计划版本；输入 revision 变化或固定冲突时会拒绝，需要重新生成。
- `plan_blocks` 上下文只覆盖未来 7 天。

## M7-AC：AI 新建学习计划块

在“调整已有计划块”之外，补上“把任务安排成计划块”。

### 本次范围

- 新增 `propose_itime_plan_block_create`：`plan_id`（plan_blocks 上下文）、`task_id`（tasks 上下文）、日期与 15 分钟对齐的起止时间。
- 迁移 `0048_ai_plan_block_create` 将 `create_plan_block` 加入提案白名单。
- 应用时读取当前周计划，保留其它块/未排项，移除该任务的未排项，追加新 `ManualPlanBlockInput`，调用 `create_manual_plan_version`；服务端校验固定冲突、截止日后移与 revision。
- 任务提案卡片支持“安排计划块”，显示任务名、日期与起止时间。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `feac535` | `feat(ai): 支持 AI 新建学习计划块` | provider、迁移 0048、apply、卡片 |

### 验证记录

- 2026-10-20 真实 DeepSeek：输入“把「写高数第 8 周作业」安排在今天下午 4 点，做 90 分钟”，生成“安排计划块”提案；确认后周课表出现 16:00–17:30 的计划块。
- 数据库确认：plan version=5、source=manual，`写高数第 8 周作业` 块日期 2026-10-20、16:00–17:30。
- 失败路径：安排到 10/23 被 `plan_after_deadline` 拒绝；安排到 10/21 16:00 被 `plan_fixed_conflict` 拒绝（体育课），说明服务端校验生效。
- 迁移 0048 down/up 通过；后端 **7 passed**；`ruff` 与 `pnpm build` 通过。

### 未覆盖范围与剩余风险

- 只能往已有周计划里加块；没有计划时需要先生成计划。
- 重复任务未指定 occurrence_key，当前适合非重复任务。
- 不支持删除计划块或批量重排。

## M7-AD：AI 移除学习计划块

计划工具链最后一块：AI 可以移除一个计划块，任务本身保留并回到未排状态。

### 本次范围

- 新增 `propose_itime_plan_block_delete`：`plan_id`、`block_id`、`assistant_message`。
- 迁移 `0049_ai_plan_block_delete` 把 `delete_plan_block` 加入提案白名单。
- 应用时读取当前计划，移除目标块，保留其它块，并为该任务补一条 `outside_scope` 未排项；重建手动计划版本。
- 任务提案卡片支持“移除计划块”，确认后刷新周课表。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `7c2ea61` | `feat(ai): 支持 AI 移除学习计划块` | provider、迁移 0049、apply、卡片 |

### 验证记录

- 2026-10-20 真实 DeepSeek：输入“把今天下午 4 点「写高数第 8 周作业」的计划块去掉”，确认后周课表中的计划块消失，任务仍在“本周”列表。
- 数据库确认：plan version=6、source=manual；该计划版本中“写高数”块数为 0，未排项为 1，说明任务回到未排状态。
- 迁移 0049 down/up 通过；后端 **7 passed**；`ruff` 与 `pnpm build` 通过。

### 未覆盖范围与剩余风险

- 只能删除单个计划块，不支持批量重排/清空整周。
- 删除后以未排项保留任务，用户仍可在任务页或 AI 重新安排。
- 计划块上下文只覆盖未来 7 天。

## M7-AE：AI 修改课程周次范围

课程更新工具补上“第几周到第几周”，不必去日历页手动改。

### 本次范围

- `AgentCourseUpdate` 增加 `week_numbers`；`busy_calendar` 上下文增加每门课的 `week_numbers`，模型可引用实际范围。
- 应用时用新的 week_numbers 构造 `SeriesRecurrenceInput`，课程 `start_date` 置空由 `resolve_series_draft` 根据周次/星期推导；超出学期或空周次由服务端拒绝。
- 课程卡片显示“上课周次改为 1、2、…、8 周”。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `3d6cc00` | `feat(ai): 支持 AI 修改课程周次范围` | provider、上下文、apply、卡片 |

### 验证记录

- 2026-10-20 真实 DeepSeek：输入“把「心理学导论」改成第 1 到 8 周上课”，提案确认后数据库 `course_week_numbers=[1..8]`，version 增加。
- 真实 Chromium：第 8 周课表仍显示该课程；第 9 周及以后不再出现（按新周次）。
- 后端 **7 passed**；`ruff` 与 `pnpm build` 通过。

### 未覆盖范围与剩余风险

- 不支持改星期；星期变更仍需在课程编辑对话框处理。
- 图片导入默认全学期的课程可能有多条同名系列，AI 只改它引用的那一条。
- 周次编号按当前学期，跨学期课程仍不支持。

## M7-AF：AI 修改课程星期

课程修改工具补上 `weekday`，可以把整门课从周三挪到周二，时间/地点/周次保持不变。

### 本次范围

- `AgentCourseUpdate` 增加 `weekday`（1=周一），`busy_calendar` 暴露当前 `weekday`。
- 应用时用课程周次推导第一个周一，重新计算 `start_date`，并把 recurrence weekdays 改成目标星期；`resolve_series_draft` 继续校验学期/周次。
- 课程卡片显示“上课星期改为 周X”。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `14ba0c6` | `feat(ai): 支持 AI 修改课程星期` | provider、上下文、apply、卡片 |

### 验证记录

- 2026-10-20 真实 DeepSeek：输入“把「体育（羽毛球）」从周三改到周二，只改这门课”，确认后数据库最新系列 `recurrence_weekdays=[2]`、`start_date=2026-09-01`、version=2。
- 周课表中体育从周三 16:00 移到周二 16:00，其它课程不变。
- 后端 **7 passed**；`ruff` 与 `pnpm build` 通过。

### 未覆盖范围与剩余风险

- 已有单次例外不会随星期迁移；有例外的课程改星期前需要先处理例外。
- 测试账号存在多条同名导入系列，AI 只改它引用的那一条；合并重复系列仍未实现。
- 跨学期、单双周等复杂周次规则仍按现有期次模型处理。

## M7-AG：AI 新建课程

课表工具补上“直接加一门课”：给课程名、星期、时间、地点和周次，预览确认后写入整门重复课程。

### 本次范围

- 新增 `propose_itime_course_create`，参数 `term_id`（服务上下文当前学期）、`name`、`location`、`weekday`、`starts_at`/`ends_at`、`week_numbers`。
- 迁移 `0050_ai_course_create` 将 `create_calendar_series` 加入提案白名单。
- 应用时构造 `SeriesFields`（weekly、目标星期、周次），经 `resolve_series_draft` 校验后写入 `CalendarSeries` 并发出 created 变更。
- 课程卡片支持“新建课程”，显示星期、时间、周次与地点。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `ce877bf` | `feat(ai): 支持 AI 新建课程` | provider、上下文 term_id、迁移 0050、apply、卡片 |

### 验证记录

- 2026-10-20 真实 DeepSeek：输入“加一门「AI 测试课」，每周六 10:00-11:30，第 1 到 8 周，地点文科楼 101”，确认后数据库新增 `AI 测试课`：start_date=2026-09-05、weekdays=[6]、weeks 1–8、地点文科楼 101。
- 周课表第 8 周周六出现 10:00–11:30 的课程块。
- 迁移 0050 down/up 通过；后端 **7 passed**；`ruff` 与 `pnpm build` 通过。

### 未覆盖范围与剩余风险

- 不检查同名课程；重复创建同一门课会生成多条系列。
- 不支持单双周、跨学期或非 weekly 频率。
- 新建课程不会自动处理已有课程冲突；固定日程冲突仍由用户自查。

## M7-AH：课表/任务页统一全站顶栏

`/timetable` 与 `/tasks` 原先仍用页面内自带 header（品牌 + 旧导航 + 头像），与 `/ai`、`/calendar` 等页面的统一顶栏不一致；本轮把两页迁入 `AppShell`。

### 本次范围

- 两页改用 `AppShell current="timetable|tasks"`，顶栏统一为品牌 + 七个产品入口 + AI/头像/退出。
- 删除页面内的旧品牌、旧导航与旧头像，避免重复导航。
- 课表页只保留周导航工具条（上一周/周次/下一周/今天），移除与课表工具条重复的“学期设置/添加课程”按钮。
- 任务页把“添加任务”移到标题行，高级管理链接保留。
- 移动端统一为抽屉导航；课表横向滚动仍由 `TimetableBoard` 内部容器承担。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `969504c` | `feat(web): 课表与任务页统一使用全站顶栏` | TimetablePage、TaskWorkspacePage、app-shell.css |

### 验证记录

- 2026-10-20 真实 Chromium：桌面 `/timetable`、`/tasks` 均显示统一顶栏，页面内 `.tt-nav`/`.tw-nav` 为 0，页面错误 0。
- 移动端 390×844：统一顶栏 + 抽屉入口；任务页无横向溢出；课表页周工具条不再挤压“今天”，课表自身横向滚动查看周末。
- `pnpm build` 通过；`Check-Workspace.ps1` clean。

### 未覆盖范围与剩余风险

- 旧页面（`/tasks/full`、`/calendar`、`/reviews`）内容区样式仍沿用各自 CSS，只统一了外层顶栏。
- 课表页仍有旧的 `.tt-nav`/`.tt-brand` 死样式，未清理，不影响渲染。
- 移动端课表网格需要横向滑动，暂未提供“周视图/日视图”切换。

## M7-AI：本地 Vosk 口述课表

浏览器 Web Speech 在没有语音服务/网络的 Chromium 中不可用。本轮改为“浏览器录音 → 服务端 ffmpeg 转码 → 本地 Vosk 中文模型识别”，音频不出机器，也不依赖第二个云服务。

### 本次范围

- 后端新增 `POST /api/v1/ai/voice/transcribe`：登录 + AI 权限 + CSRF，上传上限 10 MiB，ffmpeg 转 16 kHz 单声道 WAV，Vosk `vosk-model-small-cn-0.22` 识别。
- 模型默认路径 `server/models/vosk-model-small-cn-0.22`，可用 `ITIME_VOSK_MODEL_PATH` 覆盖；模型不提交 git，`scripts/download-vosk-model.ps1` 负责下载。
- 前端“口述”改为 MediaRecorder 录音：点击开始、再次点击结束，上传后把识别文本追加到 AI 输入框；录音/识别状态可见。
- 依赖：`vosk` 加入 `pyproject.toml`/`uv.lock`，模型目录加入 `.gitignore`。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `cd8678e` | `feat(voice): 本地 Vosk 口述课表` | voice service/router、下载脚本、前端录音、依赖 |

### 验证记录

- 2026-10-20 真实 API：用 Windows TTS 生成“每周三晚上七点健身，持续到期末”WAV，上传接口返回“每周三 晚上 七点 健身 持续 到 期末”。
- 真实 Chromium + 假麦克风音频：点击口述→录音→停止→识别中，输入框出现识别文本；页面错误 0。
- 模型目录约 42 MiB，未提交；真实 Vosk 识别在本地进程完成。
- 后端 **7 passed**；`ruff` 通过；前端 `pnpm build` 通过。

### 未覆盖范围与剩余风险

- 目前只装中文小模型，识别质量受口音/环境噪声影响；专业名词可能误识别。
- 首次请求需要加载模型，会多几百毫秒到数秒；模型文件需部署时单独下载。
- 假麦克风测试会循环播放音频，输入框可能出现重复片段；真实麦克风只录一次。
- 浏览器兼容性以 Chromium/Edge 的 MediaRecorder 为准；无麦克风权限时提示直接输入。

## M7-AJ：AI 批量调整计划块

单个计划块的移动/新建/删除已完成；本轮补上“一次改多个块”，覆盖批量提前、整体挪动等场景。

### 本次范围

- 新增 `propose_itime_plan_block_batch`：`plan_id` + `changes[]`，每项含 `block_id`、可选日期/开始/结束时间，最多 20 项。
- 迁移 `0051_ai_plan_block_batch` 加入 `batch_update_plan_blocks` 白名单。
- 应用时读取当前计划，按 block_id 应用字段，保留未修改字段与其它块/未排项，重建手动计划版本；不存在的块返回冲突错误，非法时间按 15 分钟对齐校验。
- 卡片显示“批量调整 N 个计划块”，逐条列出标题与目标时间。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `053abf5` | `feat(ai): 支持批量调整计划块` | provider、迁移 0051、apply、卡片 |

### 验证记录

- 2026-10-20 真实 DeepSeek：输入“把 10月21日 20:30 和 10月22日 10:00 的两个「组会」计划块分别改到 20:15 和 11:45，时长不变”，生成批量卡片并确认。
- 数据库：plan version=7、source=manual；10/21 组会 20:15–21:15、10/22 组会 11:45–12:45，两个块及未排项均保留。
- 迁移 0051 down/up 通过；后端 **7 passed**；`ruff` 与 `pnpm build` 通过。

### 未覆盖范围与剩余风险

- 只支持修改已有块，不支持在批次里新建/删除块。
- 最多 20 个块；一次改整周仍要分多次或使用调整单块。
- 固定冲突/截止日后移由计划服务校验，失败时整批不写入。

## M7-AK：全站路由回归（阶段收尾）

在统一顶栏、Vosk 口述、课程/计划工具完成后，对 9 个主要路由做一次桌面 + 移动端回归，确认没有新的页面错误或外壳割裂。

### 回归范围与结果

- 桌面 1440×1000：`/timetable`、`/tasks`、`/tasks/full`、`/ai`、`/calendar`、`/plans`、`/reviews`、`/me`、`/settings` 均有统一顶栏，页面标题正确，页面错误 0，无横向溢出。
- 移动 390×844：同样 9 个路由均有统一顶栏，页面错误 0；`/tasks`、`/tasks/full`、`/ai`、`/calendar`、`/plans`、`/reviews`、`/me`、`/settings` 的 `scrollWidth=390`。`/timetable` 的网格仍在内部横向滚动，页面外壳用 `overflow-x: clip` 防止整页横移。
- 提交 `d8bc43b` 为课表页加上横向溢出保护。

### 收尾判断

- 课表：图片 OCR 导入、本地 Vosk 口述、单次取消/调课、课程/计划块预览确认均已真实跑通。
- AI 工具链：任务计划/修改/完成/删除、课表导入、课程新建/修改/删除/周次/星期、单次例外、计划块新建/调整/删除/批量调整均有真实模型与浏览器证据。
- UI：9 个主要路由统一顶栏，移动端抽屉导航；旧页面内容区保留各自样式但不割裂外壳。
- 验证：后端目标测试 7 passed、`ruff`、`pnpm build`、`Save-All`、`Check-Workspace` 均通过。

阶段目标已完成；后续可继续做的内容（同名课程合并、批次内新建/删除计划块、旧页面内容区细化、更大规模回归）属于增量优化，不影响本轮完成判断。

## M7-AL：课程教师、备注、多时间段与单双周

主流课程表产品普遍把“一门课”表达为多个可独立设置周次的时间段；iTime 之前一门课只能存一个连续时间段，且没有教师/备注入口。本轮按调研结论补齐核心模型和 UI。

### 调研与取舍

- 参考[极简课程表](https://apps.apple.com/cn/app/%E6%9E%81%E7%AE%80%E8%AF%BE%E7%A8%8B%E8%A1%A8-watch%E8%AF%BE%E7%A8%8B%E8%A1%A8/id1581272719)、[超级课程表](https://mshike.it168.com/detail/488188.html)、[WakeUp 课程表](https://info.appstore.vivo.com.cn/detail/1937016)：教师、地点、备注、多时间段、单双周/指定周是基础能力；配色、提醒、小组件、分享、成绩/考试属于外围增强。
- 本轮实现教师、备注、多时间段、每段独立单双周/指定周；配色与提醒/分享等不做。
- 详细对比见任务文档 `docs/04-课表功能对比与课程模型.md`。

### 实现

- 迁移 `0052_course_teacher_group`：`calendar_series.teacher`、`course_group_id`；已有课程把 group 设为自身 id，fixed 为 null。
- `SeriesFields` 增加 `teacher`；`SeriesCreate` 增加 `course_group_id`；结果与 occurrence 返回教师和 group。
- 课程编辑弹窗重做：教师、备注、多个时间段；每段独立上课日、时间、地点、开始周/结束周、周次规则（每周/单周/双周/指定周）。
- 编辑任意时间段会加载同 group 的全部时间段；保存统一更新，删除课程按整组移入回收站。
- 课程详情抽屉显示教师和备注；高级日历编辑保留教师字段。
- AI 课程新建/修改工具同步支持教师与备注。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `342a70e` | `feat(calendar): 支持课程教师备注与多时间段` | 迁移、模型、API、课程弹窗、详情、AI 字段 |

### 验证记录

- 真实 Chromium 创建“信号与系统”：时间段 1 周一 10:00–11:30（1–16 周，每周），时间段 2 周三 14:00–15:30（3–15 周，单周）；数据库得到两条同 `course_group_id` 的 series。
- 编辑第二段为每周并修改教师为“王老师”，两条 series 教师同时更新；第二段周次变为 3–15。
- 详情抽屉显示“教师 王老师”和备注“带计算器”。
- 删除临时双时间段课程时确认框显示“2 个时间段”，两条 series 同时进入回收站。
- 后端 7 项目标测试、`ruff`、`pnpm build` 通过。

### 未覆盖与风险

- 前端按时间段顺序保存，缺少整组原子事务；中途失败可能留下部分已保存的时间段。
- 课程颜色、提醒、小组件、分享、成绩/考试未实现。
- 高级日历页仍按单 series 编辑，修改教师不会自动同步同组其他时间段；主入口课表弹窗会同步。
- 图片导入暂不解析教师/备注；同名导入课程合并仍未实现。

## M7-AM：课表 UI、滚动与教师显示修复

针对真实使用反馈修复三类问题：移动端无法纵向滚动、课程块信息拥挤且没有教师、时间与学期弹窗层次混乱。

### 根因

- `AppShell` 的 `.app-shell-main` 设置了 `overflow: hidden`，内容高度 2539px 但视口 782px，页面没有纵向滚动容器。
- 移动端课表最小宽度 620px，7 天被压到约 82px/列；时间、标题、地点没有分级，教师字段未渲染到 `TimetableBoard`。
- `CalendarSettingsDialog` 依赖旧的全局 dialog 样式，节次行是裸输入框，移动端没有专属布局。
- 教师只在详情抽屉展示，没有课程块显示开关。
- 课程块宽度按“整天最大车道数”计算：周三只要 14:00 信号与系统与 15:00 组会重叠，08:00 高等数学、10:00 数据结构实验也会被压成 50%。

### 修复

- `.app-shell-main` 改为 `overflow-y: auto`；移动端固定顶栏 + 内容独立滚动。
- 课程块按**重叠簇**分配宽度：只有真正时间重叠的课程才分车道，单独课程占满整列。
- 相同课程重复 occurrence 在展示时合并并显示 `×N`，详情提供“保留一条并清理重复”。
- 桌面 ≥900px 课表 7 天自适应铺满；移动端 980px 画布，内部横向滑动，课程块不再压扁。
- 课程块分层：时间、标题、地点、教师；短课程 compact/mini 优先保留标题与教师；时间字号/内边距调整，避免截断。
- 设置弹窗重写为卡片式：标题区、时区、显示设置、学期与节次；顶部关闭；移动端全屏；节次行改为编号+名称+删除、开始/结束两行。
- 新增“在课程块显示教师”开关，localStorage 持久化，默认开启。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `8f9744e` | `fix(web): 修复课表滚动并重做设置与课程块可读性` | AppShell、TimetableBoard、设置弹窗、教师显示 |
| `fd1352b` | `fix(web): 按重叠簇计算课程块宽度` | 重叠簇车道、重复课程合并 |
| `0747b0b` | `fix(web): 微调课程块时间标签内边距` | 时间标签 |

### 验证

- 390×844：内容滚动高度 2539、可视高度 782；`scrollTop=520` 后可见第 5–8 节、健身和计划块。
- 移动端课表画布 980px，课程时间完整，`信号与系统` 显示“王老师”。
- 桌面 1440×1000：7 天全部显示，`08:00–09:40` 完整。
- 教师开关默认显示 3 个教师标签，关闭后 0，重新开启恢复。
- 周三宽度：08:00 高等数学、10:00 数据结构实验、19:00 健身均为整列；只有 14:00 信号与系统与 15:00 组会因真实重叠各占 50%。
- 设置弹窗 390×844 全屏，关闭按钮可用，节次行字段清晰。
- 页面错误 0；`pnpm build` 通过。

### 未覆盖

- 日期/时间输入仍是原生控件，视觉可继续统一。
- 设置桌面端可进一步压缩间距；节次模板/批量编辑未做。
- 课程颜色、显示地点/备注等开关未扩展。
- 移动端 AI 会话切换仍依赖抽屉，未做底部 tab。


## M7-AN：登录与注册去过度设计

用户反馈登录页的默认安全文案、12 位密码要求和未配置邮件服务时的邮箱验证流程严重阻碍登录。

### 修复

- 密码下限从 12 改为 **8**，上限 128；前端提示统一为“至少 8 位。”。
- 迁移 `0053_email_optional` 将注册策略默认改为不要求邮箱验证，注册成功直接建立会话；验证流程仍可在管理员策略中开启。
- 登录页移除“本次登录最长保留 30 天 / 公共设备使用后请退出”。
- `/auth/policy` 增加 `email_available`；本地 SMTP/Mailpit 视为不可用，隐藏“忘记密码”和“重新发送验证邮件”入口，避免用户走进死路。
- 清理 `746706131@qq.com` 的登录限流并标记邮箱已确认（未改密码）。

### 验证

- `/auth/policy`：`password_min_length=8`、`require_email_verification=false`、`email_available=false`。
- 真实 API：8 位密码注册返回 `verification_required=false` 并带登录会话。
- 真实 Chromium：登录页无旧文案；8 位注册后直接进入 `/timetable`，页面错误 0。
- `pnpm build`、`ruff` 通过。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `9ac334d` | `fix(auth): 简化登录注册与密码要求` | 密码策略、注册验证默认、policy、登录/注册 UI |

## M7-AO：注册结果与文案规范

用户反馈“注册请求已受理 / 如果此邮箱需要处理…”既不是成功也不是失败，无法理解。根因是注册 API 对所有非会话结果复用了防枚举的通用邮件文案。

### 修复

- 注册 API 返回明确 `status` 与 `message`：
  - 新账号且已登录：`registered` / `注册成功`
  - 需要验证：`verification_sent` / `验证邮件已发送，请查收`
  - 邮箱已存在：`already_registered` / `该邮箱已注册，请直接登录`
- 注册页按状态显示：
  - 邮箱已存在：标题“该邮箱已注册”，说明“请直接登录。”，按钮“前往登录”
  - 需要验证：标题“验证邮件已发送”
  - 新账号免验证：直接进入课表，不显示中间页
- 删除“注册请求已受理 / 去邮箱看看吧 / 如果此邮箱需要处理 / 检查垃圾邮件”等模糊或无关文案。
- 新增[文案规范](../设计/iTime-文案规范.md)，要求“成功就是成功，失败就是失败”，禁止模糊状态和实现/安全术语占用户界面。

### 验证

- 真实 API 注册已存在邮箱：`status=already_registered`，`session=null`，不覆盖密码。
- 真实 Chromium：注册页显示“该邮箱已注册 / 请直接登录”，无旧通用文案，页面错误 0。
- 8 位新账号注册仍直接进入课表。
- `pnpm build`、`ruff` 通过。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `2ba94b7` | `fix(auth): 注册结果文案明确化` | 注册 API 状态、AuthPage 成功页、文案规范 |

## M7-AP：错误提示只说真实原因

用户反馈登录失败时同时看到“邮箱或密码不正确”“本次操作未保存”“请求编号”，信息过多且答非所问。要求提示合理、写真实原因、面向人。

### 修复

- 登录接口区分真实原因：
  - 邮箱不存在：`该邮箱未注册。`
  - 密码错误：`密码不正确。`
  - 当前密码错误：`当前密码不正确。`
- `StatusMessage` 全站移除“本次操作未保存/已保存/状态未知”和请求编号，只显示错误主信息。
- 同步冲突、日历订阅冲突、账号注销等用户界面移除请求编号；管理后台保留排障编号。
- 帮助中心、隐私页、服务条款移除要求用户保留请求编号/检查垃圾邮件等过时说明。
- 更新仓库 `AGENTS.md` 第 4.7 条：普通用户界面不显示保存状态、请求编号或内部状态。

### 验证

- 真实 API：已存在邮箱 + 错误密码返回 `401 密码不正确。`；不存在邮箱返回 `401 该邮箱未注册。`
- 真实 Chromium：登录页只显示“密码不正确。”，无“本次操作未保存/请求编号/保存状态”，页面错误 0。
- 帮助中心不再出现“请求编号/垃圾邮件”说明。
- `pnpm build`、`ruff` 通过。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `565ba24` | `fix(auth): 错误提示只说真实原因` | 登录错误、StatusMessage、冲突提示、帮助/条款/隐私 |

## M7-AQ：AI 文件附件

用户反馈 AI 输入框不能粘贴文件，只能走课表附件；需要 PDF、Word 等常见格式。

### 实现

- AI 输入框支持点击选择和直接粘贴文件。
- 支持 PDF、DOCX、TXT、MD、CSV；继续支持图片、XLSX、ICS 的课表导入路径。
- 新增 `POST /api/v1/ai/documents/extract`：10 MB 上限，PDF 最多 100 页，提取文字最多 12,000 字符；内存解析，不落盘、不执行文件内容。
- `.doc` 提示另存为 `.docx`；扫描版 PDF 提示先转换为可复制文字的 PDF。
- 提取文字随用户消息进入现有 AI 上下文，原始文件不持久化。

### 验证

- 真实 API：DOCX、PDF、TXT 均提取成功，PDF 文字层可读。
- 真实 Chromium：粘贴 PDF 出现附件 chip；选择 DOCX 后发送，用户消息包含 `附件《ai-sample.docx》` 和 Word 正文；页面错误 0。
- `tests/test_ai_documents.py`：4 项通过。
- `pnpm build`、`ruff` 通过。

### 边界

- 不支持旧版 `.doc`、加密 PDF、扫描版 PDF OCR；
- 超长文档截断到 12,000 字符并标记；
- 附件正文作为会话消息保存，原始文件不保存。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `9ec09a6` | `feat(ai): 支持 PDF/Word 文档附件解析` | 文档解析服务、API、AI 附件 UI、依赖、测试 |

## M7-AR：个人版入口、默认学期与 AI 图片上下文

用户要求删除商业化/过度设计入口，按个人使用收敛，并修正 AI 图片被强制识别为课表的问题。

### 已实现

- 主导航仅课表、任务、AI、日历；`/plans`、`/reviews`、`/me`、`/stats` 重定向。
- 设置改为账号、AI 用量进度条、偏好三块；删除设备、隐私、同步、额度账本明细、质量改进、注销等入口。
- 新建学期使用周数（默认 20），自动计算结束日期；默认 10 节固定节次。
- 无学期时添加课程自动创建当前学期；AI 上下文无学期时也自动创建 20 周默认学期。
- AI 图片改为 OCR 后作为普通上下文，不再自动创建课表候选；AI 上下文注入当前时间/星期/时区。
- 用户明确说“记一下/记住”时写入长期记忆。

### 验证

- Chromium：导航 4 项；设置无隐私/设备/同步；学期表单默认 20 周、10 节；页面错误 0。
- Chromium：AI 粘贴图片得到 OCR 文本，无课表候选卡。
- `pnpm build`、`ruff`、AI 文档测试通过。

### 未完成

- AI 多任务一次生成、重复任务完整调度、联网搜索、AI 主动记忆维护、学期创建工具与默认管理员同入口，尚未完成；不得标记为已实现。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `9c4003c` | `refactor: 收敛个人版入口与默认学期` | 导航、设置、学期/节次、AI 图片与上下文、记忆捕获 |

## M7-AS：AI 批量与重复安排

用户反馈 AI 一次只能添加一个安排，重复活动只写一天。

### 实现

- 新增 `propose_itime_calendar_batch` 工具，一次最多 20 个固定活动。
- 支持 `none`、`daily`、`weekly` 重复，`repeat_until` 或 `repeat_count`。
- 一个提案确认后批量写入日历；提案卡显示“新建 N 项安排”和逐项时间/重复规则。
- 支持模型一次返回多个批量工具调用时合并为同一提案。
- 历史失效提案不再显示“未找到此提案”噪音。

### 验证

- 真实 AI：三项活动一次生成 3 项批量提案。
- 真实 AI：每天 22:00–23:00 学日语到 12 月 20 日生成每日重复提案。
- 真实 API：批量提案 apply 成功，返回 3 个日历 series。
- `ruff`、`pnpm build` 通过。

### 边界

- 批量创建的是日历固定安排，不是任务对象；任务多任务调度仍未完成。
- 搜索、AI 主动记忆仍未完成。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `d60925a` | `feat(ai): 支持批量与重复日历安排提案` | 批量工具、解析、apply、提案卡 |
| `0fd5a84` | `feat(ai): 批量日历提案支持多活动与重复安排` | 失效提案噪音修复 |

## M7-AT：任务批量与重复任务

批量日历工具增加任务对象：

- `create_task=true` 时，每个 item 同时创建任务和任务 schedule；
- 支持任务 `daily` / `weekly` 重复及 `repeat_until`/`repeat_count`；
- 一个提案确认后完成多任务创建。

验证：

- 三项活动一次创建 3 个任务（120/60/30 分钟）。
- 每天 22:00–23:00 学日语到 12 月 20 日创建 `daily` 任务 schedule。
- 真实 Chromium 提案卡“确认全部”成功；页面错误 0。
- `ruff`、`pnpm build` 通过。

边界：本轮仍是“日历 + 任务”同时创建，不是独立的任务批量 API；任务批量前端编辑未单独开放。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `e6bb08c` | `feat(ai): 批量安排可同时创建任务与重复任务` | 任务对象、TaskSchedule、重复规则 |
| `70b377f` | `docs(ai): 记录任务批量与重复任务` | 证据与说明 |

## M7-AU：AI 天气查询

用户问天气时 AI 回答“没有天气数据”。

### 实现

- 检测天气关键词后，服务端读取 `wttr.in` 并注入 `live_weather`；
- 注入当前天气和未来 3 天温度/降雨概率；
- 与当前时间/日期/时区一起提供给 AI；
- 外部服务失败时明确说明不可用，不编造。

### 验证

- 真实 Chromium：问“明天会下雨吗”，回答明天阴天、降雨概率 9%–10%、13–16℃，不再说没有数据。
- `ruff` 通过。

### 边界

- 只做天气，不是通用网页搜索；
- 地点按出口 IP 推断，精确地点需追问城市。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `cae8808` | `feat(ai): 天气查询注入实时天气上下文` | 天气上下文、prompt、验证 |
| `0416f1b` | `docs(ai): 记录天气查询` | 文档与截图 |

## M7-AV：默认管理员与后台入口清理

- 用户登录页增加“管理员登录”入口。
- 新增 `server/scripts/bootstrap_admin.py`，默认创建 `admin@itime.local`，随机密码并写入 `server/.local/admin-bootstrap.json`（gitignored），不覆盖已有账号。
- 管理员登录支持 `.local` 域名；实测返回 `admin_setup_required`，说明账号已创建且等待 TOTP 设置。
- 删除后台的 AI 额度、供应商成本、AI 技术日志、质量样本入口；旧地址重定向到账号安全。
- `ruff`、`pnpm build` 通过。

### 对应提交

| SHA | 主题 | 范围 |
| --- | --- | --- |
| `e77cd28` | `feat(admin): 创建本地默认管理员并支持 .local 管理员邮箱` | bootstrap、管理员登录邮箱 |
| `a92be80` | `refactor(admin): 删除额度成本日志与质量入口` | 后台导航与路由 |
| `c55bf9a` | `docs(admin): 记录后台入口清理` | 文档 |
