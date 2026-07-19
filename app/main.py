# rap/app/main.py
import asyncio
import logging
import os
import time
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

from .db import Base, engine
import app.db.models  # noqa: F401 — register models with Base.metadata
from .auth import fastapi_users, auth_backend, UserRead, UserCreate
from .scheduler import monthly_backup_loop, wechat_auto_sync_loop
from .utils.leader import try_become_leader
from .utils.rate_limiter import login_rate_limiter, get_client_ip

# ─── Routers ────────────────────────────────────────────────────────────────
# Ecommerce domain
from .views.ecommerce   import upload_router, analysis_router, orders_all_router, identity_router
# Media domain
from .views.media       import media_router, media_upload_router
from .views.media.xhs    import router as xhs_router              # POST /media/xhs/upload
from .views.media.zhihu  import router as zhihu_router            # POST /media/zhihu/upload
# Platform
from .views.admin         import router as admin_router          # POST /admin/clear-db
from .views.collector_admin import router as collector_admin_router  # /admin/collector/*
from .views.register      import router as register_router       # POST /auth/register
from .views.wecom_auth    import router as wecom_auth_router     # Enterprise WeChat OAuth
from .views.saved_queries import router as saved_queries_router  # GET/POST/DELETE /saved-queries/

# ─── Lifespan ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    if try_become_leader():
        if not settings.rap_disable_monthly_backup:
            asyncio.create_task(monthly_backup_loop(settings))
        if settings.wechat_auto_sync_enabled:
            asyncio.create_task(wechat_auto_sync_loop(settings))
    yield

# ─── FastAPI instance ───────────────────────────────────────────────────────
app = FastAPI(title="RPA internal API", lifespan=lifespan)

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
app.include_router(identity_router)      # /analysis/identity/clusters
app.include_router(admin_router)         # /admin/clear-db
app.include_router(collector_admin_router)  # /admin/collector/*
app.include_router(media_router)         # /media/
app.include_router(media_upload_router)   # /media/upload, /media/uploads, /media/accounts (POST)
app.include_router(xhs_router)            # /media/xhs/upload
app.include_router(zhihu_router)          # /media/zhihu/upload
app.include_router(saved_queries_router)  # /saved-queries/

# ─── Health check ───────────────────────────────────────────────────────────

async def _check_db() -> None:
    """Ping the database. Raises on failure."""
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _check_redis() -> str:
    """Ping Redis. Returns 'ok' or 'unavailable' (never raises)."""
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
    body = {
        "status": "ok" if ok else "degraded",
        "database": db_status,
        "redis": redis_status,
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
