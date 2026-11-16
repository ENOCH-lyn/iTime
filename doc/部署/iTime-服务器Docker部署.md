# iTime 服务器 Docker 部署

本文描述生产形态的容器部署：**Web（Nginx + Vue 构建产物）+ API + PostgreSQL**，
只有一个公网入口（Web 容器的 80 端口，绑定到宿主机 `127.0.0.1:18000`），
由 Cloudflare Tunnel（或其它本机反向代理）对外提供 HTTPS。Nginx 同时把 `/api/`
代理给内部 API 容器，因此 Web 与 API 同源，无需 CORS。

## 文件

| 文件 | 作用 |
| --- | --- |
| `compose.prod.yaml` | `db`（postgres:16-alpine）、`api`（`server/Dockerfile.prod`）、`web`（`web/Dockerfile.prod`） |
| `server/Dockerfile.prod` | Python 3.13 + uv 锁文件安装、非 root 运行；启动时先 `alembic upgrade head` |
| `web/Dockerfile.prod` | Node 24 + pnpm 构建 Vue，Nginx 托管 `dist/` 并代理 `/api/` |
| `web/nginx.conf` | SPA history 回退、同源 API 代理（经 Docker DNS 运行时解析）、静态资源缓存 |
| `.env.prod.example` | 生产环境变量模板（复制为 `.env.prod`，后者被 `.gitignore` 忽略） |

端口约定：

- `web` → `127.0.0.1:18000:80`（唯一公网入口）
- `api` → 仅容器网络 `8000`，不对宿主机暴露；由 Nginx 代理
- `db` → 仅容器网络 `5432`

## 首次部署

```bash
# 1. 准备配置（不要提交 .env.prod）
cp .env.prod.example .env.prod
chmod 600 .env.prod
# 填入：数据库密码、公网域名、四个独立 Fernet 密钥
# 生成密钥：python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 2. 构建并启动
docker compose -f compose.prod.yaml --env-file .env.prod build
docker compose -f compose.prod.yaml --env-file .env.prod up -d

# 3. 本机验证（Host 头用公网域名）
curl -s -H "Host: <域名>" http://127.0.0.1:18000/api/health
curl -s -H "Host: <域名>" http://127.0.0.1:18000/api/ready
curl -s -H "Host: <域名>" http://127.0.0.1:18000/ | head
```

生产配置会被服务端强制校验：`ITIME_PUBLIC_ORIGIN` 必须是 HTTPS；四个加密密钥必须互不相同；
`ITIME_ALLOWED_HOSTS` 必须列出明确主机名（公网域名 + `localhost` + `127.0.0.1`）。

## Cloudflare Tunnel

Tunnel 的 Public hostname → Service 填 **`http://127.0.0.1:18000`**，域名分配后把该域名写入：

- `ITIME_PUBLIC_ORIGIN=https://<域名>`
- `ITIME_ALLOWED_HOSTS=["<域名>","localhost","127.0.0.1"]`

重启 API 并验证：

```bash
docker compose -f compose.prod.yaml --env-file .env.prod up -d --force-recreate api
curl -s -H "Host: <域名>" http://127.0.0.1:18000/api/health
curl -s https://<域名>/api/health
```

公网 HTTPS 由 Tunnel 终止，源站不需要开放 80/443，也不需要证书。

## 后台 worker

`compose.prod.yaml` 还定义了 11 个常驻 worker（复用 API 镜像、`healthcheck: disable`）：

| 服务 | 作用 |
| --- | --- |
| `ai-worker` | 对话、规划、复盘、截图、转写作业 |
| `subscription-worker` | ICS URL 订阅刷新 |
| `public-links-worker` | 任务公开链接受限抓取 |
| `data-export-worker` | 本人数据导出生成 |
| `account-deletion-worker` | 注销到期清理 |
| `tasks-cleanup-worker` / `calendar-cleanup-worker` | 任务 / 日历 30 天回收站清理 |
| `execution-cleanup-worker` / `review-cleanup-worker` | 困难 / 长期记忆回收站清理 |
| `ai-retention-worker` | AI 正文日志 7 天、质检样本 30 天清理 |
| `sync-cleanup-worker` | 同步事件 30 天清理 |

邮件 worker 需要可用 SMTP；未配置 SMTP 时不启动，验证/找回邮件不可用。

## 首次账号与后台

- 默认开放注册、无需邮箱验证；没有配置 SMTP 时，验证/找回邮件不可用（`email_available=false`）。
- 首个超级管理员必须在服务器本机创建（交互式读取密码并输出一次性 TOTP 链接）：

```bash
docker compose -f compose.prod.yaml --env-file .env.prod exec api \
  python -m itime.admin.cli create --email admin@example.com
```

非交互创建可用 `--password-stdin`（密码从标准输入传入，仍只用于本机受控初始化）。

## AI 配置

1. 用管理员登录 `/admin/login`（密码 + TOTP），在「AI 配置」为每项能力创建端点：
   `conversation` / `planning` / `review` 需要兼容 OpenAI `/chat/completions` 的文本模型；
   `calendar_screenshot` 需要支持 `image_url` 的视觉模型；`transcription` 需要支持
   `/audio/transcriptions` 的语音模型。
2. 每个版本先「测试」再「发布」；生产端点必须是 HTTPS。
3. 发布额度策略前，`calendar_screenshot` 与 `conversation` 必须有已发布、币种一致且价格完整的配置
   （服务端校验 `BASELINE_AI_CAPABILITIES`）。只用文本模型时，可直接插入一条已发布的额度策略
   （见 `ai_quota_policy_versions` / `ai_quota_policy_state`）；但之后从后台发布新策略仍会被该校验拦住，
   直到补上视觉能力配置。
4. 额度策略按天配置币种与金额（例：CNY 5）；AI 配置价格币种必须与策略一致。


## 升级与回滚

```bash
# 拉取新代码后
docker compose -f compose.prod.yaml --env-file .env.prod build
docker compose -f compose.prod.yaml --env-file .env.prod up -d

# 只更新 Web
docker compose -f compose.prod.yaml --env-file .env.prod build web
docker compose -f compose.prod.yaml --env-file .env.prod up -d web

# 回滚：切回上一个 git 提交后重新 build/up
```

数据库迁移在 API 启动时自动执行；结构变更前先备份 `itime_db` 卷：

```bash
docker run --rm -v itime_itime_db:/var/lib/postgresql/data -v "$PWD":/backup alpine \
  tar czf /backup/itime-db-$(date +%F).tar.gz -C /var/lib/postgresql/data .
```

## 安全边界

- `.env.prod` 只存在于服务器，权限 600，不进入 git、镜像层或日志。
- API 与 Web 容器均以非 root 运行（Nginx 主进程除外），`no-new-privileges`；数据库不暴露宿主机端口。
- 公网入口只有 Tunnel → `127.0.0.1:18000`；API 只在容器网络内可达。
- Web 只提供同源 `/api` 代理，不额外开放跨域。
