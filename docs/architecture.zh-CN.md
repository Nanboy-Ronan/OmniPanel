# 架构说明

[English](architecture.md) | [中文](architecture.zh-CN.md)

## 总览

```
Streamlit 前端  ──HTTP──▶  FastAPI 后端  ──▶  PostgreSQL
                              │
                              ├─ ETL：识别平台 → 归一化 → 入库
                              ├─ 分析接口（SQL 聚合）
                              ├─ SQL 查询台（只读即席查询）
                              └─ 中文问数据（在查询台之上的可选大模型层）
```

- **前端** —— Streamlit（`app/ui/`）。一个很薄的客户端：带着 JWT bearer token
  调用后端 HTTP 接口，把拿到的 JSON 渲染出来。这里不放任何业务逻辑。
- **后端** —— FastAPI（`app/`）。承载所有业务规则、ETL 流程、鉴权，以及 SQL
  查询台的安全防护。
- **数据库** —— PostgreSQL，通过 SQLAlchemy 访问（应用本体用 `asyncpg`；Alembic
  和部分后台任务用同步的 `psycopg2`/SQLAlchemy）。
- **数据库迁移** —— Alembic（`alembic/versions/`），通过 `make db-upgrade` /
  `make db-new-migration` 驱动。

## 请求流程

1. Streamlit 前端发起请求，带上 `Authorization: Bearer <token>`。
2. FastAPI-Users（`app/auth.py`）把 token 解析成一个 `User` 行及其 `role`
   （`viewer` / `analyst` / `admin`）。
3. 某个依赖项（`current_active_user` / `current_analyst_user` /
   `current_admin_user`）按角色对接口做访问控制。
4. 接口执行具体逻辑（ETL 入库、分析查询，或 SQL 查询台查询），大多数写操作/敏感操作会被写入
   `operation_log`。

## 数据摄入 (ETL)

摄入逻辑在 `app/db/etl/` 下拆成三个可组合的阶段：

1. **`detect.py` —— `detect_platform(df)`**
   纯粹基于列名指纹来识别数据来源平台——不依赖文件名或内容嗅探：
   - `买家付款时间` + `收货人手机号/提货人手机号` → `youzan`（有赞）
   - `京东价` + `客户地址` → `jd`（京东）
   - `订单编号` + `收货地址` → `tmall`（天猫）

   识别不出的列集合会抛出 `ValueError`，上传在写入任何数据之前就会被拒绝。

2. **`normalize.py` —— `normalize_dataframe(df)`**
   把各平台原始列映射到统一结构（订单号、日期、客户标识、SKU、数量、金额、收货人、手机号、省份、地址、买家昵称、优惠券、分销员）。各平台对同一个底层字段的原始列名和空值/格式约定都不一样，归一化这一步就是吸收这些差异，让下游代码不需要关心某一行数据来自哪个平台。

3. **`load.py` —— `ingest(df, session)` / `ingest_upload(...)`**
   持久化归一化后的行。订单去重基于**内容哈希**：只有当一行的
   `(订单号, 归一化后的字段值)` 哈希值之前没出现过时才会插入，所以重复上传同一份导出文件（或者日期范围有重叠的导出）总是安全的——只会插入真正新增或发生变化的行。`Customer`
   按 `customer_key` 做 upsert（同时维护 `first_order_date`），原始平台格式的行也会同时保留在对应的平台原始表（`youzan_orders` / `jd_orders` /
   `tmall_orders`）里，方便追溯回原始导出文件。

自媒体数据摄入（微信、小红书、知乎）遵循同样的"识别 → 归一化 → 入库"结构，但代码在
`app/views/media/` 和 `app/db/media_etl.py` 下——因为各平台的导出格式差异太大，没法和电商的归一化逻辑共用。

## 数据模型

核心表一览（权威定义见 `app/db/models.py`）：

| 表 | 作用 |
|---|---|
| `user` | 账号；`role` 取值 `viewer`/`analyst`/`admin` |
| `customers` | 每个 `customer_key` 一行（按平台去重后的客户身份） |
| `orders` | 跨所有平台归一化后的统一订单数据 |
| `upload_batches` | 每次上传一行；为前端轮询提供状态/计数 |
| `upload_rejected_rows` | 归一化过程中被拒绝的行，附带拒绝原因 |
| `youzan_orders` / `jd_orders` / `tmall_orders` | 与归一化的 `orders` 行对应的、平台原始格式的行 |
| `media_accounts` | 正在追踪的自媒体账号（微信公众号等） |
| `media_posts` / `media_post_metrics_daily` | 图文/笔记及其每日互动指标 |
| `media_article_traffic` | 每篇文章的流量来源拆解 |
| `media_sync_runs` | 每次同步（手动或定时）的审计记录，含状态和计数 |
| `xhs_accounts` / `xhs_posts` | 小红书账号和笔记 |
| `zhihu_posts` | 知乎文章/回答 |
| `operation_log` | 查询与写操作的只追加审计日志 |
| `saved_query` | 用户保存的 SQL 查询台查询 |

