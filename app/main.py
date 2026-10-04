# rap/app/main.py
import asyncio
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from sqlalchemy import text
from .config import settings

# Align Python logging timestamps with APP_TIMEZONE (default Asia/Shanghai).
# Must run before any logger is created.
if settings.app_timezone in ("Asia/Shanghai", "Asia/Beijing", "PRC", "CST"):
    logging.Formatter.converter = time.localtime
    os.environ.setdefault("TZ", "Asia/Shanghai")
    try:
        time.tzset()
    except AttributeError:
        pass  # Windows — TZ env-var has no effect via tzset

from .db import Base, engine, sync_engine
from . import db as db_module
from .db.maintenance import recover_uploads
import app.db.models  # noqa: F401 — register models with Base.metadata
from .auth import fastapi_users, auth_backend, UserRead, UserCreate
from .scheduler import daily_backup_loop, monthly_backup_loop, wechat_auto_sync_loop, watchdog_loop, weekly_report_loop
from .utils.leader import try_become_leader, release as release_leader
from .utils.rate_limiter import login_rate_limiter, get_client_ip

# ─── Routers ────────────────────────────────────────────────────────────────
# Ecommerce domain
from .views.ecommerce   import upload_router, analysis_router, orders_all_router, identity_router
# Media domain
from .views.media       import media_router, media_upload_router
from .views.media.xhs    import router as xhs_router              # POST /media/xhs/upload
from .views.media.zhihu  import router as zhihu_router            # POST /media/zhihu/upload
from .views.media.pgy    import router as pgy_router              # POST /media/pgy/upload
from .views.media.channels import router as channels_router       # POST /media/channels/upload
# Platform
from .views.admin         import router as admin_router          # POST /admin/clear-db
from .views.collector_admin import router as collector_admin_router  # /admin/collector/*
from .views.register      import router as register_router       # POST /auth/register
from .views.wecom_auth    import router as wecom_auth_router     # Enterprise WeChat OAuth
from .views.saved_queries import router as saved_queries_router  # GET/POST/DELETE /saved-queries/
from .views.data_freshness import router as data_freshness_router
from .views.reports import router as reports_router, admin_router as reports_admin_router  # /reports/weekly, /admin/reports/weekly/run

# ─── Lifespan ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    async def upload_recovery_loop():
        from .views.ecommerce.upload import _run_ingestion
        while True:
            async with db_module.AsyncSessionLocal() as session:
                jobs = await recover_uploads(session)
            for path, filename, user_id, digest, batch_id in jobs:
                await _run_ingestion(path, filename, user_id, digest, batch_id)
            await asyncio.sleep(60)

    async def supervise(name, factory):
        while True:
            try:
                app.state.background_status[name] = "running"
                await factory()
                raise RuntimeError(f"{name} loop returned unexpectedly")
            except asyncio.CancelledError:
                raise
            except Exception:
                app.state.background_status[name] = "restarting"
                logging.exception("Background loop %s failed; retrying", name)
                await asyncio.sleep(30)

    async def run_leader_tasks():
        while not try_become_leader():
            await asyncio.sleep(30)
        app.state.background_leader = True
        tasks = [asyncio.create_task(supervise("upload_recovery", upload_recovery_loop))]
        if not settings.rap_disable_monthly_backup:
            tasks.append(asyncio.create_task(supervise("daily_backup", lambda: daily_backup_loop(settings))))
            tasks.append(asyncio.create_task(supervise("backup", lambda: monthly_backup_loop(settings))))
        if settings.wechat_auto_sync_enabled:
            tasks.append(asyncio.create_task(supervise("wechat_sync", lambda: wechat_auto_sync_loop(settings))))
        if settings.watchdog_enabled:
            tasks.append(asyncio.create_task(supervise("watchdog", lambda: watchdog_loop(settings))))
        if settings.weekly_report_enabled:
            tasks.append(asyncio.create_task(supervise("weekly_report", lambda: weekly_report_loop(settings))))
        try:
            if tasks:
                await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    app.state.background_status = {}
    app.state.background_leader = False
    leader_task = asyncio.create_task(run_leader_tasks())
    try:
        yield
    finally:
        leader_task.cancel()
        await asyncio.gather(leader_task, return_exceptions=True)
        release_leader()
        await engine.dispose()
        await asyncio.to_thread(sync_engine.dispose)

# ─── FastAPI instance ───────────────────────────────────────────────────────
app = FastAPI(title="OmniPanel API", lifespan=lifespan)


