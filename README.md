# iTime

iTime 是面向个人的 AI 时间规划与复盘产品。学生优先，兼容通用个人场景，通过任务与课表整理、日周规划、执行记录和复盘形成完整闭环。

**当前正在开发完整 Web 首版。服务运行基础、账号与后台用户管理、目标/项目与任务页面，以及课表与统一日历页面已经完成；任务与日历均已接入截止、重复、实例操作、学期节次、冲突、例外和补课等真实服务。导入、AI、规划、执行、复盘、同步及其余产品业务仍在开发，尚未达到产品交付标准。** 实际证据见[实施与验证](doc/验收/iTime-Web实施与验证.md)。

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

再在 `web/` 启动 Vue 前端：

```bash
pnpm install --frozen-lockfile
pnpm dev
```

访问 `http://localhost:5173`。网页通过同源代理连接 `127.0.0.1:8000`；邮件中的链接指向这个网页地址。当前登录后进入任务工作区，可从产品导航进入统一日历和账号设置；全局 AI 对话及其余业务页面仍待接入。

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

UI 测试自动启动独立数据库、API、邮件进程和生产构建预览，访问 `http://localhost:4173`；开发库不被改写。本地 PostgreSQL/Mailpit 需先运行。测试后清理独立进程和测试库；截图位于 `/tmp/itime-ui-evidence/`，失败证据位于 `/tmp/itime-ui-results/`。账号页面验证不代表完整产品验收，也不等同真机或 Edge 兼容性通过。

仓库：[ENOCH-lyn/iTime](https://github.com/ENOCH-lyn/iTime)，私有。