`customer_key` 的语义因平台而异（有些平台是手机号，有些是平台自身的买家
ID）——完整的业务口径参考见 `app/utils/nl_to_sql.py` 中的 `SCHEMA_DOC`
常量，它同时也是中文问数据 prompt 的一部分，相当于这些口径细节（包括哪些阅读指标是累计值而非按日、多行订单如何去重）的活文档。

### 跨平台客户身份识别（`app/views/ecommerce/identity.py`）

因为 `customer_key` 是按平台定义的，同一个真实的人在多个平台下单时，在代码里其他地方都会被当成互不相关的客户。`GET /analysis/identity/clusters`
改为按收货人手机号把各平台订单关联起来，分成两个永不相加汇总的置信度等级：

- **`exact`（精确）** —— 有赞和天猫都导出完整、未脱敏的手机号，所以两个
  `customer_key` 只要手机号完全一致，就以高置信度合并。
- **`fuzzy`（模糊）** —— 京东导出的手机号是脱敏的（`1******6198`——只保留首位数字和后四位），所以京东的行只能按这个局部指纹匹配（`app/utils/phone.py`）。这会产生真实的误判（任何后四位相同的两个人都会被混在一起），因此在聚类逻辑、API 响应结构和前端（跨平台客户页面）里都把它和精确分组结构性地分开。

这是一个附加的、只读的视图，按需计算——不会改变 `orders`/`customers` 或任何其他接口的行为。

## 角色与权限

三种角色，通过 `app/auth.py` 里的 FastAPI 依赖项强制执行：

| 角色 | 权限 |
|---|---|
| `viewer` | 查看分析仪表盘 |
| `analyst` | `viewer` 的全部权限，外加：上传文件、使用 SQL 查询台和中文问数据 |
| `admin` | `analyst` 的全部权限，外加：管理用户/角色、清空数据库、管理自媒体账号 |

第一个注册的用户会自动提升为 `admin`
（`app/auth.py:UserManager.on_after_register`）；之后注册的账号默认是
`viewer`。

## API 一览

各路由在 `app/main.py` 中挂载。按业务域分组：

| 前缀 | 业务域 | 说明 |
|---|---|---|
| `/auth/jwt`, `/auth/register`, `/auth/wecom` | 鉴权 | JWT 登录、自助注册、企业微信 OAuth |
| `/upload` | 电商摄入 | 上传文件；通过 `upload_batches/{id}` 轮询状态 |
| `/analysis` | 电商分析 | 总览、客户拆分、复购率、队列留存（`/analysis/cohort_retention`）、跨平台客户身份（`/analysis/identity/clusters`）、字段覆盖率、SQL 查询台（`/analysis/sql`）、中文问数据（`/analysis/nl-sql`） |
| `/orders_all` | 电商 | 原始订单列表/导出 |
| `/media`, `/media/xhs`, `/media/zhihu` | 自媒体 | 账号、图文/笔记、指标、流量、微信同步触发 |
| `/admin` | 管理 | 用户管理、`/admin/clear-db` |
| `/saved-queries` | SQL 查询台 | 保存/列出/删除用户保存的查询 |
| `/health`, `/ping` | 运维 | 供反向代理或监控用的健康检查 |

## SQL 查询台安全模型

即席 SQL 查询台（`POST /analysis/sql`）和中文问数据都会先经过同一套防护，才会真正触达数据库：

1. **语句白名单** —— 只接受 `SELECT`/`WITH` 语句；其他类型在执行前就会被拒绝。
2. **自动加 `LIMIT`** —— 如果查询本身没有 `LIMIT`，会自动注入一个，上限由
   `analysis_rows_cap` 控制。
3. **只读事务** —— 查询在 `SET LOCAL transaction_read_only = on` 下执行，即使有刁钻的注入绕过了白名单检查，也无法修改数据。
4. **语句超时** —— 长时间运行的查询会在服务端被中止。
5. **审计日志** —— 每一次查询（连同调用者、角色、结果行数）都会写入
   `operation_log`。

中文问数据只是在这套机制前面加了一层很薄的封装：它只负责把问题"翻译"成 SQL
文本；生成的 SQL 会经过和上面完全一样的执行管线，所以即便模型生成了不合理的查询，其风险也不会超过人类手敲了一条糟糕的查询到查询台里。具体的服务商注册机制和生成过程见
[中文问数据 (NL-to-SQL)](nl-to-sql.zh-CN.md)。

## 后台任务

在 `app/main.py` 的 FastAPI `lifespan` 中启动，通过 leader 选举
（`app/utils/leader.py`）保证即使水平扩展了多个后端进程，也只有一个进程会真正跑这些任务：

- **月度备份循环**（`app/scheduler.py:monthly_backup_loop`）——按计划对数据库做
  dump，除非设置了 `RAP_DISABLE_MONTHLY_BACKUP=true`。
- **微信自动同步循环**（`app/scheduler.py:wechat_auto_sync_loop`）——为什么需要它、如何配置见
  [微信自动同步](wechat-auto-sync.zh-CN.md)。

## 配置参考

