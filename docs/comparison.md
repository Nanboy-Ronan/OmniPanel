# Comparison with similar projects

[English](comparison.md) | [中文](comparison.zh-CN.md)

This is an honest comparison, not a sales pitch. Where another project does
something better, it's listed under "worth borrowing" — some of these are
real candidates for a future OmniPanel release, not just praise.

## At a glance

| Project | Data source | What it actually ships |
|---|---|---|
| **OmniPanel** (this repo) | Official exports (Youzan/JD/Tmall, WeChat OA/XHS/Zhihu) | Self-hosted app: dashboards, cohort/identity analytics, SQL console, NL-to-SQL |
| [DA_Multi_Agent_Workflow](https://github.com/liuchaoqi-7/DA_Multi_Agent_Workflow) | Platform APIs + crawlers (Douyin Shop, Xiaohongshu, WeChat Video Channels, ad platforms) | n8n-orchestrated multi-agent ETL/analytics pipeline that syncs into Feishu |
| [ECommerceCrawlers](https://github.com/DropsDevopsOrg/ECommerceCrawlers) | Web scraping (Taobao, Xianyu, Weibo, Dianping, 20+ sites) | A collection of scraper code samples / learning exercises, not a deployable product |
| [data-api (Just One API)](https://github.com/justoneapi/data-api) | Web scraping, 40+ platforms | Hosted commercial pay-per-call data feed, no analytics layer |
| [global-ecommerce-data-scraping-solutions-cn](https://github.com/bodapi/global-ecommerce-data-scraping-solutions-cn) | Web scraping with anti-bot bypass, 20+ global platforms | Hosted commercial feed for cross-border price/review/competitive intelligence |

## What they do better — worth borrowing

### From DA_Multi_Agent_Workflow
- A **Supervisor → specialist-agent** routing architecture for Text-to-SQL,
  instead of OmniPanel's single-shot NL-to-SQL call — likely handles
  multi-step or ambiguous questions better.
- Formal **ODS → DWD → DIM → ADS** data-warehouse layering. OmniPanel's
  schema is one normalized `orders`/`customers` pair; a staging/mart split
  would be more rigorous as the schema grows.
- Syncs results into **Feishu**, where teams already work, instead of
  requiring a separate login. A "push a saved query result to chat/webhook"
  export would be a reasonable analogue for OmniPanel.
- **ASR + LLM material diagnostics** — analyzing ad video/audio creatives,
  not just text metrics. A content-analysis angle OmniPanel's self-media
  pages don't attempt.
- Covers **Douyin Shop and WeChat Video Channels**, two large platforms
  OmniPanel doesn't ingest at all.

### From data-api and the bodapi scraping API
- Both cover far more platforms (40+ and 20+ respectively), including ones
  OmniPanel has no connector for at all — Shopee, 1688, Kuaishou, Amazon,
  Temu, TikTok Shop. Useful signal for where to prioritize if connector
  coverage ever expands.
- Both ship a **usage/consumption console** (call history, balance, trend
  charts). OmniPanel's admin pages have an audit log but no equivalent
  "usage over time" visualization — worth adding to the operation-log page.
- bodapi's cross-border **competitive-intelligence** angle (price/review
  monitoring on competitors, not your own store) is a genuinely different,
  complementary value prop OmniPanel doesn't attempt.

### From ECommerceCrawlers
- Mostly a grab-bag of scraping techniques rather than an architecture to
  borrow, but it's a useful reference for cookie-free/anti-bot access
  patterns if OmniPanel ever needed a connector for a platform with no
  official export option.

## What OmniPanel does that none of them do

- **Zero scraping, zero ToS risk.** All four alternatives are scraping-based
  (or partly so) and explicitly engineer around anti-bot defenses. OmniPanel
  only ingests exports the merchant already legally owns: no connector that
  breaks on the next platform redesign, no legal grey area.
- **A real deployable product**, not a script collection or a paid API.
  Self-hosted FastAPI + Streamlit + PostgreSQL with auth, RBAC
  (viewer/analyst/admin), Enterprise WeChat SSO, and an append-only audit
  log — none of the four ship anything like this; they're code samples, a
  workflow stitched across n8n/MySQL/Feishu, or a hosted pay-per-call API
  with no UI of its own.
- **Business-rule-correct analytics, not just a data feed.**
  Cross-platform customer identity resolution (exact/fuzzy phone matching),
  cohort retention with right-censoring, platform-specific de-duplication —
  none of the comparison projects encode "what counts as one customer" or
  "which metrics are cumulative." They deliver raw rows or ad-hoc agent
  answers and leave correctness to the consumer.
- **NL-to-SQL with safety guardrails you control**: SELECT-only allow-list,
  forced read-only transaction, auto-`LIMIT`, full audit logging, provider
  keys that never leave your server. DA_Multi_Agent_Workflow has Text-to-SQL
  too, routed through an external n8n workflow without published safety
  constraints; the others have no query layer at all.
- **No recurring cost, no vendor lock-in.** data-api and bodapi are
  commercial pay-per-call services; OmniPanel is self-hosted and free to
  run on infrastructure you control.

## Bottom line

If you need ETL from platforms with no official export, or want a hosted
billing dashboard, those tools occupy a different, complementary niche.
OmniPanel's bet is narrower and deliberately deeper: only data you can
legally export, but every business rule that matters — customer identity,
retention, de-duplication — handled correctly out of the box.
