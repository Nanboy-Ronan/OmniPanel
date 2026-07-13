# 依赖维护与升级节奏（Dependency Maintenance）

[English](maintenance.md) | [中文](maintenance.zh-CN.md)

`requirements.txt` 全部**精确锁定版本**（pin），这是刻意的：自托管部署场景下，
可复现的环境比"永远最新"更重要。代价是依赖不会自己更新，需要一个固定节奏主动升级，
否则会慢慢积累已知漏洞（CVE）和难以逾越的版本鸿沟。

## 节奏

- **每季度一次**常规升级（1、4、7、10 月各一次）。
- **安全公告随到随办**：收到 GitHub Dependabot / CVE 通报涉及下列关键包时，不等季度，立即处理。

## 关键包（优先升级）

安全敏感，任何已知漏洞都要尽快跟：

| 包 | 作用 | 风险面 |
|---|---|---|
| `cryptography` | JWT / TLS 底层 | 加密漏洞 |
| `fastapi-users` / `fastapi-users-db-sqlalchemy` | 认证框架 | 认证绕过 |
| `fastapi` / `starlette` | Web 框架 | 请求处理、DoS |
| `uvicorn` / `h11` / `httptools` | HTTP 服务器/解析 | 请求走私、DoS |
| `argon2-cffi` / `bcrypt` | 密码哈希 | 哈希强度 |
| `pydantic` / `pydantic-settings` | 输入校验 | 校验绕过 |
| `asyncpg` / `psycopg2-binary` / `sqlalchemy` | 数据库驱动/ORM | 注入、连接处理 |
| `anthropic` / `openai` | NL2SQL 服务商 SDK | 依赖链 |

其余包（altair、streamlit、pandas 等）跟随季度节奏即可。

## 升级流程

```bash
# 1. 开分支
git checkout -b chore/deps-YYYYQN

# 2. 看哪些锁定版本已过期
make deps-outdated

# 3. 扫已知漏洞（不改动运行环境，用 pipx 临时跑）
make deps-audit

# 4. 在 requirements.txt 里改版本号（关键包优先；一次别跨太多大版本）
#    改完在项目 venv 里 pip install -r requirements.txt

# 5. 测试是唯一的门禁——全部用例必须通过
make test

# 6. 冒烟：起服务，跑一次上传 + 一个分析页 + 登录（见 docs/getting-started.zh-CN.md）

# 7. 绿了再提交；一个 commit 说明升了哪些包、为什么
```

**原则**：测试全绿是合并的硬门槛。一次升级别同时跨多个包的大版本（major），
出问题难定位——关键安全包可以单独一个 commit，其余按生态批量升。

## 工具说明

- `make deps-outdated` — 列出 `requirements.txt` 中已有更新版本的包。
- `make deps-audit` — 用 `pipx run pip-audit` 扫描锁定版本里的已知 CVE。
  需要本机装了 [`pipx`](https://pipx.pypa.io/)；它在临时环境里跑，不会污染项目或 base 环境。
  没有 pipx 时：`python -m pip install pip-audit && python -m pip_audit -r requirements.txt`
  （建议在项目 venv 内，不要在 anaconda base 里装）。
