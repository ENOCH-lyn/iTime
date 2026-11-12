# iTime 需求追踪矩阵

版本：0.2；更新：2026-10-08。需求编号来自[现行规格](iTime-需求规格说明书.md)，用例来自[测试方案](../测试/iTime-测试方案与测试用例.md)。服务基础、账号、任务/课表、任务与困难语音转写、任务公开链接、日历系列与 ICS 订阅源生命周期、全局 AI、额度、业务提案、日周规划、执行/困难、复盘/记忆、个人统计、本人数据导出、通用同步及账号注销已有分模块动态证据；Element Plus 迁移、Agent 自动意图路由和图片/网页/文字统一课表导入已完成设计，尚未计入实现证据。实际范围见[实施与验证](../验收/iTime-Web实施与验证.md)。

下表的章节指[详细设计](../设计/iTime-详细设计文档.md)，页面对应 [designer](../../designer.md)，阶段对应[开发总计划](../计划/iTime-开发总计划.md)。已确认方向下的剩余细则以[问题清单](iTime-需求访谈问题清单.md)为准。

## 1. 需求到设计、测试与阶段

| 需求 | 已确认范围 | 页面/设计章节 | 用例 | 阶段 |
| --- | --- | --- | --- | --- |
| REQ-WEB-01 | 完整 Web、全局对话入口、整理任务课表 | 公共页/新手/用户端；§1、§12 | UX-01、UX-04、E2E-01 | M2—M8 |
| REQ-APP-01 | Android 首轮已交付，iOS 待开始 | 平台矩阵；§1；android/ | APP-01、APP-02、NOT-01 | M9（Android）/M10（iOS） |
| REQ-AUTH-01 | 邮箱密码、验证找回、开放/邀请制 | 账号；§3 | AUTH-01、AUTH-02 | M2 |
| REQ-AUTH-02 | 不限固定设备数、设备管理和撤销 | 设置/设备；§3、§10 | AUTH-03、SYN-04 | M2 |
| REQ-TASK-01 | 统一任务、层级、语音、文本链接、批量检索 | 任务/目标；[语音转写设计](../设计/iTime-任务与困难语音转写设计.md)；§2、§4 | TASK-01、TASK-03 | M3 |
| REQ-TASK-02 | 步骤顺序、重复、跨日拆分/不可拆 | 任务详情；§4 | TASK-01、TASK-02、PLAN-04 | M3/M5 |
| REQ-CAL-01 | 学期/节次/周次/例外、固定区间 | 课表/日历；§4、§5 | CAL-01、CAL-03 | M3 |
| REQ-CAL-02 | 图片/Excel/ICS/网页/文字/手工的统一导入 | [AI 课表统一导入设计](../设计/iTime-AI课表统一导入设计.md)；§4、§9 | CAL-02、MEDIA-01 | M3/M8 |
| REQ-CAL-03 | 只读来源更新，提示计划冲突 | 来源状态/日历；§4 | CAL-04 | M3/M5 |
| REQ-CHAT-01 | 多会话、Agent 自动意图、相关检索和附件 | [AI Agent 对话编排设计](../设计/iTime-AI%20Agent对话编排设计.md)；§2、§7、§11 | CHAT-01、REV-02 | M4/M6/M8 |
| REQ-CHAT-02 | Agent 业务写操作全部预览确认 | [AI Agent 对话编排设计](../设计/iTime-AI%20Agent对话编排设计.md)；§6 | CHAT-02、CHAT-03 | M4/M5/M8 |
| REQ-LINK-01 | 自动读取任务公开链接 | 对话/任务来源；§9 | LINK-01、LINK-02 | M4 |
| REQ-PLAN-01 | 用户触发日周规划、密度/缓冲/步骤 | 规划预览；§4、§6 | PLAN-01、PLAN-02、PLAN-03、PLAN-04 | M5 |
| REQ-PLAN-02 | 手工纠错、重排预览与版本 | 预览/日历；§6 | PLAN-05、CHAT-03 | M5 |
| REQ-EXEC-01 | 正/倒计时、单一进行中、修订、确认完成 | 专注；§5 | EXEC-01、EXEC-02、EXEC-03、TASK-02 | M5 |
| REQ-DIFF-01 | 一句话困难、可选情绪、主动求助 | 专注/困难；[语音转写设计](../设计/iTime-任务与困难语音转写设计.md)；§2、§11 | DIFF-01、REV-01 | M5 |
| REQ-REVIEW-01 | 单日补做、可跳过、报告批注与版本 | 复盘/历史；§11 | REV-01、REV-02、REV-03 | M6 |
| REQ-MEM-01 | 记忆/偏好确认保存、管理删除 | 记忆/设置；§6、§11 | REV-03、CHAT-02 | M6 |
| REQ-STATS-01 | 计划块/任务/专注分开，日周月 | 个人统计；§5、§11 | MET-01、MET-02 | M6 |
| REQ-SYNC-01 | 约 5 秒目标、安全合并、离线范围 | 同步状态/冲突；§10 | SYN-01、SYN-02、SYN-03、SYN-04、PERF-01 | M2—M6 |
| REQ-TIME-01 | 04:00 活动日，真实日历日期 | 日历/统计/额度；§5、§8 | TIME-01、CAL-03、MET-01、QUOTA-02 | M1—M6 |
| REQ-DATA-01 | 历史保留、30 天回收站 | 数据/回收；§11 | PRIV-03、SYN-02 | M6 |
| REQ-DATA-02 | 注销 7 天可撤销，到期清理 | 账号/数据；§7、§11 | PRIV-03、AI-04、REL-02 | M6 |
| REQ-EXPORT-01 | JSON、CSV、ICS、Markdown，本人下载 | 导出；§11、§12 | PRIV-04、AUTH-02 | M6 |
| REQ-MEDIA-01 | 原媒体确认后/24 小时清理 | 导入/语音；§9、§11 | MEDIA-01 | M3 |
| REQ-PRIV-01 | AI 正文日志默认 7 天 | 后台日志/隐私；§3、§11 | PRIV-01、ADM-03 | M4/M6 |
| REQ-PRIV-02 | 默认质检可关闭、样本 30 天、不训练 | 隐私设置/质检；§11 | PRIV-02、PRIV-03 | M6 |
| REQ-AI-01 | 服务端分能力 AI、后台流式/超时/备用 | 对话/作业；§7 | AI-01、AI-02、AI-03、AI-04、AI-05、AI-06 | M4—M6 |
| REQ-AI-02 | 后台端点/Key、配置测试发布回滚 | 后台 AI；§3、§7 | ADM-02、E2E-02 | M4 |
| REQ-QUOTA-01 | 每日金额、成功一次、失败不扣、估算 | 用量/后台额度；§8 | QUOTA-01、QUOTA-02、QUOTA-03、AI-03 | M4 |
| REQ-ADMIN-01 | 超级管理员 TOTP、用户/额度/AI/日志/审计/质检 | 后台；§3、§12 | ADM-01、ADM-02、ADM-03、E2E-02 | M2—M7 |
| NFR-PERF-01 | 同步/AI 等待目标，数值可配 | 作业/同步；§7、§10；cloud | PERF-01、PERF-02 | M8 |
| NFR-CAP-01 | 小规模基线、200—1000 元/月总预算 | §2、§7、§8；cloud | PERF-01、PERF-02、QUOTA-02 | M8 |
| NFR-REL-01 | 有界重试、每日备份、24 小时恢复 | §7、§10、§11；cloud | REL-01、REL-02 | M8 |
| NFR-SEC-01 | 所有权、Key、TOTP、审计、生命周期 | §3、§9、§11 | AUTH-02、ADM-01、ADM-02、LINK-02、PRIV-03 | M2—M8 |
| NFR-COMP-01 | 全尺寸 Web、Chrome/Edge 优先 | designer 平台矩阵 | UX-01、APP-01、APP-02 | M7—M10 |
| NFR-UX-01 | 温和、主题/字号、Element Plus、精简文案和完整状态 | [Web 组件库迁移与文案设计](../设计/iTime-Web组件库迁移与文案设计.md) | UX-01、UX-02、UX-03 | M7/M8 |

