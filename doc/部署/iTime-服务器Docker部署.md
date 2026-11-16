# iTime 服务器 Docker 部署

本文描述生产形态的最小部署：**API 容器 + PostgreSQL 容器**，只把 API 绑定到
`127.0.0.1`，由 Cloudflare Tunnel（或其它本机反向代理）对外提供 HTTPS。
Web 前端与后台 worker 按需另加，不属于本最小拓扑。

## 文件

| 文件 | 作用 |
| --- | --- |
| `compose.prod.yaml` | `db`（postgres:16-alpine）+ `api`（`server/Dockerfile.prod`），API 绑定 `127.0.0.1:18000` |
| `server/Dockerfile.prod` | 基于 `python:3.13-slim`、uv 锁文件安装、非 root 运行；启动时先 `alembic upgrade head` |
| `.env.prod.example` | 生产环境变量模板（复制为 `.env.prod`，后者已被 `.gitignore` 忽略） |

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

# 3. 本机验证
curl -s http://127.0.0.1:18000/api/health
curl -s http://127.0.0.1:18000/api/ready
```

生产配置会被服务端强制校验：`ITIME_PUBLIC_ORIGIN` 必须是 HTTPS；四个加密密钥必须互不相同；
`ITIME_ALLOWED_HOSTS` 必须列出明确主机名（公网域名 + `localhost` + `127.0.0.1`）。

## Cloudflare Tunnel

Tunnel 的目标填 `http://127.0.0.1:18000`。域名分配后，把该域名同时写入：

- `ITIME_PUBLIC_ORIGIN=https://<域名>`
- `ITIME_ALLOWED_HOSTS=["<域名>","localhost","127.0.0.1"]`

然后重启 API：

```bash
docker compose -f compose.prod.yaml --env-file .env.prod up -d --force-recreate api
```

验证：

```bash
curl -s -H "Host: <域名>" http://127.0.0.1:18000/api/health
curl -s https://<域名>/api/health
```

公网 HTTPS 由 Tunnel 终止，源站不需要开放 80/443，也不需要证书。

## 升级与回滚

```bash
# 拉取新代码后
docker compose -f compose.prod.yaml --env-file .env.prod build api
docker compose -f compose.prod.yaml --env-file .env.prod up -d api

# 回滚：切回上一个 git 提交后重新 build/up
```

数据库迁移在 API 启动时自动执行；结构变更前先备份 `itime_db` 卷：

```bash
docker run --rm -v itime_itime_db:/var/lib/postgresql/data -v "$PWD":/backup alpine \
  tar czf /backup/itime-db-$(date +%F).tar.gz -C /var/lib/postgresql/data .
```

## 安全边界

- `.env.prod` 只存在于服务器，权限 600，不进入 git、镜像层或日志。
- 容器使用非 root 用户（API），`no-new-privileges`；数据库不暴露宿主机端口。
- API 仅监听回环地址，公网入口只有 Tunnel。
- 管理后台首个管理员必须在服务器本机执行 CLI 创建（`uv run python -m itime.admin.cli create`，
  在容器内执行时使用 `docker compose exec api python -m itime.admin.cli create --email ...`）。
