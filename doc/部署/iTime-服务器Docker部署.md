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
| `web/nginx.conf` | SPA history 回退、同源 API 代理、静态资源缓存 |
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

## 首次账号与后台

- 默认开放注册、无需邮箱验证；没有配置 SMTP 时，验证/找回邮件不可用（`email_available=false`）。
- 首个超级管理员必须在服务器本机创建（交互式读取密码并输出一次性 TOTP 链接）：

```bash
docker compose -f compose.prod.yaml --env-file .env.prod exec api \
  python -m itime.admin.cli create --email admin@example.com
```

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
