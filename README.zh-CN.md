# OmniPanel

**可自行部署的商城、内容与客户分析工作台。**

把分散在多个平台的订单、内容表现和达人合作数据汇集到一起。从导入报表、查看趋势，到分析客户与生成周报，让每个数字都有可查看的数据覆盖范围和统计口径。

[![CI](https://github.com/Nanboy-Ronan/OmniPanel/actions/workflows/ci.yml/badge.svg)](https://github.com/Nanboy-Ronan/OmniPanel/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)](LICENSE)

[English](README.md) · **简体中文**

[体验界面](#体验界面) · [支持的平台](#支持的平台) · [自行部署](#自行部署) · [文档](#文档)

![OmniPanel 经营概览：营业额趋势、渠道构成与数据覆盖状态](docs/images/screenshot_overview.png)

*所有截图均使用合成演示数据。仓库不包含生产账号、凭据或真实业务导出文件。*

## 从平台报表到经营分析

OmniPanel 面向同时经营多个商城、运营多个内容账号的团队，将平台格式识别、订单归一化、重复数据处理和分析放在同一个应用中完成。

| 想了解什么 | 从哪里开始 |
|---|---|
| 销售额如何变化，哪些渠道和商品贡献最大？ | 商城看板：同期对比、渠道构成、商品排行和地区分布 |
| 哪些客户会回来，多久回来一次？ | 客户档案、月度留存队列，以及区分精确和模糊结果的跨平台匹配 |
| 哪些内容获得了阅读与互动？ | 内容分布、发布趋势、账号指标和观众来源分析 |
| 达人合作的投入与表现如何？ | 蒲公英项目和达人对比，查看费用、曝光与互动成本 |
| 这一周发生了什么？ | 商城与内容周报，保留历史报告、数据完整性提示和可选的分析摘要 |
| 如何进一步查证？ | 只读 SQL、中文问数据，或按权限开放的 WorkBuddy 查询工具 |

营业额遵循统一的订单状态与退款规则。看板分别展示数据覆盖日期和导入时间，并提示不完整的统计区间。内容带货分析用于观察关联，跨平台客户的精确与模糊匹配结果分别展示。

## 工作台一览

| 商城分析 | 内容表现 |
|---|---|
| ![商城分析](docs/images/screenshot_analysis.png) | ![内容表现分析](docs/images/screenshot_content.png) |

| 达人合作 | WorkBuddy 连接 |
|---|---|
| ![蒲公英合作分析](docs/images/screenshot_pgy.png) | ![WorkBuddy 连接，使用合成数据](docs/images/screenshot_workbuddy.png) |

<details>
<summary>查看客户留存、身份匹配、SQL 与运维面板</summary>

| 客户留存 | 跨平台客户 |
|---|---|
| ![月度留存队列](docs/images/screenshot_cohort.png) | ![跨平台客户匹配](docs/images/screenshot_identity.png) |

| SQL 查询台 | 数据库状态 |
|---|---|
| ![SQL 查询台](docs/images/screenshot_sql.png) | ![数据库状态](docs/images/screenshot_database.png) |

![操作日志：账号与事件均为合成样例](docs/images/screenshot_logs.png)

</details>

## 体验界面

先用合成数据在本地浏览工作台。只需 **Node.js 22.18+**，无需数据库或企业微信凭据。

```sh
git clone https://github.com/Nanboy-Ronan/OmniPanel.git
cd OmniPanel
npm --prefix frontend ci
npm --prefix frontend run build
node frontend/scripts/preview-fixtures.mjs
```

打开 **http://127.0.0.1:5180/console/**，使用模拟登录进入。预览服务返回固定的演示数据，上传、授权等后台操作不代表真实部署。应用界面以中文为主。

## 支持的平台

| 平台 | 数据内容 | 接入方式 |
|---|---|---|
| 有赞 · 天猫 | 订单与客户 | 上传平台导出文件 |
| 京东 | 订单与客户 | 上传导出文件，或通过浏览器采集商家后台已展示的订单列表 |
| 微信公众号 | 文章、流量与粉丝 | 定时调用 API 同步 |
| 小红书 | 笔记、互动、账号指标与观众来源 | 上传导出文件或浏览器采集；具体能力随数据类型而异 |
| 微信视频号 · 知乎 | 内容与表现指标 | 上传导出文件或浏览器采集 |
| 蒲公英 | 达人合作与项目表现 | 上传导出文件或浏览器采集 |

导入时识别支持的列结构，将记录转换为统一的分析数据。重复文件和订单会去重，后续导出可以更新订单状态。浏览器采集使用经过授权的平台登录态；登录过期或平台页面变化时，可能需要重新登录或更新采集适配。详见[采集器文档](docs/collector.zh-CN.md)。

## WorkBuddy 与 AI 查询

**连接 WorkBuddy。** 分析员和管理员通过 OAuth 或个人令牌授予查询范围。只读工具返回有数量限制的汇总分析与媒体明细，支持审计和撤销授权，不开放客户身份字段、采集凭据或任意 SQL。

**生成经营周报。** 可选的 WorkBuddy 周报服务通过任务队列处理汇总快照，校验分析结论引用的指标，并支持重试和企业微信推送。采集账号负责人还可以授权本地浏览器登录，修复支持平台的采集登录态。

**中文问数据。** 输入问题，查看生成的 SQL，再通过受限的报表视图查询结果。查询使用独立的只读数据库账号。

这些能力需要单独配置。外部 AI 服务按需启用，启用后会将相应输入发送给所选服务商。详见 [WorkBuddy 接入说明](docs/workbuddy-integration.md)和[中文问数据文档](docs/nl-to-sql.zh-CN.md)。

## 自行部署

应用使用 **React + TypeScript、FastAPI 和 PostgreSQL**。Docker Compose 包含数据库、迁移任务、API，以及由 Nginx 提供的前端。

在仓库根目录运行：

```sh
cp .env.example .env
# 先完成 .env 配置，再启动应用。
docker compose up --build
```

登录前需要配置：

| 配置项 | 用途 |
|---|---|
| `RAP_SECRET` | 足够长的随机认证签名密钥 |
| `WECOM_CORP_ID`、`WECOM_AGENT_ID`、`WECOM_APP_SECRET` | 自己的企业微信应用凭据 |
| `WECOM_CONSOLE_REDIRECT_URI` | 可访问且已在企业微信登记的 `/console/` 回调地址 |
| `PUBLIC_BASE_URL` | 对外部署域名，用于授权和报告链接 |

前端监听 **http://localhost:5173/console/**。真实登录需要配置符合企业微信要求的回调地址；无需凭据的界面体验请使用上面的预览服务。首个认证用户成为管理员，后续新增用户默认等待管理员审批。

Compose 已提供容器内数据库连接，并持久化数据库和备份卷。对外部署时，请按自己的环境配置 HTTPS、数据库凭据和可信代理。SQL 与中文问数据还需要配置 `RAP_SQL_CONSOLE_DATABASE_URL`；未配置时，这两项查询功能保持关闭。

- **首次安装：** [快速开始](docs/getting-started.zh-CN.md)
- **已有部署：** [升级说明](docs/upgrading.md)——启动本版本前，先将数据库迁移至 `0026`
- **完整配置：** [.env.example](.env.example)

## 日常运行与维护

查看者、分析员和管理员角色控制功能访问。审计日志覆盖认证、查询、导出以及数据和权限变更。后台任务支持主节点选举、异常重启、上传中断恢复和采集健康检查。数据库备份包含校验、保留策略、可选的异地复制钩子及恢复演练脚本。

<details>
<summary>查看系统架构</summary>

![OmniPanel 系统架构](docs/images/architecture.zh-CN.png)

React 工作台通过同源代理调用 FastAPI。API 负责数据导入、分析、认证和定时任务，数据保存在 PostgreSQL 中；SQL 查询使用独立的受限账号。可选接入提供自动采集、AI 查询与通知能力。

数据模型与服务边界详见[架构文档](docs/architecture.zh-CN.md)。

</details>

## 文档

| 开始使用 | 扩展与维护 |
|---|---|
| [安装与登录](docs/getting-started.zh-CN.md) | [系统架构](docs/architecture.zh-CN.md) |
| [前端开发与合成预览](frontend/README.md) | [采集器配置](docs/collector.zh-CN.md) |
| [WorkBuddy 接入](docs/workbuddy-integration.md) | [公众号自动同步](docs/wechat-auto-sync.zh-CN.md) |
| [中文问数据](docs/nl-to-sql.zh-CN.md) | [维护与密钥轮换](docs/maintenance.zh-CN.md) |
| [合成数据测试](docs/testing.zh-CN.md) | [版本升级](docs/upgrading.md) |

## 参与贡献

欢迎改进平台适配、统计口径、测试和界面。请先阅读[贡献指南](CONTRIBUTING.md)，或通过 [Issue](https://github.com/Nanboy-Ronan/OmniPanel/issues) 提交可复现的例子。贡献中的样例与截图请使用合成数据，不要提交凭据、真实导出文件或组织专属标识。

安全问题请按 [SECURITY.md](SECURITY.md) 中的方式报告。

## 许可证

[GNU Affero General Public License v3.0](LICENSE)。
