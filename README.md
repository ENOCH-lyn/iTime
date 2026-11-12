# iTime

**面向个人的 AI 时间规划与复盘系统 —— Web 与原生 Android 共用一套服务端语义。**

iTime 把「任务 / 课表与日历 / AI 对话 / 执行记录 / 复盘与长期记忆 / 统计」放在同一个工作台里：用户用自然语言描述安排，AI 以受限工具生成提案，用户确认后才写入业务数据；所有内容经服务端同步，Web 与 Android 离线优先的本地库保持同一份事实。

<p>
  <img alt="Status" src="https://img.shields.io/badge/status-M8--B%2FM8--C%20pending-yellow">
  <img alt="Web" src="https://img.shields.io/badge/Web-Vue%203%20%2B%20Vite%207-42b883">
  <img alt="Server" src="https://img.shields.io/badge/Server-FastAPI%20%2B%20PostgreSQL-009688">
  <img alt="Android" src="https://img.shields.io/badge/Android-Kotlin%20%2B%20Compose%20M3-3ddc84">
</p>

> 当前状态：Web 首版功能闭环（M1—M8-A）已完成并通过真实浏览器与性能基线验证；原生 Android 客户端完成首轮（v0.1，模拟器主流程验收）；M8-B 备份恢复、M8-C 生产发布、正式模型质量评测与 iOS 客户端仍未完成，因此尚不宣称产品交付。详见[实施与验证](doc/验收/iTime-Web实施与验证.md)。

## 目录