所有配置项都是环境变量，通过 `app/config.py` 里的 `pydantic_settings`
加载（也可以放进 `.env` 文件）。`.env.example` 对常改的几项有行内注释；完整列表及默认值：

| 变量 | 默认值 | 用途 |
|---|---|---|
| `RAP_DATABASE_URL` | `postgresql+asyncpg://rpa:rpa@127.0.0.1:5432/rpa` | 主数据库连接串 |
| `DB_ECHO` | `false` | 打印每条 SQL 语句（调试用） |
| `DB_POOL_SIZE` | `10` | SQLAlchemy 连接池大小 |
| `DB_MAX_OVERFLOW` | `20` | 池外允许的额外连接数 |
| `DB_POOL_RECYCLE` | `3600` | 连接池回收周期（秒） |
| `RAP_SECRET` | `CHANGE_ME` | 签发登录令牌的密钥——任何真实部署都**必须**修改 |
| `TOKEN_LIFETIME_SECONDS` | `86400` | JWT 有效期（24 小时） |
| `HOST` | `0.0.0.0` | uvicorn 绑定地址 |
| `PORT` | `8000` | uvicorn 绑定端口 |
| `PROXY_HEADERS` | `true` | 信任反向代理传来的 `X-Forwarded-*` |
| `FORWARDED_ALLOW_IPS` | `*` | 信任哪些代理 IP 的转发头 |
| `SSL_KEYFILE` / `SSL_CERTFILE` | 未设置 | 在 uvicorn 内直接启用 HTTPS（见[快速上手](getting-started.zh-CN.md)） |
| `CORS_ORIGINS` | 未设置（回退到 `localhost:8501`） | 允许的来源域名，逗号分隔 |
| `APP_TIMEZONE` | `Asia/Shanghai` | 用于日志时间戳和所有定时任务 |
| `RPA_BACKUP_DIR` | `backups` | 数据库 dump 文件存放目录 |
| `RAP_DISABLE_MONTHLY_BACKUP` | `false` | 关闭后台月度备份循环 |
| `BACKUP_HOUR` | `2` | 每日备份检查运行的小时数（0–23，按 `APP_TIMEZONE`） |
| `MAX_UPLOAD_MB` | `50` | 允许的最大上传文件大小 |
| `REDIS_URL` | `redis://localhost:6379/0` | 可选——分布式限流用 |
| `LOGIN_MAX_ATTEMPTS` | `5` | 触发锁定前允许的登录失败次数 |
| `LOGIN_LOCKOUT_SECONDS` | `60` | 锁定时长 |
| `CACHE_TTL` | `300` | 分析接口结果缓存时长（秒） |
| `NL_SQL_PROVIDER` | `anthropic` | 中文问数据默认服务商 id |
| `NL_SQL_MODEL` | 未设置 | 默认模型（若对该服务商无效则回退到该服务商的第一个模型） |
| `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`MINIMAX_API_KEY`、`DEEPSEEK_API_KEY`、`MOONSHOT_API_KEY`、`ZHIPU_API_KEY` | 未设置 | 各服务商的 API Key——按需配置任意子集 |
| `OPENAI_BASE_URL` | 未设置 | 仅用于覆盖通用 `openai` 服务商的 base URL |
| `ANALYSIS_ROWS_CAP` | `5000` | 原始行预览接口返回的最大行数（聚合结果不受限） |
| `RAP_LEADER_LOCK_PATH` | 未设置 | 多后端进程间做 leader 选举用的文件路径 |
| `WECHAT_SYNC_TIMEOUT` | `300` | 一次完整微信同步的超时时间 |
| `WECHAT_REQUEST_TIMEOUT` | `10` | 每次微信 API 调用的超时时间 |
| `WECOM_HTTP_TIMEOUT` | `10.0` | 企业微信 API 调用的超时时间 |
| `WECOM_DEFAULT_ROLE` | `viewer` | 通过企业微信 OAuth 自动创建用户时赋予的角色 |
| `WECOM_AUTO_CREATE_USERS` | `true` | 企业微信首次登录时是否自动创建本地用户 |
| `WECOM_STREAMLIT_REDIRECT_URI` | 未设置 | 企业微信 OAuth 登录后跳回的地址 |
| `APP_URL` / `STREAMLIT_URL` | 未设置 | 某些流程中用于拼接绝对链接 |
| `WECHAT_AUTO_SYNC_ENABLED` | `false` | 启用每日后台微信同步 |
| `WECHAT_AUTO_SYNC_WINDOW_DAYS` | `170` | 每次同步覆盖的历史天数 |
| `WECHAT_AUTO_SYNC_HOUR` | `3` | 同步运行的小时数（0–23，按 `APP_TIMEZONE`） |

微信/企业微信的每账号凭证（`WECHAT_APP_ID_N`、`WECHAT_APP_SECRET_N`、
`WECHAT_ACCOUNT_NAME_N`、`WECOM_CORP_ID`、`WECOM_AGENT_ID`、
`WECOM_APP_SECRET`）也都是环境变量——多账号场景下的编号写法见
`.env.example`。
