# 自动采集代理（创作者后台导出）

[English](collector.md) | [中文](collector.zh-CN.md)

## 为什么需要这个功能

小红书和知乎都没有公开的数据分析接口。要拿到账号级别的内容指标（曝光、点击率、观看时长等），只能靠人登录创作者后台并手动点击"导出"。这个子系统把这个过程自动化：复用一份保存下来的浏览器登录状态打开后台、触发导出，再把下载到的文件通过**已有的**
`/media/xhs/upload` / `/media/zhihu/upload` 接口上传——ETL、去重、审计日志和手动上传完全一致。

它**不会**取代手动上传，只是把人已经在做的"点击 → 下载 → 上传"这套动作变成无人值守。

## 架构

- `app/collector/` ——一个独立的 Python 包，使用
  [Playwright](https://playwright.dev/python/)（同步 API）。**除了
  `app/collector/` 之外没有任何模块引入 Playwright**——FastAPI 后端和
  Streamlit 前端都不会加载浏览器。
- 作为独立进程运行（`python -m app.collector collect`），与 Web
  应用进程互不依赖——用什么方式触发取决于你的部署方式（cron、systemd
  timer、定时 CI 任务，或手动执行都可以）。
- 上传通过 API 完成，使用一个专用的服务账号（走
  `app/ui/api_client.py` 的 `APIClient`）——和人在页面上点"上传"走的是完全相同的代码路径。
- 每次运行的状态直接写入 Postgres（`CollectorRun` / `collector_runs`
  表），与微信自动同步的 `MediaSyncRun` 是同一套模式。
- 可选的 WeCom 告警（`app/utils/wecom_bot.py`）会在会话过期、下载超时或上传失败时触发——通过企业微信自建应用的应用消息接口（`cgi-bin/message/send`）发送，而不是群机器人
  webhook，原因见下方"告警"一节。

## 首次配置：在本机完成登录（bootstrap-login）

创作者后台的登录状态无法无头（headless）创建——必须由人完成一次真实登录。请在自己的电脑上执行，不要在服务器上：

```bash
pip install -r requirements.txt
playwright install chromium

python -m app.collector bootstrap-login --platform xhs --out xhs_session.json
# 会弹出一个 Chromium 窗口，停在小红书登录页（pro.xiaohongshu.com/login）。
# 用手机号 + 短信验证码登录——小红书创作者账号用的是短信登录，不是扫码。
# 如果这个手机号关联了多个小红书专业号，登录后会先进入一个选择账号的页面——点击你希望这份
# session 文件代表的那个账号。脚本会自动等过这一步，在真正进入后台首页后写出
# xhs_session.json（整体超时 8 分钟）。

python -m app.collector bootstrap-login --platform zhihu --out zhihu_session.json
```

然后通过 Streamlit 管理页面「自动采集」（仅管理员可见）上传生成的 JSON
文件：选择平台（小红书还需要选择对应账号——如果一个手机号关联多个账号，每个账号需要单独一份
session 文件），上传即可。文件会写到服务器端的
`{COLLECTOR_DIR}/sessions/xhs_{account_id}.json` 或
`.../zhihu.json`，权限 `0600`。

**会话有效期**：创作者后台的登录状态通常能维持几周。每次采集成功都会把（已轮换的）cookie
重新写回同一个文件，实际上会不断延长有效期。当会话最终过期时，采集器能检测到（反复重定向到登录流程且始终无法恢复——为什么*短暂*的重定向是正常现象、不会被误判为过期，见下面"小红书的鉴权是基于
CAS 的"一节），会在对应的 `CollectorRun` 上记录
`status=session_expired`，如果配置了 WeCom
告警，还会推送一条指明平台/账号的提醒。修复方式：针对该平台重新执行一次
`bootstrap-login` 并重新上传。

## 小红书的鉴权是基于 CAS 的——改动 `xhs.py` 前请先读这段

简要版本（完整过程见 `app/collector/xhs.py` 和 `browser.py` 的模块 docstring），因为这里很容易"修"出一个更差的版本：

- 登录入口在 `pro.xiaohongshu.com/login`。真正的笔记级数据在**另一个子域名**
  `creator.xiaohongshu.com` 上。
- 认证访问 `creator.xiaohongshu.com` 需要一次 **CAS
  服务票据交换**，不是简单的共享 cookie SSO。一次全新的跳转会先短暂返回
  401 并显示一个看起来像登录页的 URL，持续几秒钟——*同时后台在静默完成票据交换*——之后页面会自动跳回真实内容，通常约
  6 秒左右。`collect_xhs()` 里的登录检测（`_goto_and_check_login`）刻意等待最多
  15 秒，只有等到时间用完、登录态*依然*存在时才会判定为
  `SessionExpiredError`——不要缩短这个等待时间，也不要改成一看到登录态
  URL 就立刻返回。
- `browser.py` 里的 `open_context()` **没有**给 Chromium 传
  `--disable-blink-features=AutomationControlled`。这是一个常见的反检测参数，但在这里起了反效果：小红书的风控识别出了这个参数*本身*的存在（真实用户浏览器都没有这个参数），并因此强制让原本有效的会话过期。不要在没有重新做真实验证的情况下把它加回去。
- `COLLECTOR_HEADLESS=true`（真正的无头模式，没有可见窗口）**尚未**在小红书上验证过——只有
  `headless=False` 经过了完整的端到端确认。在有人单独验证无头模式之前，请保持
  `COLLECTOR_HEADLESS=false`，并在没有显示器的服务器上用虚拟显示（例如
  `xvfb-run`）来运行。

## 配置

所有配置项都是环境变量（也可以放进 `.env`）：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `COLLECTOR_ENABLED` | `false` | 总开关；为 `false` 时 `collect` 会立即以退出码 0 结束 |
| `COLLECTOR_XHS_ENABLED` | `true` | 本次运行是否包含小红书账号 |
| `COLLECTOR_ZHIHU_ENABLED` | `true` | 本次运行是否包含知乎（文章+回答） |
| `COLLECTOR_DIR` | `data/collector` | session / 下载 / 调试文件的根目录 |
| `COLLECTOR_HEADLESS` | `false` | 目前只有 `false`（配合服务器上的虚拟显示）在小红书上验证通过；真正的无头模式尚未验证，见上文 |
| `COLLECTOR_API_URL` | `http://127.0.0.1:8000` | 采集器上传数据的目标地址 |
| `COLLECTOR_SERVICE_EMAIL` / `COLLECTOR_SERVICE_PASSWORD` | — | 服务账号凭证（viewer 角色即可） |
| `COLLECTOR_NAV_TIMEOUT_SECONDS` | `45` | Playwright 页面跳转超时时间 |
| `COLLECTOR_DOWNLOAD_TIMEOUT_SECONDS` | `120` | 等待导出文件下载完成的时长 |
| `COLLECTOR_DEBUG_KEEP` | `20` | 失败时保留的截图+HTML 组合数量上限 |
| `COLLECTOR_COLLECT_RETRIES` | `1` | 下载超时（临时性失败）在判定整次运行失败前的重试次数 |
| `WECOM_ALERT_TOUSER` | `@all`（可选） | 通知接收人，见下方"告警"一节 |
| `WECOM_NOTIFY_SUCCESS` | `true` | 运行全部成功时是否也发送 WeCom 通知——设为 `false` 则只在失败时告警 |

告警功能还需要 `WECOM_CORP_ID` / `WECOM_AGENT_ID` /
`WECOM_APP_SECRET`（与企业微信 OAuth 登录共用，不是采集专属的变量）。

## 告警

每次运行只会发送**一条** WeCom 通知——要么成功要么失败，绝不会两条都发——这样一次运行结束后不会处于"两边都没消息"的沉默状态：

- **所有目标都成功**：发送一条汇总消息，包含每个目标的入库行数，除非设置了
  `WECOM_NOTIFY_SUCCESS=false`。`--dry-run` 模式（本地调试用，不算真实运行）完全不发送。
- **有目标失败**（会话过期、下载超时、上传被拒绝，或其他任何错误）：发送一条告警，列出每个失败项，并附上本次运行中确实成功的目标列表——避免部分失败时其余成功项被完全掩盖。

发送方式是企业微信自建应用的应用消息接口（`cgi-bin/message/send`），复用
`WECOM_CORP_ID` / `WECOM_AGENT_ID` /
`WECOM_APP_SECRET`——与企业微信 OAuth
登录（`app/views/wecom_auth.py`）用的是同一套凭证，不需要额外管理一份密钥。只要这三个变量中有任何一个未配置，通知会被静默跳过，不影响其他功能。

**为什么不用群机器人 webhook**（企业微信自定义群机器人）：一些企业微信组织把自定义群机器人的创建权限关闭了，且没有自助开启的方式。用应用消息发送可以完全绕开这个权限——任何自建应用都能给它可见的用户发消息，不需要群机器人权限。

接收人默认是 `WECOM_ALERT_TOUSER=@all`（该应用可见的所有用户）。如果要指定具体的人，使用其企业微信
userid（对任何曾通过企业微信 OAuth
登录过的用户，可以用 `SELECT wecom_userid FROM "user" WHERE wecom_userid IS
NOT NULL` 查到），多个用 `|` 分隔，例如
`WECOM_ALERT_TOUSER=userid1|userid2`。

## 命令行

```bash
# 本地、每个平台只需一次：
python -m app.collector bootstrap-login --platform xhs --out xhs_1.json
python -m app.collector bootstrap-login --platform zhihu --out zhihu.json

# 手动运行（服务器上，或本地对着本地后端跑）：
python -m app.collector collect                                # 所有已启用的目标
python -m app.collector collect --platform xhs --account-id 3
python -m app.collector collect --platform zhihu --content-type article
python -m app.collector collect --dry-run                       # 只下载不上传
python -m app.collector collect --headed                        # 强制显示窗口（默认本来就是有头模式，见上方 COLLECTOR_HEADLESS）

# 只检查某个已保存的 session 是否仍然有效，不触发下载：
python -m app.collector verify-session --platform xhs --account-id 3
```

## 选择器维护（迟早会失效）

`app/collector/xhs.py` 和 `app/collector/zhihu.py`
都把所有和后台页面相关的 URL / 选择器集中放在文件顶部的一个常量区块里。

**小红书已经端到端完整验证**（登录 → 导出 → 上传 → `xhs_posts`
入库，且确认重复运行是幂等的）。**知乎的选择器还只是未验证的占位实现**——预计会遇到和小红书当初类似的各种问题（域名不对、登录方式不对、鉴权跳转的时序问题），需要针对真实账号预留真正的调试时间，而不只是改改选择器。

当后台页面改版、运行开始报 `download_failed` 时：

1. 查看 `{COLLECTOR_DIR}/debug/` 下这次失败运行留下的截图+HTML
   文件（命名格式 `{timestamp}_{tag}.png`/`.html`）。
2. 更新对应模块里的常量——理论上不需要改其他任何东西。
3. 重新部署。

优先使用文本/角色定位器（例如 `button:has-text("导出数据")`）而不是 CSS
class——小红书/知乎的 class 名是每次前端构建都会变的哈希值。

## 监控

```sql
SELECT platform, account_id, content_type, status, rows_upserted,
       error_message, started_at, finished_at
FROM collector_runs
ORDER BY started_at DESC
LIMIT 20;
```

也可以在 Streamlit「自动采集」管理页面里查看（session 状态 + 最近运行列表）。

## 数据管道健康巡检

单次运行的通知（本采集器的，以及微信自动同步的——见
[wechat-auto-sync.zh-CN.md](wechat-auto-sync.zh-CN.md)）只有在真的运行时才会触发，如果整个流水线彻底停止运行（定时任务被关闭、进程崩溃、宿主机配置出问题），这类通知不会有任何反应。`app.scheduler.watchdog_loop`
是每日兜底检查：检查每个已启用的流水线是否有近期的运行记录，只有在状态看起来异常时才告警——健康的一天不会产生任何消息（避免和上面的单次运行成功通知重复刷屏）。

| 变量 | 默认值 | 说明 |
|---|---|---|
| `WATCHDOG_ENABLED` | `true` | 每日健康巡检的总开关 |
| `WATCHDOG_HOUR` | `9` | 巡检运行的小时数（0-23，按 `APP_TIMEZONE`） |
| `WATCHDOG_MAX_AGE_HOURS` | `30` | 采集器 / 微信同步：超过这个时长没有运行记录就告警 |
| `WATCHDOG_BACKUP_MAX_AGE_DAYS` | `35` | 每月备份：超过这个时长没有成功备份就告警（30 天周期 + 缓冲） |

会检查的流水线（各自的功能未启用时会跳过）：

- **采集器**（`COLLECTOR_ENABLED=true`）：`collector_runs.started_at` 的最新记录。
- **微信自动同步**（`WECHAT_AUTO_SYNC_ENABLED=true`）：`media_sync_runs.started_at`
  中 `source = 'api'` 的最新记录——手动上传 xlsx 也会写入新的一行，但不能因此掩盖自动同步实际上已经停止运行的事实。
- **每月备份**（除非 `RAP_DISABLE_MONTHLY_BACKUP=true`）：`app/db/backup.py`
  已经维护的 `.last_monthly_backup` 标记文件。

## 永远需要人工的部分

- 真实的后台登录（短信验证码，以及手机号关联多个账号时的账号选择）。
- 后台的反爬 / 风控行为（什么会触发它，是否会随时间变化）。
- 选择器的有效性——除了 `download_failed` / 空导出这种运行时报错，没有任何自动化手段能提前发现后台页面的静默改版。
