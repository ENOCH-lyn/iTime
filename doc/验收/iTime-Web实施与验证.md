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
