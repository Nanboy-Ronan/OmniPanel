"""app/views/media/upload.py

POST /media/accounts — admin creates a media account (no app credentials required)

# DISABLED: xlsx upload routes commented out 2026-06-01 — data comes from
# manual API sync (POST /media/wechat/sync), xlsx upload was never used in
# production.  See README §"Disabled: WeChat xlsx upload" to re-enable.
#
# POST /media/upload   — upload a WeChat xlsx export for a named account
# GET  /media/uploads  — list recent manual upload runs (source='manual')
"""
from __future__ import annotations

# import os          # only needed by disabled xlsx upload
# import tempfile    # only needed by disabled xlsx upload
# from datetime import date, datetime  # only needed by disabled xlsx upload
# from typing import Any               # only needed by disabled xlsx upload

from fastapi import APIRouter, Depends, HTTPException, status
# from fastapi import Form, Query, UploadFile, File  # disabled xlsx upload
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import current_admin_user  # , current_analyst_user  # disabled
from ...db import get_session
# from ...db.media_etl import parse_wechat_xlsx  # disabled xlsx upload
from ...db.models import MediaAccount  # , MediaArticleTraffic, MediaSyncRun  # disabled

upload_router = APIRouter(prefix="/media", tags=["media"])

from ...config import settings as _settings
_MAX_UPLOAD_MB = _settings.max_upload_mb  # kept so test_settings_p1 still passes
# _MAX_UPLOAD_BYTES = _MAX_UPLOAD_MB * 1024 * 1024  # disabled xlsx upload
# _CHUNK = 1024 * 1024                              # disabled xlsx upload
# _ALLOWED_EXTENSIONS = {".xlsx", ".xls"}           # disabled xlsx upload
# _BLOCKED_MIME_PREFIXES = (                        # disabled xlsx upload
#     "application/x-executable",
#     "application/x-msdownload",
#     "application/x-sh",
#     "application/x-shellscript",
#     "application/x-elf",
# )
WECHAT_PLATFORM = "wechat_official"


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class CreateAccountRequest(BaseModel):
    name: str
    app_id: str | None = None


# ── Disabled helpers (xlsx upload) ─────────────────────────────────────────────

# def _validate_upload(filename: str, content_type: str | None) -> None:
#     ext = os.path.splitext(filename)[1].lower()
#     if ext not in _ALLOWED_EXTENSIONS:
#         raise HTTPException(
#             status_code=400,
#             detail=(
#                 f"不支持的文件类型 '{ext or '(无扩展名)'}'。"
#                 "公众号上传只接受 .xlsx 或 .xls 文件。"
#             ),
#         )
#     if content_type:
#         mime = content_type.split(";")[0].strip().lower()
#         if any(mime.startswith(b) for b in _BLOCKED_MIME_PREFIXES):
#             raise HTTPException(
#                 status_code=415,
#                 detail=f"不支持的 Content-Type '{mime}'。",
#             )


# async def _upsert_traffic(
#     session: AsyncSession,
#     account: MediaAccount,
#     row: dict[str, Any],
# ) -> bool:
#     result = await session.execute(
#         select(MediaArticleTraffic).where(
#             MediaArticleTraffic.account_id == account.id,
#             MediaArticleTraffic.external_id == row["external_id"],
#         )
#     )
#     record = result.scalar_one_or_none()
#     values = {
#         "title":            row["title"],
#         "publish_date":     row["publish_date"],
#         "read_user_count":  row["read_user_count"],
#         "read_count":       row["read_count"],
#         "like_user":        row["like_user"],
#         "share_user_count": row["share_user_count"],
#         "comment_count":    row["comment_count"],
#         "collection_user":  row["collection_user"],
#         "read_avg_time":    row["read_avg_time"],
#         "raw_payload":      row["raw_payload"],
#         "updated_at":       datetime.now(),
#     }
#     if record is None:
#         session.add(MediaArticleTraffic(account_id=account.id, external_id=row["external_id"], **values))
#         return True
#     else:
#         for k, v in values.items():
#             setattr(record, k, v)
#         return False


# ── Routes ─────────────────────────────────────────────────────────────────────

