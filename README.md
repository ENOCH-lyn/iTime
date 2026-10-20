# iTime

iTime 是面向个人的 AI 时间规划与复盘产品。学生优先，兼容通用个人场景，通过任务与课表整理、日周规划、执行记录和复盘形成完整闭环。

**当前正在开发完整 Web 首版。服务运行基础、公开产品首页/演示/帮助/协议隐私、账号与后台用户管理、目标/项目与任务、任务名称与困难正文语音转写、任务公开链接读取、任务归档及 30 天回收站、课表与统一日历、全部日历导入、日历系列与 ICS 订阅源 30 天回收站、订阅 occurrence 单次例外、订阅来源更新后的已确认计划冲突处理、Chat-first 全局 AI 多会话和安全 Markdown、可恢复文本流、AI 能力级主备路由与作业级故障切换、AI 正文日志/质检/访问审计、供应商成本对账、用户每日额度、业务提案与日周规划、专注执行和困难记录、单日复盘与确认式长期记忆、个人日周月统计、统一设置、本人完整数据导出、账号隔离的通用同步，以及 7 天可撤销的账号注销已经完成。全部现有 Web 页面已通过 Chromium 和真实 Microsoft Edge 154 的电脑、手机、平板视口回归，完整核心用户闭环另在真实 Google Chrome 149 desktop 通过；M8-A 已在固定 2 vCPU / 4 GiB 隔离环境完成普通 API、长历史、AI/队列/维护隔离、60 分钟耐久、10—100 并发阶梯和负载下 Chromium/Edge 验证。学期、项目、AI 会话删除语义仍待确认且当前无删除旁路，M8-B 备份恢复、M8-C 生产发布、正式模型质量和实体设备仍未完成，因此尚未达到产品交付标准。** 实际证据见[实施与验证](doc/验收/iTime-Web实施与验证.md)。

2026-10-20：登录后的首页重构为**课表工作台**（`/timetable`），把周课表、今日任务和 AI 入口放在同一屏，任务可在首页快建并设置周期；设计见[课表工作台设计](doc/设计/iTime-课表工作台设计.md)。

## 产品范围

- 完整 Web 用户端及管理后台先发，再 Android、iOS；后端 Python、前端 Vue。
- 全局 AI 对话为主入口，保留完整任务、课表、日历、规划、专注、复盘、统计和数据管理页面。
- 邮箱账号、服务端存储与同步、服务端 AI；所有 AI 业务变更先预览确认。
- 先免费运营，实行每日 AI 金额额度；后台管理用户、额度、AI 配置、日志、审计和质检。
- Web 适配电脑、平板、手机，优先验收 Chrome/Edge；温和轻松，支持明暗主题及字号。
- Web 首版不包含业务提醒、分享、协作、真实支付；App 保留完整功能目标，提醒在 App 阶段细化。

## 文档入口

1. [需求规格](doc/需求/iTime-需求规格说明书.md)：当前产品基线、功能边界与未定细节。
2. [文档索引](doc/README.md)：架构、设计、界面、计划、测试与验收。
3. [需求追踪](doc/需求/iTime-需求追踪矩阵.md)：功能到设计、用例及交付阶段。
4. [AI 协作规范](AGENTS.md)：事实来源、工程约束与完成标准。

原始 PRD 保留为输入档案，开发以现行规格为准；技术推荐和待定配置不视为已实现。

## 本地开发

需要 Python 3.13（由 uv 管理）、uv、Node.js 24、pnpm 11 和 Docker Compose。依赖锁定在 `server/uv.lock`、`web/pnpm-lock.yaml`，容器固定镜像摘要。

