# iTime 需求追踪矩阵

版本：0.2；更新：2026-10-03。需求编号来自[现行规格](iTime-需求规格说明书.md)，用例来自[测试方案](../测试/iTime-测试方案与测试用例.md)。服务基础、账号、任务/课表、全局 AI、额度、业务提案和日周规划已有分模块动态证据；完整 Web 闭环尚未验收，实际范围见[实施与验证](../验收/iTime-Web实施与验证.md)。

下表的章节指[详细设计](../设计/iTime-详细设计文档.md)，页面对应 [designer](../../designer.md)，阶段对应[开发总计划](../计划/iTime-开发总计划.md)。已确认方向下的剩余细则以[问题清单](iTime-需求访谈问题清单.md)为准。

## 1. 需求到设计、测试与阶段

| 需求 | 已确认范围 | 页面/设计章节 | 用例 | 阶段 |
| --- | --- | --- | --- | --- |
| REQ-WEB-01 | 完整 Web、全局对话入口、整理任务课表 | 公共页/新手/用户端；§1、§12 | UX-01、UX-04、E2E-01 | M2—M8 |
| REQ-APP-01 | Android 后 iOS，完整用户功能 | 平台矩阵；§1 | APP-01、APP-02、NOT-01 | M9—M10 |
| REQ-AUTH-01 | 邮箱密码、验证找回、开放/邀请制 | 账号；§3 | AUTH-01、AUTH-02 | M2 |
| REQ-AUTH-02 | 不限固定设备数、设备管理和撤销 | 设置/设备；§3、§10 | AUTH-03、SYN-04 | M2 |
| REQ-TASK-01 | 统一任务、层级、语音、文本链接、批量检索 | 任务/目标；§2、§4 | TASK-01、TASK-03 | M3 |
| REQ-TASK-02 | 步骤顺序、重复、跨日拆分/不可拆 | 任务详情；§4 | TASK-01、TASK-02、PLAN-04 | M3/M5 |
| REQ-CAL-01 | 学期/节次/周次/例外、固定区间 | 课表/日历；§4、§5 | CAL-01、CAL-03 | M3 |
| REQ-CAL-02 | 截图/Excel/手工/ICS 与订阅 | 导入/校对；§4、§9 | CAL-02、MEDIA-01 | M3 |
| REQ-CAL-03 | 只读来源更新，提示计划冲突 | 来源状态/日历；§4 | CAL-04 | M3/M5 |
| REQ-CHAT-01 | 多普通会话、复盘独立、相关检索 | 全局对话；§2、§7、§11 | CHAT-01、REV-02 | M4/M6 |
| REQ-CHAT-02 | 助手业务写操作全部预览确认 | 提案差异；§6 | CHAT-02、CHAT-03 | M4/M5 |
| REQ-LINK-01 | 自动读取任务公开链接 | 对话/任务来源；§9 | LINK-01、LINK-02 | M4 |
| REQ-PLAN-01 | 用户触发日周规划、密度/缓冲/步骤 | 规划预览；§4、§6 | PLAN-01、PLAN-02、PLAN-03、PLAN-04 | M5 |
| REQ-PLAN-02 | 手工纠错、重排预览与版本 | 预览/日历；§6 | PLAN-05、CHAT-03 | M5 |
| REQ-EXEC-01 | 正/倒计时、单一进行中、修订、确认完成 | 专注；§5 | EXEC-01、EXEC-02、EXEC-03、TASK-02 | M5 |
| REQ-DIFF-01 | 一句话困难、可选情绪、主动求助 | 专注/困难；§2、§11 | DIFF-01、REV-01 | M5 |
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
| NFR-UX-01 | 温和、主题/字号、日周月、完整状态 | designer 交互合同 | UX-01、UX-02、UX-03 | M7 |

## 2. 平台范围矩阵

