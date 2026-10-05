# Upgrading OmniPanel

## React workbench update — October 2026

The React + TypeScript workbench replaces the retired Python UI. All 22 routes
use the existing FastAPI backend, with new commerce BI, linked content/cooperation
analysis, source coverage, explicit permission guidance, and live database checks.

1. Install the updated Python dependencies and run `python -m alembic upgrade head`.
   There are no new revisions after `0017` in this frontend update.
2. Use Node.js 22.18+ to run `npm --prefix frontend ci` and
   `npm --prefix frontend run build`. Development uses `make ui` on port 5173.
3. Serve the built frontend at `/console/` and proxy same-origin `/api/` requests
   to FastAPI. The updated Compose file provides this using Nginx on port 5173.
4. Set `WECOM_CONSOLE_REDIRECT_URI` to your public `/console/` URL, configure that
   domain in Enterprise WeChat, and update `PUBLIC_BASE_URL` to the workbench URL.
   Legacy callback variables are accepted for compatibility but should not point
   to the retired service. Stop the old UI service after verifying the new one.
5. Check OAuth with your deployment credentials, roles, uploads, reports, and
   database status. For a local UI review without credentials, use the synthetic
   preview described in [frontend/README.md](../frontend/README.md).

Historical report charts are converted to safe inline SVG in the workbench's
sandboxed viewer. The viewer does not execute archived scripts or require a CDN.

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
4. Start the API and React processes. Check `/health` and the data freshness
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

For Enterprise WeChat login, use the same-origin `/api/` proxy and the callback
configuration described above. OAuth state is stored in a browser-owned cookie.

Weekly reports remain opt-in via `WEEKLY_REPORT_ENABLED`. Set `PUBLIC_BASE_URL`
to your own deployment URL and test generation in the weekly report page before
enabling scheduled delivery. Report tables remain available without JavaScript;
standalone HTML exports can load Chart.js from its public CDN, while the React
viewer converts those charts to local SVG without executing embedded scripts.

## 升级说明

本次 React + TypeScript 工作台替换旧 Python 前端。安装 Node.js 22.18+，构建
`frontend/` 并部署到 `/console/`，把同源 `/api/` 代理到 FastAPI。新的 Compose
在端口 5173 提供前端。将 `WECOM_CONSOLE_REDIRECT_URI` 与 `PUBLIC_BASE_URL` 更新为
实际工作台地址，并在企业微信配置回调域名；验证登录后停用旧前端。此次无新增数据库
迁移版本，已有部署仍需确认数据库达到 0017。

先备份数据库、安装依赖并停止旧 API 进程，再使用有创建角色和授权权限的迁移账号运行
`python -m alembic upgrade head`。0015–0017 分别处理历史小红书账号归属、周报通知重试
和 SQL 查询的受限角色与视图。若 API 使用独立数据库登录账号，需由数据库管理员为其
授予 `rpa_app` 成员资格；运行账号与迁移账号应分开配置。

备份环境变量仍兼容旧名称。请持久化 `BACKUP_DIR/upload_spool`，供进程重启后的上传
恢复使用。周报默认关闭；将 `PUBLIC_BASE_URL` 设置为自己的地址，手动验证后再开启。