```bash
python3 scripts/bootstrap.py
docker compose --env-file .env.local up -d --wait
cd server
uv sync --frozen
uv run alembic upgrade head
uv run uvicorn itime.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

另开终端，在 `server/` 执行 `uv run python -m itime.mail_worker` 启动邮件进程。账号接口把验证/找回邮件写入数据库队列，发送进程中断后可恢复。仅运行 API 不会发送队列中的邮件。

口述课表使用本地 Vosk 中文模型：在 `server/` 运行 `powershell -ExecutionPolicy Bypass -File scripts/download-vosk-model.ps1` 下载模型（约 42 MiB，目录已忽略），并确保 `ffmpeg` 在 PATH；也可用 `ITIME_VOSK_MODEL_PATH` 指定模型目录。音频在 API 进程内转换和识别，不发送到外部语音服务。

截图识别、语音转写、全局对话、日周规划和复盘还需要另开终端，在 `server/` 执行 `uv run python -m itime.ai.worker` 启动 AI 作业进程。管理员须按需创建、测试并发布 `calendar_screenshot`、`transcription`、`conversation`、`planning`、`review` 能力配置，并可为每项能力设置最多两个已测试备用版本；新作业冻结三次尝试的主备顺序，只有可重试故障才切换，且不重置总截止时间。转写按服务端校验的音频分钟计价，其他能力按 Token 计价。未发布对应配置时服务不会接收该能力的新作业。开发环境未设置独立 AI 加密密钥时暂回退到基础加密密钥，生产必须配置不同的 `ITIME_AI_ENCRYPTION_KEY`。

ICS URL 订阅由独立刷新进程执行；在 `server/` 另开终端运行 `uv run python -m itime.calendar.subscription_worker`。生产必须配置独立的 `ITIME_CALENDAR_SUBSCRIPTION_ENCRYPTION_KEY`，用于加密可能包含访问令牌的订阅 URL；API 与日志只显示主机和省略敏感路径、查询参数后的地址。

任务公开链接由独立受限抓取进程执行；在 `server/` 另开终端运行 `uv run python -m itime.public_links.worker`。新增链接、URL 变化或本人手动刷新后会异步读取公开 HTTPS HTML/XHTML/纯文本，任务页显示状态、来源、摘要和失败恢复；进程不使用浏览器 Cookie、平台凭据或系统代理。`--once` 可处理当前一个作业并退出。

任务回收站到期清理由独立进程执行；在 `server/` 运行 `uv run python -m itime.tasks.cleanup_worker`。它每批锁定最多 100 条到期任务，清空正文和直接关联明细并保留阻止旧操作复活的最小墓碑；`--once` 可供受控定时任务和运维检查使用。

日历回收站到期清理由独立进程执行；在 `server/` 运行 `uv run python -m itime.calendar.cleanup_worker`。它分别限批锁定到期日历系列和 ICS 订阅源，清除名称、地点、订阅 URL、来源事件及直接明细并保留最小墓碑；`--once` 可供受控定时任务和运维检查使用。

AI 技术正文日志和质量样本到期清理由独立进程执行；在 `server/` 运行 `uv run python -m itime.ai.retention_worker`。正文日志默认 7 天、质量样本默认 30 天，两类数据分别限批清理；`--once` 可供受控定时任务和运维检查使用。

困难回收站到期清理由独立进程执行；在 `server/` 运行 `uv run python -m itime.execution.cleanup_worker`。它每批锁定并清理最多 100 条已删除满 30 天的困难记录；`--once` 可供受控定时任务和运维检查使用。

长期记忆回收站到期清理由独立进程执行；在 `server/` 运行 `uv run python -m itime.review.cleanup_worker`。删除后记忆立即退出 AI 检索，并在回收站保留 30 天；`--once` 可供受控定时任务和运维检查使用。

本人数据导出由独立进程生成和清理；在 `server/` 运行 `uv run python -m itime.data_export.worker`。作业在一致性只读快照中读取本人数据，生成完整 JSON 和分类 CSV/ICS/Markdown ZIP；产物加密保存并默认可下载 24 小时，终态元数据默认保留 30 天。生产必须配置与其他密钥不同的 `ITIME_EXPORT_ENCRYPTION_KEY`；`--once` 可处理当前批次并退出。

通用同步变更事件由独立进程清理；在 `server/` 运行 `uv run python -m itime.sync.cleanup_worker`。事件默认保留 30 天，清理时按账号推进游标水位，不删除业务对象、操作去重记录或墓碑；`--once` 可执行一个受控清理批次并退出。

账号注销到期清理由独立进程执行；在 `server/` 运行 `uv run python -m itime.account_deletion.worker`。用户申请后立即退出全部设备并停止账号在途工作，7 天内可用正确凭据撤销；到期后 worker 在单个事务中删除本人数据并保留不含邮箱、原用户 ID 或正文的最小回执。`--once` 可处理最多一个到期账号并退出。

再在 `web/` 启动 Vue 前端：

```bash
pnpm install --frozen-lockfile
pnpm dev
```

访问 `http://localhost:5173` 可查看公开产品首页，并从首页进入不保存数据的交互演示、帮助、用户协议、隐私说明、注册或登录。网页通过同源代理连接 `127.0.0.1:8000`；邮件中的链接指向这个网页地址。登录后默认进入 `/timetable` 课表工作台；顶部导航为课表、任务、AI、复盘，规划、统计与统一设置继续位于个人中心和账号菜单。`/tasks` 的活动任务名称可录制最长 60 秒语音，校对后再替换或追加到未保存表单；`/calendar` 可删除并恢复日历系列或 ICS 订阅源，暂停订阅不会删除内容；`/plans` 支持查看与修改日周规划，`/focus` 已并入 AI 与任务/日历时间块，`/reviews` 支持按活动日补做复盘、短追问、版本报告、批注和长期记忆管理，`/me/stats` 支持按日、自然周和自然月查看计划、任务与专注的独立统计，`/settings` 集中管理账号安全、外观、时间、AI 隐私、额度、同步、数据导出和账号注销；旧 `/account/*` 地址继续兼容跳转。

