# Upgrading OmniPanel

## September–October 2026 update

This update adds all-platform weekly reports, recoverable uploads and background
jobs, daily backups, source freshness, filtered order exports, and shareable
dashboard views. SQL and natural-language queries now run against restricted
reporting views that exclude sensitive columns.

1. Back up your database and retain your existing environment configuration.
2. Install `requirements.txt` and stop the old API workers before migrating.
3. Run `python -m alembic upgrade head` with a database migration account that can
   create roles and grant privileges. This applies:
   - `0015`: assign legacy XHS posts without an account to a placeholder account;
   - `0016`: persist weekly notification text for delivery retries;
   - `0017`: create reporting views and the `rpa_analytics_readonly` / `rpa_app` roles.
4. Start the API and Streamlit processes. Check `/health` and the data freshness
   display, then verify a read-only SQL query and a weekly report.

The migration grants access to its executing role and to `rpa_app`. If your API
uses a separate database login, grant that login `rpa_app` membership using your
database administrator account. Keep the migration credentials separate from the
runtime login. Existing queries that reference private columns or explicitly use
`public.orders` must be changed to use the available reporting fields.

`BACKUP_DIR`, `BACKUP_KEEP`, and `PG_DOCKER_CONTAINER` replace their `RPA_`-prefixed
names; the old environment variables remain supported. Daily backups retain seven
files by default (`DAILY_BACKUP_KEEP`). `RAP_DISABLE_MONTHLY_BACKUP=true` disables
both scheduled daily and monthly backups. When restoring to a different cluster,
provision the reporting roles before restoring grants and views.

Uploads waiting for ingestion are stored under `BACKUP_DIR/upload_spool`. Persist
this directory across worker/container restarts and make it available to all API
workers. Interrupted jobs older than two hours are reclaimed by the background
leader. Never commit spool files, backups, browser sessions, or business exports.
The supplied Compose file persists this directory and backups in the
`omnipanel_backups` volume. The image includes PostgreSQL client tools for dumps
and restores.

For Enterprise WeChat login behind a reverse proxy, route `/auth/wecom/` on the
public dashboard origin to the API, and route `/` to Streamlit. Keep
`WECOM_STREAMLIT_REDIRECT_URI` set to that dashboard URL. The new login flow starts
in the browser so its OAuth state cookie survives a Streamlit reconnection.

Weekly reports remain opt-in via `WEEKLY_REPORT_ENABLED`. Set `PUBLIC_BASE_URL`
to your own deployment URL and test generation in the weekly report page before
enabling scheduled delivery. Report tables remain available without JavaScript;
interactive charts load Chart.js from its public CDN.

## 升级说明

先备份数据库、安装依赖并停止旧 API 进程，再使用有创建角色和授权权限的迁移账号运行
`python -m alembic upgrade head`。0015–0017 分别处理历史小红书账号归属、周报通知重试
和 SQL 查询的受限角色与视图。若 API 使用独立数据库登录账号，需由数据库管理员为其
授予 `rpa_app` 成员资格；运行账号与迁移账号应分开配置。

备份环境变量仍兼容旧名称。请持久化 `BACKUP_DIR/upload_spool`，供进程重启后的上传
恢复使用。周报默认关闭；将 `PUBLIC_BASE_URL` 设置为自己的地址，手动验证后再开启。