## 2. 平台范围矩阵

| 能力 | Web 用户端首版 | Web 管理后台首版 | Android（首轮已交付）/ iOS（待开始） |
| --- | --- | --- | --- |
| 邮箱账号、设备与本人数据 | 完整提供 | 管理认证及用户管理 | 完整提供 |
| 任务/课表/全部导入、日周规划、执行/困难、复盘 | 完整提供 | 授权正文查看及诊断，不替代用户端 | 完整提供 |
| 全局对话、提案确认、记忆、统计 | 完整提供 | 配置/日志/质检 | 完整提供 |
| 同步、额度、导出、回收、注销、隐私与主题字号 | 完整提供 | 额度/状态/审计/清理管理 | 完整提供 |
| 平台 AI 端点与 Key | 无管理权限 | 草稿、测试、发布、回滚 | 无管理权限 |
| 业务提醒 | 不做 | 不新增用户提醒模块 | 可加，类型/静默/任务覆盖方向已定，渠道后定 |
| 分享、协作、真实支付 | 首版不做 | 首版不做相关运营模块 | 另有新需求时再确定 |

## 3. 分模块实现状态

| 需求 | 当前代码与证据 | 仍未完成 |
| --- | --- | --- |
| REQ-WEB-01 | 公开产品页、演示、帮助、协议隐私和账号入口已实现；登录后默认进入 `web/src/pages/AIPage.vue`，AI、任务、规划、日历、专注、复盘、统计和设置从八项统一产品导航可达，设置内 `/account/data` 提供本人数据导出与账号注销，`/account/sync` 提供同步与离线工作区，`/calendar` 提供日历系列与 ICS 订阅源统一回收站；现有页面已通过生产构建及 Chromium、真实 Edge 154 的 desktop、mobile、tablet 动态测试，`web/e2e/full-user-journey.spec.ts` 还以独立第二端贯通注册、任务、AI 规划确认、日历、专注/困难、明确完成、复盘和统计，并在真实 Chrome 149 desktop 通过；`performance-under-load.spec.ts` 已在 20 用户背景负载下通过 Chromium 三视口和 Edge desktop | 学期、项目、AI 会话删除语义仍待确认且当前无删除旁路；实体设备、正式最低版本矩阵、正式模型质量、M8-B 备份恢复及 M8-C 生产部署未验证 |
| REQ-AUTH-01 | `server/src/itime/auth/`、`admin/management.py`、`mail_worker.py` 及 `web/src/pages/AuthPage.vue`；邮箱账号与开放/邀请注册已执行真实 PostgreSQL/HTTP/Mailpit 和三种视口 Chromium 测试 | 改邮箱/申诉细则；不得将整个需求标为通过 |
| REQ-AUTH-02 | `auth/session.py`、`router.py`、`web/src/components/DeviceSettings.vue`、`web/src/lib/auth.ts`；服务端会话撤销、分页列表、并发授权、真实设备管理页面，以及显式退出或申请注销后的账号级游标、缓存和待传队列清理已动态验证；注销申请即时撤销全部设备，撤销时仅创建一个新会话 | 会话意外失效后的重新认证提示继续单独验证 |
| REQ-TASK-01/02 | `server/src/itime/tasks/`、迁移 `0005_tasks`、`0009_task_schedules`、`0016_task_lifecycle`、`0035_voice_transcriptions` 与 `0036_transcription_quota_usage`、任务工作区、日期编辑器、共享语音控件及统一日历；任务核心、截止/重复/可拆参数、实例完成/跳过/调期/关联补做、未来拆分、手动归档、30 天任务回收站和任务名称语音校对均有可达 Web 入口，并通过真实 HTTP/PostgreSQL、AI worker、Chromium 和真实 Edge 154 三视口测试 | 多任务/属性语音解析不属于本模块；真机未验证 |
| REQ-DATA-01 | 迁移 `0016_task_lifecycle`、`0025_execution_difficulties`、`0026_review_memory` 与 `0034_calendar_lifecycle`，四类独立清理 worker 及对应 Web 工作区已实现任务、困难、长期记忆、日历系列和 ICS 订阅源的删除、30 天恢复、到期清理和可达回收入口；任务与日历对象保留最小墓碑，记忆删除后立即退出 AI 检索，日历删除不改写既有计划/执行/复盘事实；专项覆盖双 worker、旧操作与迟到刷新阻断、导出/同步、并发 UI 冲突及 320px 大字号；M8-A 在普通负载下清理任务/日历/困难/记忆各 100 条及同步事件 1,000 条共用 0.472 s | 学期、节次、项目、AI 会话等其他独立对象尚未开放单项删除或回收；账号注销单列 `REQ-DATA-02`，备份删除重放仍待 M8-B |
| REQ-DATA-02 | 迁移 `0029_account_deletions`、`server/src/itime/account_deletion/`、`POST /api/v1/account-deletion`、`POST /cancel`、独立清理 worker、`/account/data` 危险区和登录恢复页已实现 7 天撤销、会话冻结、在途任务停止、到期单事务清理及最小回执；专项覆盖密码/邮箱/CSRF、并发、晚到结果、双 worker、回滚、全部 owner 表、跨标签本地清理、三视口和同邮箱新 UUID | 生产备份恢复后的删除回执重放、法务保留及其余对象单项回收仍待 M8-B 或后续模块验证 |
| REQ-CAL-01 / REQ-CAL-03 / REQ-TIME-01 | `server/src/itime/calendar/`、`server/src/itime/planning/`、`server/src/itime/statistics/`、迁移 `0006`—`0015`、`0022`—`0024`、`0031_plan_source_conflicts`、`0032_subscription_exceptions` 与 `0034_calendar_lifecycle`、日历及统计页面；时区、学期节次、课程/固定安排、六类重复、周/月视图、DST、冲突、例外、全部导入、日历系列 30 天回收恢复、订阅 occurrence 单次取消/移动/内容覆盖/重置、当前计划块投影、来源更新后的已确认计划复核/实际重叠/保留或重排处理，以及额度和统计的 04:00 活动日已通过真实 HTTP/PostgreSQL、worker、Chromium 和真实 Edge 154 三视口测试；删除日历对象不改写既有计划/执行/复盘，刷新失败或仅非调度字段变化不制造计划冲突 | 学期不提供独立删除；真机未验证 |
| REQ-CAL-02 | `server/src/itime/calendar/import_*.py`、`screenshot_*.py`、`subscription_*.py`、`lifecycle_service.py`、`server/src/itime/ai/`、迁移 `0011`—`0015` 与 `0034_calendar_lifecycle`、`web/src/components/CalendarImport*.vue`、`CalendarScreenshotImport.vue`、`CalendarSubscriptionManager.vue` 与 `CalendarTrashDialog.vue`；已实现 XLSX/ICS 文件、截图和 ICS URL 订阅的创建、刷新、失败保旧、暂停、30 天删除恢复、字段覆盖、差异与统一日历投影 Web 流程 | AI 会话入口、公开网页和文字课表尚未实现；真实第三方私密订阅源兼容性仍需部署前联调 |
| REQ-MEDIA-01 | `TemporaryMedia` 已同时约束截图和规范 PCM WAV；截图确认/跳过与转写应用/丢弃后立即清理，失败/取消清理无恢复价值媒体，未解决媒体固定 24 小时到期；账号注销优先清理。真实测试覆盖格式边界、清理竞争、迟到结果、导出排除原媒体及浏览器不持久化音频 | 正式对象存储迁移与供应商侧保留规则待部署确定 |
| REQ-CHAT-01 / REQ-AI-01 | 迁移 `0017_ai_conversations`、`0022`—`0024`、`0026_review_memory`、`0030_public_task_links`、`0033_ai_fallback_routes`、`0035_voice_transcriptions` 与 `0036_transcription_quota_usage`、`server/src/itime/ai/`、通用 worker/SSE 路由及用户端 AI/语音入口已实现多普通会话、相关历史和公开资料不可变快照、消息持久化、文本流、语音转写、租约续期、重试 reset、取消、离页/断线恢复，以及与普通会话分离的单日复盘会话；五类能力新作业会冻结主配置和最多两个备用配置的尝试顺序，可重试故障切换但不重置总截止时间，非重试错误不切换 | 自动意图、工具循环、通用提案和会话附件尚未实现；正式模型质量评测仍待后续模块 |
| REQ-LINK-01 | 迁移 `0030_public_task_links`、`server/src/itime/network/restricted_http.py`、`server/src/itime/public_links/`、任务链接刷新 API、任务来源块及 `web/e2e/public-links.spec.ts` 已实现新增/URL 变化自动排队、受限 HTTPS 读取、确定性摘要、失败保旧、手动刷新、账号级并发/频率限制、同步失效、规划和普通对话不可变不可信上下文，以及本人导出/任务清理/注销联动；已通过真实 PostgreSQL/Uvicorn/受控 HTTP/worker、Chromium 和真实 Edge 154 三视口测试 | 需登录或 JavaScript 渲染页面不在首版；正式公网兼容性、生产出口、正式模型质量和真机仍未验证 |
| REQ-CHAT-02 / REQ-PLAN-01 / REQ-PLAN-02 | `server/src/itime/planning/`、迁移 `0022`—`0024`、`web/src/pages/PlansPage.vue` 及规划组件已实现一致输入快照、日/周 AI 提案、确定性硬约束校验、确认/拒绝、过期与版本冲突、不可变周版本、手工时间/步骤/顺序纠错、历史查看和日历投影；已通过真实 PostgreSQL/HTTP/双 worker、额度终态、Chromium 和真实 Edge 154 三视口测试 | 对话仍需手动选择规划模式，任务/日历/专注/复盘/记忆的通用 Agent 提案尚未实现；正式模型规划质量和真机仍未验证 |
| REQ-EXEC-01 / REQ-DIFF-01 | 迁移 `0025_execution_difficulties`、`0035_voice_transcriptions` 与 `0036_transcription_quota_usage`、`server/src/itime/{execution,ai}/`、`web/src/pages/FocusPage.vue`、`DifficultyPanel.vue`、共享语音控件和账号/会话隔离离线队列已实现自由/任务/计划块正倒计时、单一开放专注、暂停恢复结束、步骤进度、明确任务完成、补记修订、困难增删改及 30 天恢复、困难正文语音校对、主动带到 AI 草稿；语音不推断分类/情绪且应用后仍须保存困难；已覆盖双连接并发、未知响应、主备故障、取消晚到、顺序重放、终态冲突、队列上限、Chromium 和真实 Edge 154 三视口 | 正式模型质量和真机仍未验证 |
| REQ-REVIEW-01 / REQ-MEM-01 | 迁移 `0026_review_memory`、`server/src/itime/review/`、通用 AI worker 的 `review` 能力、`web/src/pages/ReviewsPage.vue` 和复盘组件已实现 04:00 单日事实快照、最多三轮可跳过追问、离页恢复、结构化报告、不可变版本、独立批注、记忆候选确认/拒绝、人工记忆管理、整体暂停及 30 天回收恢复；普通对话与规划在调用时重新校验有效记忆，并通过 Chromium 和真实 Edge 154 三视口测试 | 正式模型复盘质量和真机仍未验证 |
| REQ-STATS-01 | `server/src/itime/statistics/`、`web/src/pages/StatisticsPage.vue`、统计组件/API 及 `web/e2e/statistics.spec.ts` 已实现日/自然周/自然月查询、当前计划版本、任务完成/跳过、专注完成/放弃/补记/修订、跨 04:00 分配、空态和读取失败保旧；已通过真实 PostgreSQL/HTTP、活动日数学、Chromium、真实 Edge 154 三视口及 320px 大字号验证；M8-A 重历史和 60 分钟耐久场景持续读取统计，均无失败 | 后台运营指标和真机不属于本模块或仍未验证 |
| REQ-SYNC-01 | 迁移 `0028_sync_feed`、`server/src/itime/sync/`、`web/src/lib/{data-cache,sync,sync-queue,use-sync-refresh}.ts`、`/account/sync` 和 `web/e2e/sync.spec.ts` 已实现账号绑定 HMAC 游标、无正文失效事件、30 天有界清理、跨标签/跨设备刷新、当前已认证页面的 AES-GCM 离线缓存、普通/重复任务步骤与既有专注操作的类型化队列、冲突保留及退出清理；已覆盖游标篡改/过期/超前、账号隔离、清理竞争、幂等重放、跨设备 5 秒上界、断网恢复、Chromium、真实 Edge 154 三视口和 320px 深色大字号；M8-A 另覆盖标准/重历史读取、10—100 用户阶梯和 20 用户负载下跨端 5 秒上界 | 离线新建、离线冷启动不属于已确认首版范围；公网、生产候选拓扑和真机仍未验证 |
| REQ-EXPORT-01 | 迁移 `0027_data_exports`、`server/src/itime/data_export/`、`POST/GET /api/v1/data-exports`、独立 worker、`web/src/components/DataExportSettings.vue`、`/account/data` 及 `web/e2e/data-exports.spec.ts` 已实现本人一致性快照、完整 JSON、分类 CSV/ICS/Markdown、加密 ZIP、24 小时认证下载和 30 天终态元数据；专项真实下载并解包核对清单摘要、代表数据及敏感字段排除，覆盖双 worker、租约恢复、所有权、会话撤销、过期/损坏、Chromium、真实 Edge 154 三视口和 320px 大字号 | 首版导出范围已完成；长历史容量、实体设备和生产密钥轮换仍未验证 |
| REQ-PRIV-01 / REQ-PRIV-02 | 迁移 `0018_ai_governance`、逐尝试正文日志、成功终态采样、用户隐私设置、独立 7/30 天清理进程及用户/后台页面已实现；专项覆盖关闭与终态竞态、重试/取消/超时、截图 base64 排除、双清理、显式正文访问、访问审计及账号注销优先清理 | 未来单项彻底删除的跨聚合规则随对应数据模块完成 |
| REQ-QUOTA-01 | 迁移 `0019`—`0021`、`0033_ai_fallback_routes`、`0035_voice_transcriptions` 与 `0036_transcription_quota_usage`、`server/src/itime/{ai,quota}/` 及截图、转写、对话、规划、复盘五类 AI 接单/终态事务已实现 Token/音频分钟价格、usage、逐尝试实际配置成本、04:00 活动日、按整条主备路由最大估算原子预留、成功 attempt 一次结算、失败/取消释放和过期恢复；用户 `/account/quota`、后台 `/admin/quota` 与 `/admin/costs` 已通过真实 PostgreSQL/HTTP/worker 和三视口 Chromium 测试 | 后续 AI 业务能力继续复用同一额度合同；充值、支付和现金钱包不属于首版 |
| REQ-ADMIN-01 / REQ-AI-02 | 普通注册/登录拒绝管理旁路；`server/src/itime/admin/`、AI 配置/主备路由与治理接口、`server/src/itime/quota/admin_router.py`、`web/src/pages/Admin*.vue` 及管理组件已实现并动态验证 TOTP、恢复码、独立会话、注册邀请、普通用户/AI 状态、额度策略与调整、带 Token/音频分钟价格的 AI 配置、转写合成音频测试、每能力最多两个备用的顺序/并发恢复/提升为主、供应商成本、日志、质检、正文访问和审计查询；Key 不回显 | 最终恢复/交接规则随部署准备继续细化 |
| NFR-PERF-01 | `server/performance/`、`compose.performance.yaml`、结构化报告和 `web/e2e/performance-under-load.spec.ts` 已在 2 vCPU / 4 GiB 下完成稳态、峰值、重历史、AI 隔离、队列、维护、60 分钟耐久和负载 UI；50 用户最低门禁通过，100 用户为测试上限内最高稳定档，20 个 AI 作业领取 P95 2.741 s、端到端 P99 3.165 s | 本地受控上游结果不代表公网或正式模型；M8-C 需在生产候选拓扑重测并冻结 SLO |
| NFR-CAP-01 | 标准/重历史固定数据集、10—100 用户阶梯、理论连接预算 20、三类各 50 条队列及资源门禁已实测；全部最终场景无 5xx、池超时、超过 2 秒锁等待或不变量异常，最高连接 17/50 | 未观察到 100 用户以上容量边界；真实流量模型、服务器/数据库价格、AI/邮件/存储/流量和备份月度总成本待 M8-C |
| NFR-REL-01 | 有界重试、租约、重复投递、维护批次和 60 分钟耐久已有模块及 M8-A 动态证据 | 每日备份、实际 RPO/RTO、24 小时恢复及删除台账重放待 M8-B，不能由性能基线代替 |

| REQ-APP-01 / 02 / 03 | `android/`：Kotlin 2.0 + Compose M3 客户端；Room + outbox 同步引擎（`data/sync/`）、任务/日历/AI 仓库（离线优先）、今天/日历/任务/AI/设置页面与宽屏自适应；服务端新增 `GET /api/v1/sync/snapshot` 快照与任务实例列表；模拟器主流程验证（登录、建任务、离线建项目后补传、冲突保留本地/云端、重复任务按次完成、AI 提案批准、系统分享建任务、回退前台同步）；2026-11-12 复核 JVM 单测 30、仪器测试 33 通过，`gradle clean :app:assembleDebug` 成功 | 真机与实体设备、TalkBack 逐条朗读、提醒/通知、iOS 客户端、离线冷启动以外的弱网矩阵未验证；Android 文档在任务工作区 `docs/39`、`docs/40`，未随本仓库发布 |

只记录实际代码和执行证据，不用测试编号表示已通过。