首次创建管理员只能在服务器本机执行，命令交互读取密码并输出一次性 TOTP 设置链接：

```bash
cd server
uv run python -m itime.admin.cli create --email admin@example.com
```

TOTP 和恢复码全部丢失时，`reset-totp` 会撤销该管理员全部会话并生成新设置链接。设置链接打开 `/admin/setup`，完成后从 `/admin/login` 进入安全设置；不要通过普通注册提升管理员，也不要把一次性链接写入文档或日志。

使用独立 Colima profile 时，在 Docker 命令中加 `--context colima-itime`。开发配置只在忽略的 `.env.local`，初始化不会覆盖已有文件。

- API 存活：`http://127.0.0.1:8000/api/health`
- 数据库与迁移就绪：`http://127.0.0.1:8000/api/ready`
- 开发 OpenAPI：`http://127.0.0.1:8000/api/docs`
- 本地测试邮箱：`http://127.0.0.1:58025`，只收取开发邮件，不外发。

## 服务验证

在 `server/` 执行：

```bash
uv run ruff check .
uv run pytest -q
uv build --no-sources
```

集成测试自动创建随机命名的本地 PostgreSQL 测试库、运行迁移、启动独立 HTTP 服务，结束后只删除测试库。覆盖数据库故障恢复、账号并发、会话撤销、真实邮件发送与重试。生产和外部数据库会被测试入口拒绝。

在 `web/` 执行真实浏览器验证：

```bash
pnpm build
pnpm exec playwright install chromium
pnpm test:e2e
```

UI 测试自动启动独立数据库、API、邮件及业务 worker 和生产构建预览，访问 `http://localhost:4173`；开发库不被改写。本地 PostgreSQL/Mailpit 需先运行。测试后清理独立进程和测试库；截图位于 `/tmp/itime-ui-evidence/`，失败证据位于 `/tmp/itime-ui-results/`。仓库默认 Chromium 回归不单独证明真实 Edge 或实体设备兼容性。

`web/e2e/full-user-journey.spec.ts` 使用真实服务贯通注册、任务、AI 规划确认、日历专注、困难、
明确任务完成、复盘及独立第二端同步；该核心流程已在 Google Chrome 149 desktop 和 Microsoft
Edge 154 的 desktop、mobile、tablet 项目运行通过，全部 297 项 Web 测试也已在 Edge 154
三视口完成。Playwright 配置可用 `ITIME_E2E_BROWSER_CHANNEL` 或
`ITIME_E2E_BROWSER_EXECUTABLE_PATH` 显式选择本机浏览器；浏览器视口结果不替代实体设备测试。

完整性能基线只允许在专用 `colima-itime-perf` context、随机 `itime_perf_*` 数据库和
`ITIME_ENVIRONMENT=test` 下运行。它会占用约 3 小时，依次执行稳态、峰值、重历史、阶梯容量、
AI 隔离、三类队列、维护和 60 分钟耐久场景，并精确清理本次资源：

```bash
cd server
uv run python -m performance.run baseline
uv run python -m performance.run verify-clean
```

负载下真实 UI 由 `uv run python -m performance.run scenario ui-load` 编排；Chromium 三视口和
真实 Edge desktop 的最终证据、运行 ID、分位数及边界见[实施与验证](doc/验收/iTime-Web实施与验证.md)。
该本地结果不代表公网、正式 AI 供应商或最终生产拓扑 SLO。

仓库：[ENOCH-lyn/iTime](https://github.com/ENOCH-lyn/iTime)，私有。
