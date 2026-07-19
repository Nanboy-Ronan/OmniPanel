# 微信自动同步

[English](wechat-auto-sync.md) | [中文](wechat-auto-sync.zh-CN.md)

## 为什么需要这个功能

微信的 DataCube 接口（`getarticletotaldetail`）只保留每篇文章发布后大约
**180 天**以内的阅读互动数据。一旦文章超过这个时间窗口，微信会清除其指标数据——无法再通过接口找回（对过期文章查询任意日期都会返回 errcode
61501）。

如果没有自动同步，任何在你第一次手动同步之前就已发布超过 6 个月的文章，都将无法再取回统计数据。自动同步通过每天运行一次、在数据过期前抓取每篇文章的指标，解决了这个问题。

## 工作原理

1. 在配置的时间点，一个后台 asyncio 任务每天被唤醒一次。
2. 计算本次同步的时间窗口：`[今天 − window_days, 今天 − 2 天]`。
   - 2 天的偏移量是为了应对微信 1–2 天的数据处理延迟。
   - 默认 170 天的窗口在 180 天过期之前留了 10 天的安全缓冲。
3. 对窗口内的每一天、对所有已配置的微信账号分别调用
   `getarticletotaldetail`。
4. 结果会 upsert 进 `media_posts` 和 `media_post_metrics_daily`。重复运行是安全的——已存在的行会被更新为最新值。
5. 每次运行都会记录进 `media_sync_runs`（状态、图文/指标数量、时间戳、错误信息）。

## 配置

所有配置项都是环境变量（也可以放进 `.env`）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `WECHAT_AUTO_SYNC_ENABLED` | `false` | 设为 `true` 启用定时任务 |
| `WECHAT_AUTO_SYNC_WINDOW_DAYS` | `170` | 每次运行覆盖的历史天数 |
| `WECHAT_AUTO_SYNC_HOUR` | `3` | 运行的小时数（0–23），按 `APP_TIMEZONE` |
| `APP_TIMEZONE` | `Asia/Shanghai` | 定时任务使用的 IANA 时区 |

### 启用所需的最小 `.env` 改动

```dotenv
WECHAT_AUTO_SYNC_ENABLED=true
```

这个定时任务使用的是和手动同步相同的 `WECHAT_APP_ID_N` /
`WECHAT_APP_SECRET_N` 凭证——具体的多账号编号写法见 `.env.example`，以及
[架构说明 → 配置参考](architecture.zh-CN.md#配置参考)。

## 新服务器的首次配置

在一台刚部署、没有历史数据的新服务器上，先通过管理后台（自媒体 → 微信同步）手动跑一次回填，参数设为：

- **开始日期**：今天 − 170 天
- **结束日期**：今天 − 2 天

之后，每日定时任务会持续维护数据的完整性。

## 如果某一次同步被跳过会怎样？

漏掉一次每日同步是无害的——下一次运行会覆盖同样的日期范围。漏掉几周也是可以恢复的，只要文章还在
180 天窗口之内。唯一的风险是这个定时任务被关闭超过约 10 天（安全缓冲期），这时窗口里最早的文章就会开始丢数据。

## 通知

每天的运行只会发送**一条** WeCom 通知——要么成功要么失败，绝不会两条都发——覆盖所有已配置的账号：

- **所有账号都同步成功**：发送一条汇总消息，包含每个账号的图文/指标数量，除非
  `WECOM_NOTIFY_SUCCESS` 被设为 `false`（参见
  [collector.md](collector.zh-CN.md)，与自动采集共用这个开关）。
- **有账号同步失败**：发送一条告警，列出每个失败账号，并附上本次运行中同步成功的账号列表。
- **完全没有配置任何账号**：不发送通知（没有可汇报的内容，这是既有的"跳过本次运行"逻辑，未改变）。

发送方式与自动采集的告警共用同一个 WeCom 自建应用（参见
[collector.md](collector.zh-CN.md) 中的"告警"一节）——不需要单独管理凭证。

每日的健康巡检（同样在 `app/scheduler.py` 中，参见
[collector.md](collector.zh-CN.md) 中的"数据管道健康巡检"一节）会单独兜底这个定时任务彻底停止运行的情况，因为单次运行通知只有在真的运行时才会触发。

## 监控

查看 `media_sync_runs` 表了解最近的运行历史：

```sql
SELECT account_id, status, start_date, end_date,
       posts_upserted, metrics_upserted, error_message, finished_at
FROM media_sync_runs
ORDER BY finished_at DESC
LIMIT 20;
```

失败的运行会显示 `status = 'failed'` 和对应的 `error_message`。常见原因：

| 错误 | 原因 | 处理方式 |
|---|---|---|
| `40001 invalid credential` | 同步过程中 access token 过期 | 凭证会自动刷新；通常是临时性的，下次运行就会恢复 |
| `61501` | 查询的日期超出保留窗口，或数据尚未生成 | 对很新的日期是正常现象；2 天的延迟偏移应该已经避免了这种情况 |
| `40164 not whitelisted` | 服务器出口 IP 不在微信白名单内 | 把服务器的出口 IP 加入微信公众平台的 IP 白名单 |

## 架构说明

这个定时任务在 `app/scheduler.py` 里实现，作为一个 `asyncio` 任务在
FastAPI 应用的 lifespan 中启动（`app/main.py`）。它直接从
`app/views/media/routes.py`（`_sync_one_wechat_account`、
`_ensure_env_wechat_accounts`）导入同步逻辑来调用，而不是走 HTTP，所以不需要自我调用。

应用关闭时（SIGTERM / uvicorn reload）该任务会被干净地取消，因为
`asyncio.CancelledError` 会被正常向上传播，而不是被吞掉。
