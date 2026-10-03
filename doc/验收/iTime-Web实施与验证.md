# iTime Web 实施与验证

更新：2026-10-04。此文件记录已落地模块和真实验证，不替代[全量验收清单](iTime-产品验收清单.md)。用户要求完整 Web 用户端与后台、逐模块提交、详细服务及真实 UI 测试。

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
- 生命周期表逐项区分“产品规则”和“当前状态”：识别原图清理和任务回收站已实现；M4-C 后又将 7 天 AI 正文日志、30 天质检样本更新为已实现。日历对象回收站和注销清理仍标为待实现，不把需求目标冒充现状。
- 隐私表格在宽屏保持结构化表格；600px 以下转换为带字段标签的完整列表，不把“当前状态”藏在默认视口之外。所有公开页脚只链接真实路由。

2026-10-02 执行：

| 命令 | 实际结果 | 范围 |
| --- | --- | --- |
| `cd web && pnpm build` | 通过 | Vue TypeScript 检查与生产构建 |
| `cd web && pnpm exec playwright test e2e/public.spec.ts` | **9 passed in 14.4s** | 五类公共页面 × 1440×1000、390×844、768×1024，另含首页、演示、隐私 320×700 最大字号 |
| `cd web && pnpm test:e2e` | **105 passed in 4.2m** | 账号、后台、任务、日历、导入、订阅和全部公开页面 Chromium 回归 |

浏览器流程真实访问帮助锚点、协议和隐私路由，核对 7 天日志、30 天样本及“已实现/待实现”状态；M6-B 后更新任务回收站状态，M4-C 后更新正文日志和质量样本状态。三个视口均验证 axe、控制台、API 500 和根级横向溢出；修复了可滚动表格键盘焦点，并将手机表格进一步改为完整标签列表。截图位于 `/tmp/itime-ui-evidence/{desktop,mobile,tablet}-public-privacy.png` 与 `desktop-public-privacy-320-large-font.png`，已人工检查。正式运营主体/供应商/地区仍是发布前配置与披露项；真实 Edge 和实体设备仍未验证。

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

Browser 插件工具在本会话未暴露，因此按前端测试规范使用仓库 Playwright Chromium。真实 Edge、实体设备、日历等其他业务对象回收站及跨聚合删除仍待后续模块。

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

Browser 插件工具未在当前执行环境暴露，因此按前端测试规范使用仓库 Playwright Chromium。金额额度后续已在 M4-E 验证，规划业务提案后续已在 M5-A/M5-B 接入同一治理链路；真实 Edge、实体设备、账号注销优先清理、单项彻底删除联动、其他业务提案及正式第三方模型质量仍未验证或实现。

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

专项覆盖迁移往返、复合所有权、历史不可变、用户级联删除、上海/纽约 DST、超过 100 候选、共享/排他锁、随机边界、双 worker 租约、重试、取消竞态、过期恢复、跨 capability 并发、额度结算及事务回滚。正式第三方模型规划质量、备用服务路由、性能和生产部署仍未验证。

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

Browser 插件未在当前执行环境暴露，因此按前端测试规范使用仓库 Playwright Chromium。复盘与记忆、个人统计、本人数据导出和通用同步后续分别由 M6-C、M6-D、M6-E、M6-F 实现；困难语音、注销、真实 Edge/实体设备、性能和生产部署仍未实现或验证。

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

Browser 插件未在当前执行环境暴露，因此按前端测试规范使用仓库 Playwright Chromium。个人统计、本人数据导出和通用同步后续分别由 M6-D、M6-E、M6-F 实现；困难语音、注销、真实 Edge/实体设备、正式第三方模型质量、性能和生产部署仍未实现或验证。

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

Browser 插件未在当前执行环境暴露，因此按前端测试规范使用仓库 Playwright Chromium。通用同步随后已由 M6-F 实现；完成本模块不代表账号注销、其他对象回收、真实 Edge/实体设备、长历史性能、生产密钥轮换、备份恢复或部署已经完成。

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

Browser 插件未在当前执行环境暴露，因此按前端测试规范使用仓库 Playwright Chromium。完成本
模块不代表账号注销、其他对象回收、困难语音、公开链接、其他业务对象提案、正式模型质量、
M8 性能分位数、真实 Edge/实体设备、备份恢复或生产部署已经完成。