| 能力 | Web 用户端首版 | Web 管理后台首版 | Android/iOS 后续 |
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
| REQ-WEB-01 | 公开产品页、演示、帮助、协议隐私和账号入口已实现；登录后默认进入 `web/src/pages/AIPage.vue`，AI、任务、规划、日历、专注和设置从六项统一产品导航可达；现有页面已通过生产构建及桌面、平板、手机、320px 大字号 Chromium 动态测试 | 复盘、记忆、统计和数据管理页面仍待后续模块；Edge/真机未验证 |
| REQ-AUTH-01 | `server/src/itime/auth/`、`admin/management.py`、`mail_worker.py` 及 `web/src/pages/AuthPage.vue`；邮箱账号与开放/邀请注册已执行真实 PostgreSQL/HTTP/Mailpit 和三种视口 Chromium 测试 | 改邮箱/申诉细则；不得将整个需求标为通过 |
| REQ-AUTH-02 | `auth/session.py`、`router.py`、`web/src/components/DeviceSettings.vue`；服务端会话撤销、分页列表、并发授权及真实设备管理页面 | 后续业务离线队列与切账号本地数据清理 |
| REQ-TASK-01/02 | `server/src/itime/tasks/`、迁移 `0005_tasks`、`0009_task_schedules` 与 `0016_task_lifecycle`、任务工作区、日期编辑器及统一日历；任务核心、截止/重复/可拆参数、实例完成/跳过/调期、未来拆分、手动归档及 30 天任务回收站已通过真实 HTTP/PostgreSQL 和三视口 Chromium 测试 | 任务实例补做 UI 及语音/AI 校对仍待后续模块 |
| REQ-DATA-01 | `0016_task_lifecycle`、`tasks/lifecycle_service.py`、独立清理 worker 及任务工作区已实现任务删除、30 天恢复、普通写入阻断、到期脱敏、最小墓碑和可达回收站 UI；专项覆盖双 worker、旧操作响应清理、重复任务前序外键、并发 UI 冲突和 320px 大字号 | 当前只覆盖任务聚合；其他业务对象及跨聚合关联仍待实现 |
| REQ-CAL-01 / REQ-CAL-03 / REQ-TIME-01 | `server/src/itime/calendar/`、`server/src/itime/planning/`、迁移 `0006`—`0015` 与 `0022`—`0024`、`web/src/pages/CalendarPage.vue` 及日历组件；时区、学期节次、课程/固定安排、六类重复、周/月视图、DST、冲突、例外、全部导入、当前计划块投影及 04:00 额度活动日已通过真实 HTTP/PostgreSQL 和三视口 Chromium 测试；规划输入变化会使待确认提案冲突失效 | 订阅 occurrence 的用户单次例外、来源更新后已确认计划的专门冲突提示及活动日统计应用尚未实现 |
| REQ-CAL-02 | `server/src/itime/calendar/import_*.py`、`screenshot_*.py`、`subscription_*.py`、`server/src/itime/ai/`、迁移 `0011`—`0015`、`web/src/components/CalendarImport*.vue`、`CalendarScreenshotImport.vue` 与 `CalendarSubscriptionManager.vue`；已实现 XLSX/ICS 文件、截图和 ICS URL 订阅的创建、刷新、失败保旧、暂停恢复、字段覆盖、差异与统一日历投影 Web 流程 | 已完成首版可达流程；真实第三方私密订阅源兼容性仍需部署前联调 |
| REQ-CHAT-01 / REQ-AI-01 | 迁移 `0017_ai_conversations` 与 `0022`—`0024`、`server/src/itime/ai/conversation_*.py`、通用 worker/SSE 路由及 `web/src/pages/AIPage.vue` 已实现多普通会话、相关历史快照、消息持久化、文本流、租约续期、重试 reset、取消、离页/断线恢复，以及对话规划模式和提案卡；文字与结构化工具参数分离，接单已接入用户额度预留及终态结算/释放 | 复盘独立会话、备用服务路由、公开链接及正式模型质量评测仍待后续模块 |
| REQ-CHAT-02 / REQ-PLAN-01 / REQ-PLAN-02 | `server/src/itime/planning/`、迁移 `0022`—`0024`、`web/src/pages/PlansPage.vue` 及规划组件已实现一致输入快照、日/周 AI 提案、确定性硬约束校验、确认/拒绝、过期与版本冲突、不可变周版本、手工时间/步骤/顺序纠错、历史查看和日历投影；已通过真实 PostgreSQL/HTTP/双 worker、额度终态及三视口 Chromium 测试 | 首版规划范围已完成；局部接受、锁定单块、任意历史恢复不在本阶段，正式模型规划质量、Edge 和真机仍未验证 |
| REQ-EXEC-01 / REQ-DIFF-01 | 迁移 `0025_execution_difficulties`、`server/src/itime/execution/`、`web/src/pages/FocusPage.vue`、`DifficultyPanel.vue` 和账号/会话隔离离线队列已实现自由/任务/计划块正倒计时、单一开放专注、暂停恢复结束、步骤进度、明确任务完成、补记修订、困难增删改及 30 天恢复、主动带到 AI 草稿；已覆盖双连接并发、未知响应、顺序重放、终态冲突、队列上限与三视口 Chromium | 困难语音校对、跨活动日统计、通用跨业务离线同步、Edge 和真机仍未实现或验证 |
| REQ-PRIV-01 / REQ-PRIV-02 | 迁移 `0018_ai_governance`、逐尝试正文日志、成功终态采样、用户隐私设置、独立 7/30 天清理进程及用户/后台页面已实现；专项覆盖关闭与终态竞态、重试/取消/超时、截图 base64 排除、双清理、显式正文访问和访问审计 | 账号注销优先清理及未来单项彻底删除的跨聚合规则随对应数据模块完成 |
| REQ-QUOTA-01 | 迁移 `0019`—`0021`、`server/src/itime/{ai,quota}/` 及两类 AI 接单/终态事务已实现价格、usage、逐尝试成本、04:00 活动日、原子预留、成功一次结算、失败/取消释放和过期恢复；用户 `/account/quota` 与后台 `/admin/quota` 已通过真实 PostgreSQL/HTTP/worker 和三视口 Chromium 测试 | 后续 AI 业务能力继续复用同一额度合同；充值、支付和现金钱包不属于首版 |
| REQ-ADMIN-01 | 普通注册/登录拒绝管理旁路；`server/src/itime/admin/`、`server/src/itime/ai/admin_*.py`、`server/src/itime/quota/admin_router.py`、`web/src/pages/Admin*.vue` 及管理组件已实现并动态验证 TOTP、恢复码、独立会话、注册邀请、普通用户/AI 状态、额度策略与调整、带价格 AI 配置、供应商成本、日志、质检、正文访问和审计查询 | 最终恢复/交接规则随部署准备继续细化 |

只记录实际代码和执行证据，不用测试编号表示已通过。
