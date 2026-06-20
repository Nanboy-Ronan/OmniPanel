# OmniPanel

[![CI](https://github.com/Nanboy-Ronan/OmniPanel/actions/workflows/ci.yml/badge.svg)](https://github.com/Nanboy-Ronan/OmniPanel/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/Nanboy-Ronan/OmniPanel)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue)](https://www.python.org/)

[English](README.md) | [中文](README.zh-CN.md)

一个面向中国电商和自媒体数据的自托管分析平台——基于各平台**官方提供的导出数据**，不涉及任何爬虫。

把你从店铺后台和内容后台导出的表格上传进来，OmniPanel 会把它们归一化成统一的数据结构，然后你就能得到客户分析、内容表现指标，以及一个支持自然语言提问的即席 SQL 查询台。

## 为什么做这个

通用 BI 工具不理解平台特有的业务口径（比如不同平台上"客户"该如何定义、哪些阅读指标是累计值不能直接求和、多行订单要怎么去重）。爬虫类工具处在法律灰色地带，而且经常因为平台改版而失效。OmniPanel 介于两者之间：只摄入你自己合法拥有的**权威导出数据**，并把平台业务口径编码进系统，让数字开箱即用就是对的。

## 功能

- **多平台订单摄入** —— 直接丢进常见电商平台的导出文件；通过列指纹自动识别平台来源，归一化到统一结构。
- **客户分析** —— 新老客户拆分、复购率与复购周期、单客户订单历史、地区分布。
- **自媒体分析** —— 自有公众号及内容平台的图文/笔记指标，以及内容→销量的归因视图。
- **SQL 查询台** —— 带严格防护的只读即席查询工具（仅允许 SELECT/WITH、自动加 LIMIT、语句超时、全程审计日志）。
- **中文问数据 (NL-to-SQL)** —— 用中文直接提问，自动生成 SQL 并执行返回结果。支持多家大模型服务商（Anthropic、OpenAI、MiniMax、DeepSeek、月之暗面、智谱）；API Key 只保存在服务端，用户在下拉框里选服务商和模型。
- **角色与审计** —— viewer / analyst / admin 三级角色；每一次查询和写操作都会写入操作日志。

## 架构

```
Streamlit 前端  ──HTTP──▶  FastAPI 后端  ──▶  PostgreSQL
                              │
                              ├─ ETL：识别平台 → 归一化 → 入库
                              ├─ 分析接口
                              └─ SQL 查询台 + 中文问数据
```

- **后端：** FastAPI（`app/`），SQLAlchemy + asyncpg，Alembic 做数据库迁移。
- **前端：** Streamlit（`app/ui/`），通过 HTTP 调用后端。
- **数据库：** PostgreSQL。

## 快速开始

依赖：Python 3.13+ 和一个 PostgreSQL 实例。

```bash
# 1. 安装依赖
python -m pip install -r requirements.txt

# 2. 配置环境变量
cp .env.example .env
#    编辑 .env：设置 RAP_DATABASE_URL、RAP_SECRET，以及（可选）某个大模型的 API Key

# 3. 执行数据库迁移
make db-upgrade            # 等价于：alembic upgrade head

# 4. 启动后端（FastAPI，端口 8000）
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 5. 在另一个终端启动前端（Streamlit，端口 8501）
streamlit run app/ui/dashboard.py
```

打开 Streamlit 页面后，注册第一个用户（会自动成为 admin），就可以开始上传导出文件了。

## 配置

所有配置项都来自环境变量（完整列表见 `.env.example`）。最核心的几项：

| 变量 | 用途 |
|---|---|
| `RAP_DATABASE_URL` | PostgreSQL 连接串（`postgresql+asyncpg://…`） |
| `RAP_SECRET` | 用于签发登录令牌的密钥——请设置一个强随机值 |
| `CORS_ORIGINS` | 允许访问 API 的来源域名，逗号分隔 |

### 启用中文问数据 (NL-to-SQL)

可选功能。给你想用的服务商配置好 API Key 即可；用户会在 SQL 查询台的下拉框里选择服务商和模型。Key 永远不会离开服务端。

```bash
NL_SQL_PROVIDER=minimax            # 默认服务商
MINIMAX_API_KEY=...                # 或 ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / ……
```

不配置任何 Key 时，该功能只会返回 503，不影响其他功能。

## 文档

- [快速上手](docs/getting-started.zh-CN.md) —— 安装、配置、运行、创建首个管理员
- [架构说明](docs/architecture.zh-CN.md) —— 各组件、数据模型、ETL 流程、角色权限、API 一览
- [中文问数据 (NL-to-SQL)](docs/nl-to-sql.zh-CN.md) —— 工作原理、服务商注册表、如何新增服务商
- [测试指南](docs/testing.zh-CN.md) —— 如何跑测试、合成数据集、依赖真实文件的烟雾测试
- [微信自动同步](docs/wechat-auto-sync.zh-CN.md) —— 公众号指标的每日后台自动同步

## 测试

测试套件需要一个可访问的 PostgreSQL 服务器。它不会动你的业务数据库——每次运行都会在同一服务器上新建并在结束后删除一个临时的 `*_test_*` 数据库。

```bash
# 指向你的数据库服务器（只用于建临时库，不会写入这个连接串本身指向的库）
export PG_TEST_URL=postgresql://user:pass@127.0.0.1:5432/postgres
make test                  # 等价于：pytest -q
```

## 数据库迁移

数据库结构变更通过 Alembic 管理。常用命令：

```bash
make db-upgrade                          # 应用所有未执行的迁移
make db-new-migration msg="add table x"  # 根据 ORM 改动自动生成迁移文件
make db-check                            # 校验数据库是否已是最新版本
```

## 贡献

欢迎提 Issue 和 PR——见 [CONTRIBUTING.md](CONTRIBUTING.md)（英文）。

## 许可证

见 [LICENSE](LICENSE)。
