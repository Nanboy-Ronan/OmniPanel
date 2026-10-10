# 快速上手

[English](getting-started.md) | [中文](getting-started.zh-CN.md)

本指南介绍如何安装 OmniPanel、完成配置、启动前后端服务，并创建第一个用户。

## 环境依赖

- Python 3.13+
- Node.js 22.18+ 与 npm
- 一个后端能连接到的 PostgreSQL 服务器（13+）
- （可选）Redis —— 仅用于分布式限流；连不上 Redis 时会自动退回到进程内限流

## 1. 安装依赖

```bash
git clone <你的仓库地址> omnipanel
cd omnipanel
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## 2. 配置环境变量

```bash
cp .env.example .env
```

至少需要编辑 `.env` 中的这几项：

```dotenv
RAP_DATABASE_URL=postgresql+asyncpg://<用户名>:<密码>@<host>:5432/<数据库名>
RAP_SECRET=<一段足够长的随机字符串>
CORS_ORIGINS=http://localhost:5173
WECOM_CONSOLE_REDIRECT_URI=http://localhost:5173/console/
WECOM_CORP_ID=<企业 ID>
WECOM_AGENT_ID=<应用 ID>
WECOM_APP_SECRET=<应用密钥>
```

`RAP_SECRET` 用于签发登录令牌，可以这样生成一个：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

完整的环境变量列表见 [架构说明 → 配置参考](architecture.zh-CN.md#配置参考)，或者直接看
`.env.example`，里面对每个可选项都有行内注释说明。

## 3. 创建数据库

创建一个与 `RAP_DATABASE_URL` 对应的空数据库：

```bash
createdb <数据库名>
```

## 4. 执行数据库迁移

```bash
make db-upgrade        # 等价于：alembic upgrade head
```

这会创建应用所需的全部表。可以用下面的命令校验：

```bash
make db-check
```

SQL 查询台和中文问数据使用独立的只读登录角色 `rpa_sql_console`，迁移会创建这个角色，但不设密码。要启用它们，请用数据库所有者设置密码，并在 `.env` 里配置连接：

```bash
psql <dbname> -c "ALTER ROLE rpa_sql_console PASSWORD '<足够长的随机密码>'"
# .env
RAP_SQL_CONSOLE_DATABASE_URL=postgresql+asyncpg://rpa_sql_console:<密码>@<host>:5432/<dbname>
```

配置之前，查询台会返回 503"未配置"；它不会退回使用应用自己的数据库角色。

## 5. 启动后端

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

启动时应用会：
- 把所有 ORM 模型注册到 SQLAlchemy
- 尝试获取一个"leader"锁（见 `app/utils/leader.py`）——只有拿到锁的进程会运行后台的备份/同步任务，所以可以安全地启动多个后端进程
- 暴露 `GET /health`（检查数据库 + Redis）和 `GET /ping`

## 6. 启动前端

另开一个终端：

```bash
npm --prefix frontend ci
make ui
```

打开 `http://localhost:5173/console/`。Vite 将同源 `/api/` 请求代理到 FastAPI。实际部署时需在企业微信登记回调域名。

## 7. 创建第一个用户

配置企业微信凭据和回调后扫码登录。**首个登录用户成为管理员**，后续用户按
`WECOM_DEFAULT_ROLE` 分配角色（默认为 viewer）。无需配置凭据的界面体验可先构建前端，
运行 `node frontend/scripts/preview-fixtures.mjs`，再打开
`http://127.0.0.1:5180/console/`；该本地预览仅提供合成数据。

## 8. 上传第一份导出文件

从你的企业后台或内容平台后台导出一份订单或图文报表（OmniPanel 目前能自动识别的格式见
[架构说明 → 数据摄入](architecture.zh-CN.md#数据摄入-etl)），在前端的上传页面上传它，然后到分析页面查看生成的指标。

## 直接使用 HTTPS（可选）

如果不打算用反向代理而想直接在本机/单机环境启用 HTTPS，可设置：

```dotenv
SSL_KEYFILE=/path/to/key.pem
SSL_CERTFILE=/path/to/cert.pem
```

以 `python -m app.main` 方式启动时会自动读取这两项。在生产环境中，更常见的做法是在
uvicorn（纯 HTTP）前面用反向代理（nginx、Caddy 等）终止 TLS——这种情况下不要设置这两项，并确保
`PROXY_HEADERS=true`（默认就是开启的），这样才能从 `X-Forwarded-For` 正确读取客户端 IP。

## 常见问题排查

**`make db-upgrade` 报"connection refused"或"role does not exist"**

检查 `.env` 中 `RAP_DATABASE_URL` 是否指向一个正在运行的 PostgreSQL 实例，以及 URL 里的用户和数据库是否已创建。如果数据库还没建，先执行 `createdb <数据库名>`。

**后端启动后，`GET /health` 返回不健康**

健康检查会同时验证数据库连接和（如果配置了 `REDIS_URL`）Redis 连通性。Redis 不可达时，应用会自动降级，但其他功能不受影响。如果是数据库检查失败，请再次核对 `RAP_DATABASE_URL` 并确认迁移已执行。

**React 加载数据时提示"Connection refused"**

浏览器请求同源 `/api/`；开发环境由 Vite 代理到 `http://127.0.0.1:8000`，Compose 使用 Nginx。检查后端服务与代理目标是否可达；开发目标可用 `API_PROXY_TARGET` 调整。

**上传文件时报"unrecognized column set"（无法识别列结构）**

OmniPanel 纯粹依靠列名来识别平台来源。请确保上传的是从平台后台直接导出的原始文件，而不是你自己重新整理过的格式。支持的列名指纹详见[架构说明 → 数据摄入](architecture.zh-CN.md#数据摄入-etl)。

**第一个注册的用户没有自动成为 admin**

自动提升为 `admin` 只对 `user` 表中的第一行生效。如果你曾经删掉并重建过数据库，重新注册后应该正常。如果表里之前已有记录，可以手动执行 SQL 提升：

```sql
UPDATE "user" SET role = 'admin' WHERE email = 'your@email.com';
```

**SQL 查询台提示未配置（503）**

`RAP_SQL_CONSOLE_DATABASE_URL` 为空，见第 4 步。

**企业微信新成员登录后提示"账号尚未开通"**

新成员需要管理员在用户管理页开通。设置 `WECOM_NEW_USERS_ACTIVE=true` 可以跳过这一步。

**中文问数据返回 503**

没有配置任何大模型服务商的 API Key。在 `.env` 中至少配置一个（例如 `MINIMAX_API_KEY=...`），然后重启后端。

## 下一步

- [架构说明](architecture.zh-CN.md) —— 各组件如何协作、数据模型、完整配置参考
- [中文问数据 (NL-to-SQL)](nl-to-sql.zh-CN.md) —— 启用自然语言查数据功能
- [测试指南](testing.zh-CN.md) —— 在你自己的 PostgreSQL 上跑测试套件