@app.exception_handler(HTTPException)
async def safe_http_errors(request: Request, exc: HTTPException):
    if exc.status_code != 500:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)
    error_id = uuid.uuid4().hex[:12]
    logging.error("request_failed id=%s path=%s status=%s detail=%s", error_id, request.url.path, exc.status_code, exc.detail)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": "服务器处理失败，请稍后重试。", "request_id": error_id},
        headers=exc.headers,
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Middleware: rate-limit password login failures ─────────────────────────
@app.middleware("http")
async def _password_login_rate_limit(request: Request, call_next):
    """Count failed /auth/jwt/login attempts per email; block after MAX_ATTEMPTS.

    Keyed by email rather than IP: all Streamlit→FastAPI calls share the same
    loopback IP (127.0.0.1), so IP-keying would let one user's lockout block
    the entire organisation. Starlette caches request.form() on first call, so
    downstream OAuth2PasswordRequestForm dependency can still read the body.

    HTTPException cannot be raised from BaseHTTPMiddleware (it bypasses
    FastAPI's exception handler and crashes Starlette's TaskGroup). Instead,
    we return a JSONResponse directly when the rate limit is exceeded.
    """
    is_login = request.method == "POST" and request.url.path == "/auth/jwt/login"
    if not is_login:
        return await call_next(request)

    # Read the submitted email; fall back to IP for malformed requests.
    # Must use request.body() (not request.form()): Starlette's _CachedRequest
    # replays self._body to the downstream app only when body() was called;
    # form() consumes stream() instead, and the downstream then receives an
    # empty body (returning 422 from OAuth2PasswordRequestForm).
    try:
        from urllib.parse import parse_qs
        body_bytes = await request.body()
        form_data = parse_qs(body_bytes.decode("utf-8", errors="replace"))
        identifier = (form_data.get("username", [""])[0]).strip().lower()
    except Exception:
        identifier = ""
    if not identifier:
        identifier = get_client_ip(request)

    try:
        await login_rate_limiter.check(identifier, "password_login")
    except HTTPException as exc:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=dict(exc.headers or {}),
        )

    response = await call_next(request)

    if response.status_code in (400, 401):
        await login_rate_limiter.record_failure(identifier, "password_login")
    elif response.status_code == 200:
        await login_rate_limiter.reset(identifier, "password_login")

    return response


# Added last so Starlette wraps it outermost (most-recently-added middleware
# wraps everything else) — request.client.host must already be the real,
# trust-checked client IP before CORS or the rate limiter above ever read it.
# This must not depend on uvicorn's own CLI/__main__ startup path: baking it
# into the app object means it applies the same way whether the process is
# started via `uvicorn app.main:app`, `python -m app.main`, or TestClient.
if settings.proxy_headers:
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.forwarded_allow_ips)


# ─── FastAPI-Users auth routes ──────────────────────────────────────────────
app.include_router(
    fastapi_users.get_auth_router(auth_backend),
    prefix="/auth/jwt",
    tags=["auth"],
)
app.include_router(register_router)
app.include_router(wecom_auth_router)

# ─── Business routes ────────────────────────────────────────────────────────
app.include_router(upload_router)        # /upload/
app.include_router(analysis_router)      # /analysis/
app.include_router(orders_all_router)    # /orders_all/
app.include_router(data_freshness_router) # /data/freshness
app.include_router(identity_router)      # /analysis/identity/clusters
app.include_router(admin_router)         # /admin/clear-db
app.include_router(collector_admin_router)  # /admin/collector/*
app.include_router(media_router)         # /media/
app.include_router(media_upload_router)   # /media/accounts (POST)
app.include_router(xhs_router)            # /media/xhs/upload
app.include_router(zhihu_router)          # /media/zhihu/upload
app.include_router(pgy_router)            # /media/pgy/*
app.include_router(channels_router)       # /media/channels/*
app.include_router(saved_queries_router)  # /saved-queries/
app.include_router(reports_router)        # /reports/weekly
app.include_router(reports_admin_router)  # /admin/reports/weekly/run

# ─── Health check ───────────────────────────────────────────────────────────

async def _check_db() -> None:
    """Ping the database. Raises on failure."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis() -> str:
    """Ping Redis. Returns 'ok' or 'unavailable' (never raises)."""
    from .utils.cache import _RedisCache, analysis_cache

    if not isinstance(analysis_cache, _RedisCache):
        return "unavailable"
    try:
        import redis.asyncio as _aioredis  # optional dep — lazy to avoid hard dependency
        r = _aioredis.from_url(settings.redis_url, socket_connect_timeout=1)
        await r.ping()
        await r.aclose()
        return "ok"
    except Exception:
        return "unavailable"


@app.get("/health", tags=["ops"])
async def health():
    """Component health check used by nginx and monitoring scripts.

    Returns HTTP 200 when all critical components are reachable,
    HTTP 503 when any critical component is degraded.
    """
    db_status: str
    try:
        await _check_db()
        db_status = "ok"
    except Exception as exc:
        db_status = f"error: {exc}"

    redis_status = await _check_redis()

    ok = db_status == "ok"
    background = getattr(app.state, "background_status", {})
    body = {
        "status": "ok" if ok else "degraded",
        "database": db_status,
        "redis": redis_status,
        "background": background if getattr(app.state, "background_leader", False) else "other_worker",
    }
    return JSONResponse(content=body, status_code=200 if ok else 503)


# ─── Quick HTTPS check ──────────────────────────────────────────────────────
@app.get("/ping")
async def ping(request: Request):
    return {
        "pong": True,
        "scheme": request.url.scheme,
        "host": request.client.host if request.client else None,
    }

# ─── Development / HTTPS runner ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn

    # proxy_headers/forwarded_allow_ips are handled by the ProxyHeadersMiddleware
    # added to `app` above, not passed here — that way trust is enforced the
    # same way regardless of whether uvicorn is started via this __main__ block
    # or via `uvicorn app.main:app` directly (the documented production path).
    if settings.ssl_keyfile and settings.ssl_certfile:
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
            ssl_keyfile=settings.ssl_keyfile,
            ssl_certfile=settings.ssl_certfile,
        )
    else:
        logging.warning("SSL_KEYFILE/SSL_CERTFILE not set; starting HTTP server.")
        uvicorn.run(
            app,
            host=settings.host,
            port=settings.port,
        )
