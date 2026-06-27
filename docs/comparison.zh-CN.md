# 与同类项目的对比

[English](comparison.md) | [中文](comparison.zh-CN.md)

这是一份诚实的对比，不是自我推销。别的项目做得好的地方会列在"值得借鉴"里——其中一些是
OmniPanel 未来版本的真实候选项，不只是客套话。

## 一览

| 项目 | 数据来源 | 实际提供的是什么 |
|---|---|---|
| **OmniPanel**（本仓库） | 官方导出数据（有赞/京东/天猫，微信公众号/小红书/知乎） | 自托管应用：仪表盘、队列留存与跨平台身份识别、SQL 查询台、中文问数据 |
| [DA_Multi_Agent_Workflow](https://github.com/liuchaoqi-7/DA_Multi_Agent_Workflow) | 平台 API + 爬虫（抖音小店、小红书、视频号、广告平台） | 由 n8n 编排的多智能体 ETL/分析流水线，结果同步进飞书 |
| [ECommerceCrawlers](https://github.com/DropsDevopsOrg/ECommerceCrawlers) | 网页爬虫（淘宝、闲鱼、微博、点评等 20+ 网站） | 一组爬虫代码示例/练习，不是可部署的产品 |
| [data-api (Just One API)](https://github.com/justoneapi/data-api) | 网页爬虫，覆盖 40+ 平台 | 托管型按调用计费的数据接口服务，没有分析层 |
| [global-ecommerce-data-scraping-solutions-cn](https://github.com/bodapi/global-ecommerce-data-scraping-solutions-cn) | 带反爬绕过的网页爬虫，覆盖 20+ 全球平台 | 面向跨境的托管型商品价格/评论/竞品情报数据服务 |

## 它们做得更好、值得借鉴的地方

### 来自 DA_Multi_Agent_Workflow
- **Supervisor → 专家子 agent** 的路由架构来做 Text-to-SQL，而不是 OmniPanel
  现在单次调用式的中文问数据——前者大概率更能处理多步骤或有歧义的问题。
- 规范的 **ODS → DWD → DIM → ADS** 数据仓库分层。OmniPanel 现在是一张归一化的
  `orders`/`customers`，随着 schema 增长，引入分层会更严谨。
- 把结果同步进**飞书**，团队本来就在那里办公，不需要额外登录一个系统。
  OmniPanel 可以考虑类似的"把保存的查询结果推送到群/webhook"的导出能力。
- **ASR + 大模型素材诊断**——分析广告视频/音频素材，不只是文本指标。这是
  OmniPanel 自媒体分析目前完全没有涉及的内容分析角度。
- 覆盖了**抖音小店和视频号**，这是 OmniPanel 完全没有接入的两个大平台。

### 来自 data-api 和 bodapi 这两个爬虫 API
- 两者覆盖的平台数量都远超 OmniPanel（分别 40+ 和 20+），包括 OmniPanel
  完全没有连接器的平台——Shopee、1688、快手、Amazon、Temu、TikTok Shop。如果
  OmniPanel 未来要扩展连接器覆盖范围，这是值得参考的优先级信号。
- 两者都有**用量/消费控制台**（调用历史、余额、趋势图）。OmniPanel 的管理后台
  有审计日志，但没有同等的"用量随时间变化"可视化——值得加到操作日志页面里。
- bodapi 的跨境**竞品情报**角度（监控竞争对手的价格/评论，而不是自己企业的数据）
  是一个 OmniPanel 完全没有尝试过、但互补的真实价值主张。

### 来自 ECommerceCrawlers
- 架构层面没什么可借鉴的（更像是爬虫技巧的大杂烩），但如果 OmniPanel 未来需要
  为一个没有官方导出选项的平台做连接器，它的免 cookie/反爬接入方式是有用的参考。

## OmniPanel 做到了、但它们都没做到的事

- **零爬虫，零 ToS 风险。** 上面四个项目都基于爬虫（或部分基于），并且明确地
  在和反爬机制博弈。OmniPanel 只摄入商家本就合法拥有的导出数据：不会因为平台
  改版就连接器失效，也没有法律灰色地带。
- **一个真正可部署的产品**，不是脚本合集，也不是付费 API。自托管的
  FastAPI + Streamlit + PostgreSQL，带鉴权、角色权限（viewer/analyst/admin）、
  企业微信单点登录、只追加的审计日志——四个对比项目都没有这样完整的东西：
  它们是代码示例、靠 n8n/MySQL/飞书拼起来的工作流，或者是没有自己界面的
  按调用付费托管 API。
- **业务口径正确的分析，而不只是数据接口。** 跨平台客户身份识别（精确/模糊
  手机号匹配）、带右删失处理的队列留存、按平台的去重逻辑——对比的项目都没有
  编码"什么算同一个客户""哪些指标是累计值"这类规则。它们交付的是原始行数据
  或 agent 临场给出的答案，正确性留给使用者自己处理。
- **带真实安全防护、且完全由你掌控的中文问数据**：仅允许 SELECT 的白名单、
  强制只读事务、自动加 LIMIT、完整审计日志，服务商 API Key 永不离开你的服务器。
  DA_Multi_Agent_Workflow 也有 Text-to-SQL，但是经由外部 n8n 工作流，没有公开
  的安全约束说明；其他几个项目根本没有查询层。
- **没有持续费用，没有厂商锁定。** data-api 和 bodapi 都是按调用付费的商业服务；
  OmniPanel 是自托管的，可以免费跑在你自己掌控的基础设施上。

## 结论

如果你需要从没有官方导出的平台做 ETL，或者想要一个带计费看板的托管服务，
上面这些工具占据的是另一个、与 OmniPanel 互补的细分领域。OmniPanel 的取舍更窄
但更深：只用你能合法导出的数据，但把客户身份、留存、去重这些真正要紧的业务
规则都做对、开箱即用。