- [功能特性](#功能特性)
- [技术栈](#技术栈)
- [仓库结构](#仓库结构)
- [快速开始](#快速开始)
- [配置](#配置)
- [测试](#测试)
- [文档](#文档)
- [项目状态与路线图](#项目状态与路线图)
- [贡献](#贡献)
- [安全](#安全)
- [许可证](#许可证)

## 功能特性

### 任务与日程

- 统一任务：标题、描述、项目、标签、优先级、预估耗时、截止时间与重复规则；步骤顺序执行，不引入任务依赖。
- 课表与日历统一：学期、周次、单双周、节次、单次例外（取消 / 改期 / 覆盖），周视图与月视图共用同一事件详情。
- 导入：Excel、ICS 文件、课表截图、ICS URL 订阅与公开网页；候选先校对再入库，来源失败保留上次成功数据。
- 生命周期：任务、困难、长期记忆、日历系列与 ICS 订阅源 30 天回收站；账号注销 7 天可撤销。

### AI 对话与提案

- 全局多会话对话，Agent 自动识别规划 / 导入 / 复盘意图，无需先选模式。
- 服务端受限工具：只读工具可执行，所有业务写操作生成提案，用户确认后应用；版本变化时提示冲突再确认。
- 截图识别、语音转写、日周规划、复盘报告、长期记忆候选；AI 正文日志默认 7 天、质检样本 30 天，均可审计。
- 每日金额额度按用户时区 04:00 活动日冻结与结算，失败不扣额。

### 执行、复盘与统计

- 正计时 / 倒计时专注，全账号单一进行中会话，支持暂停、恢复、结束与补记修订。
- 困难记录与主动求助 AI；单日复盘最多三轮追问，报告版本不可变，支持批注。
- 日 / 自然周 / 自然月统计，计划、任务与专注事实分开展示，不合成单一效率分。

### 同步与离线

- 服务端变更流 + 快照：Web 用 IndexedDB 缓存与类型化队列；Android 用 Room + outbox 队列，离线可新建 / 编辑 / 勾选，联网自动补传。
- 简单幂等改动自动收敛；实质冲突保留用户选择（Web 端同步工作区、Android 设置页），不做「最后写入覆盖」。

### 管理后台

- 单超级管理员，密码 + TOTP + 恢复码；邀请注册、用户状态、AI 能力配置（草稿 / 测试 / 发布 / 主备路由）、额度策略、成本对账、日志、质检与正文访问审计。
- 平台 AI Key 仅存服务端，客户端与管理页面均不回显明文。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 服务端 | Python 3.13、FastAPI、SQLAlchemy 2（async）、Alembic、PostgreSQL、psycopg 3、LangGraph 持久化 Agent |
| Web | Vue 3.5、TypeScript 5.9、Vite 7、Vue Router 4、Element Plus 2、markdown-it + DOMPurify、Playwright |
| Android | Kotlin 2.0、Jetpack Compose（Material 3）、Navigation Compose、Room 2.6 + KSP、OkHttp 4、kotlinx.serialization、DataStore、WorkManager |
| 基础设施 | Docker Compose（PostgreSQL + Mailpit）、uv、pnpm |

## 仓库结构

```text
iTime/
├─ server/                 # FastAPI 服务、迁移、领域模块与后台进程
│  ├─ src/itime/           # auth / tasks / calendar / planning / execution /
│  │                       # review / statistics / sync / ai / quota / admin ...
│  ├─ migrations/          # Alembic 迁移
│  ├─ performance/         # 性能基线编排
│  └─ tests/
├─ web/                    # Vue 用户端与管理后台
│  ├─ src/pages/           # 日历、任务、AI、规划、复盘、统计、设置、后台
│  └─ e2e/                 # Playwright 真实服务端到端用例
├─ android/                # 原生 Android 客户端（Kotlin + Compose M3）
│  └─ app/src/main/java/com/itime/app/
│     ├─ data/             # Room、outbox、同步引擎、API、会话
│     ├─ repository/       # 任务 / 日历 / AI / 账号仓库（离线优先）
│     └─ ui/               # 今天、日历、任务、AI、设置与自适应导航
├─ doc/                    # 需求、架构、设计、计划、测试、验收
├─ scripts/                # 初始化与工具脚本
├─ compose.yaml            # PostgreSQL + Mailpit
└─ .env.example            # 环境变量示例（复制为 .env.local）
```

## 快速开始

### 前置条件

- Python 3.13 与 [uv](https://docs.astral.sh/uv/)
- Node.js 24 与 pnpm 11
- Docker Compose（本地 PostgreSQL + Mailpit）
- 构建 Android 需要 JDK 17、Android SDK（platform 35、build-tools 35.0.0），可选 Android 模拟器

### 1. 启动依赖与服务端

```bash
python3 scripts/bootstrap.py
docker compose --env-file .env.local up -d --wait

cd server
uv sync --frozen
uv run alembic upgrade head
uv run uvicorn itime.main:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log
```

健康检查：

- API 存活：`http://127.0.0.1:8000/api/health`
- 数据库与迁移就绪：`http://127.0.0.1:8000/api/ready`
- OpenAPI：`http://127.0.0.1:8000/api/docs`
- 本地测试邮箱：`http://127.0.0.1:58025`（仅接收开发邮件）

### 2. 启动 Web

```bash
cd web
pnpm install --frozen-lockfile
pnpm dev
```

访问 `http://localhost:5173`：公开首页 → 注册 / 登录 → 默认进入 `/calendar`。网页通过同源代理连接 `127.0.0.1:8000`。

### 3. 构建与运行 Android

```bash
cd android
export JAVA_HOME=/path/to/jdk17
export ANDROID_SDK_ROOT=/path/to/android-sdk

./gradlew :app:assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

模拟器内的服务端地址填 `http://10.0.2.2:8000/`（回环到宿主机）；真机填电脑局域网 IP，例如 `http://192.168.1.10:8000/`，并确认该地址在 `ITIME_ALLOWED_HOSTS` 中。

### 4. 后台进程（按需启动）

邮件、AI 作业、订阅刷新、链接抓取与各类到期清理是独立进程；只跑 API 不会发送邮件或执行 AI 作业。均在 `server/` 下用 `uv run python -m <module>` 启动：

| 模块 | 作用 |
| --- | --- |
| `itime.mail_worker` | 验证 / 找回邮件队列 |
| `itime.ai.worker` | 截图识别、语音转写、对话、规划、复盘作业 |
| `itime.calendar.subscription_worker` | ICS URL 订阅刷新 |
| `itime.public_links.worker` | 任务公开链接受限抓取 |
| `itime.tasks.cleanup_worker` / `itime.calendar.cleanup_worker` | 任务 / 日历 30 天回收站清理 |
| `itime.execution.cleanup_worker` / `itime.review.cleanup_worker` | 困难 / 长期记忆回收站清理 |
| `itime.ai.retention_worker` | AI 正文日志（7 天）与质检样本（30 天）清理 |
| `itime.data_export.worker` | 本人数据导出生成与过期清理 |
| `itime.sync.cleanup_worker` | 同步变更事件 30 天清理 |
| `itime.account_deletion.worker` | 注销到期清理 |

大部分 worker 支持 `--once` 供受控定时任务调用。

### 5. 管理员

首次创建管理员只能在服务器本机执行，命令交互读取密码并输出一次性 TOTP 设置链接：

```bash
cd server
uv run python -m itime.admin.cli create --email admin@example.com
```

设置链接打开 `/admin/setup`，完成后从 `/admin/login` 进入；不要通过普通注册提升管理员。

## 配置

- 本地配置放在仓库根目录 `.env.local`（已被 `.gitignore` 忽略），从 `.env.example` 复制后修改；初始化脚本不会覆盖已有文件。
- 生产必须为账号、AI、ICS 订阅、数据导出分别配置不同的加密密钥（`ITIME_*_ENCRYPTION_KEY`），不能用默认值。
- AI 端点与 Key 通过管理后台「草稿 → 测试 → 发布」配置，客户端不接触明文 Key。
- 对外地址与来源限制由 `ITIME_ALLOWED_HOSTS` 等变量控制；Android 真机访问时需加入对应局域网地址。

完整变量、部署拓扑与成本说明见 [cloud.md](cloud.md)。

## 测试

服务端：

```bash
cd server
uv run ruff check .
uv run pytest -q
uv build --no-sources
```

Web（Playwright 会启动独立数据库、API、邮件与 worker，使用生产构建预览）：

```bash
cd web
pnpm build
pnpm exec playwright install chromium
pnpm test:e2e
```

Android（JVM 单测无需模拟器，仪器测试需要已启动的模拟器或真机）：

```bash
cd android
./gradlew :app:testDebugUnitTest
./gradlew :app:connectedDebugAndroidTest
```

截至 2026-11-12：Android JVM 单测 30 个、仪器测试 33 个通过；Web Chromium 全量 Playwright `317 passed, 22 skipped`（339 项收集，22 项为既有 desktop-only/慢用例跳过），真实 Edge 154 关键路径 `28 passed`（三视口）；服务端 pytest 覆盖真实 PostgreSQL、HTTP、邮件与 worker。数字与证据以[实施与验证](doc/验收/iTime-Web实施与验证.md)与 Android 验收记录为准，不以本文代替执行结果。

性能基线只允许在专用 Docker context、随机测试库和 `ITIME_ENVIRONMENT=test` 下运行，详见 [cloud.md](cloud.md)；本地受控结果不代表公网或正式供应商 SLO。

## 文档

- [文档索引](doc/README.md) —— 需求、架构、设计、计划、测试与验收的入口
- [需求规格说明书](doc/需求/iTime-需求规格说明书.md) —— 当前产品基线（版本 0.3）
- [需求追踪矩阵](doc/需求/iTime-需求追踪矩阵.md) —— 需求 → 设计 → 用例 → 阶段
- [架构决策记录](doc/架构/iTime-ADR架构决策文档.md) —— 已确认边界与待比较方案
- [实施与验证](doc/验收/iTime-Web实施与验证.md) —— 真实执行证据与未完成项
- [AI 协作规范](AGENTS.md) —— 事实优先级、工程约束与完成标准

## 项目状态与路线图

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| M1—M4 | 账号、任务、日历与导入、AI 对话与提案、额度与后台 | 已完成 |
| M5—M6 | 订阅冲突、执行 / 困难、复盘与记忆、统计、同步、导出、注销 | 已完成 |
| M7 | 完整 Web 闭环与真实浏览器回归 | 已完成 |
| M8-A | 性能、容量与耐久基线（2 vCPU / 4 GiB 隔离环境） | 已完成 |
| M8-B | 备份与恢复（RPO/RTO、删除台账重放） | 未开始 / 待排期 |
| M8-C | 生产发布与正式模型质量评测 | 未开始 / 待排期 |
| Android v0.1 | 原生客户端、离线优先、同步、AI 线上、宽屏适配 | 已完成（模拟器验收） |
| iOS | 原生客户端 | 未开始 |
| 业务提醒 | Android 提醒开关、静默与逐任务覆盖 | 未实现（Web 明确不做） |

已知限制：真机与实体设备未验证；TalkBack 仅验证焦点；M8-B/C 未完成；Android 未实现提醒与 iOS 版本。详见[实施与验证](doc/验收/iTime-Web实施与验证.md)。

## 贡献

1. 先读 [AGENTS.md](AGENTS.md) 与 [文档索引](doc/README.md)，确认事实来源与边界。
2. 每个改动保持可说明、可回滚：提交信息用 `feat|fix|docs|refactor|chore(scope): 中文摘要`。
3. 业务逻辑、数据持久化、鉴权、同步等行为变更需附对应测试；不要只提交页面或模拟数据。
4. 不提交 `.env.local`、token、密钥或测试用户隐私数据；测试使用本地隔离数据库。
5. 文档与实现同步：需求/设计/测试/验收发生语义变化时更新对应文档，而不是让 README 单独描述。

## 安全

- 请勿在公开 Issue 中披露未修复漏洞；使用 GitHub Security Advisory 或私下联系维护者。
- 仓库历史已扫描常见凭据模式（token、私钥、`sk-` 等）为空；如发现泄露请立即报告。
- 生产部署前必须更换全部示例密钥，并完成 M8-C 的发布检查。

## 许可证

本仓库暂未附带开源许可证文件（`LICENSE`）。在维护者明确选择许可证之前，保留所有权利；如需在其它项目中使用、分发或商用，请先联系维护者确认授权方式。