@upload_router.post("/accounts", status_code=201)
async def create_media_account(
    payload: CreateAccountRequest,
    _u=Depends(current_admin_user),
    session: AsyncSession = Depends(get_session),
):
    """Admin creates a media account (no WeChat API credentials required)."""
    account = MediaAccount(
        platform=WECHAT_PLATFORM,
        name=payload.name,
        app_id=payload.app_id,
        is_active=True,
    )
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return {
        "id":        account.id,
        "platform":  account.platform,
        "name":      account.name,
        "app_id":    account.app_id,
        "is_active": bool(account.is_active),
    }


# ── Disabled routes (xlsx upload) ──────────────────────────────────────────────

# @upload_router.post("/upload")
# async def upload_wechat_xlsx(
#     file: UploadFile = File(...),
#     account_id: int = Form(...),
#     _u=Depends(current_analyst_user),
#     session: AsyncSession = Depends(get_session),
# ):
#     """Upload a WeChat backend xlsx export and upsert posts + metrics."""
#     _validate_upload(file.filename or "", file.content_type)
#     result = await session.execute(select(MediaAccount).where(MediaAccount.id == account_id))
#     account = result.scalar_one_or_none()
#     if account is None:
#         raise HTTPException(status_code=404, detail="Media account not found")
#     tmp_path = None
#     try:
#         suffix = os.path.splitext(file.filename or "upload")[1] or ".xlsx"
#         with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
#             total = 0
#             while True:
#                 chunk = await file.read(_CHUNK)
#                 if not chunk:
#                     break
#                 total += len(chunk)
#                 if total > _MAX_UPLOAD_BYTES:
#                     tmp_path = tmp.name
#                     os.unlink(tmp_path)
#                     raise HTTPException(status_code=413, detail=f"文件过大，最大允许 {_MAX_UPLOAD_MB} MB。")
#                 tmp.write(chunk)
#             tmp_path = tmp.name
#     except HTTPException:
#         raise
#     except Exception as exc:
#         if tmp_path and os.path.exists(tmp_path):
#             os.unlink(tmp_path)
#         raise HTTPException(status_code=500, detail=f"无法保存上传文件：{exc}")
#     try:
#         rows, rejected = parse_wechat_xlsx(tmp_path, account_id)
#     except Exception as exc:
#         raise HTTPException(status_code=400, detail=f"文件解析失败：{exc}")
#     finally:
#         if tmp_path and os.path.exists(tmp_path):
#             os.unlink(tmp_path)
#     inserted = 0
#     updated = 0
#     try:
#         for row in rows:
#             is_new = await _upsert_traffic(session, account, row)
#             if is_new:
#                 inserted += 1
#             else:
#                 updated += 1
#         today = date.today()
#         run = MediaSyncRun(
#             account_id=account.id, status="success", start_date=today, end_date=today,
#             posts_upserted=inserted + updated, metrics_upserted=0, rejected=len(rejected),
#             finished_at=datetime.now(), source="manual", filename=file.filename,
#         )
#         session.add(run)
#         await session.commit()
#     except Exception as exc:
#         await session.rollback()
#         raise HTTPException(status_code=500, detail=f"数据写入失败：{exc}")
#     return {
#         "posts_upserted": inserted + updated, "inserted": inserted, "updated": updated,
#         "metrics_upserted": 0, "rejected": len(rejected),
#         "rejected_reasons": rejected[:20], "filename": file.filename,
#         "account_name": account.name,
#     }


# @upload_router.get("/uploads")
# async def list_media_uploads(
#     limit: int = Query(20, ge=1, le=100),
#     _u=Depends(current_analyst_user),
#     session: AsyncSession = Depends(get_session),
# ):
#     """Return recent manual xlsx upload runs (source='manual'), newest first."""
#     stmt = (
#         select(MediaSyncRun, MediaAccount.name.label("account_name"))
#         .join(MediaAccount, MediaSyncRun.account_id == MediaAccount.id)
#         .where(MediaSyncRun.source == "manual")
#         .order_by(MediaSyncRun.started_at.desc())
#         .limit(limit)
#     )
#     result = await session.execute(stmt)
#     return [
#         {
#             "id": row.MediaSyncRun.id, "account_name": row.account_name,
#             "filename": row.MediaSyncRun.filename, "posts_upserted": row.MediaSyncRun.posts_upserted,
#             "metrics_upserted": row.MediaSyncRun.metrics_upserted, "rejected": row.MediaSyncRun.rejected,
#             "source": row.MediaSyncRun.source, "status": row.MediaSyncRun.status,
#             "started_at": str(row.MediaSyncRun.started_at) if row.MediaSyncRun.started_at else None,
#         }
#         for row in result.all()
#     ]
