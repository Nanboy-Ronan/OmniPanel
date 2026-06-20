# rap/app/views/ecommerce/upload.py

import asyncio
import hashlib
import logging
import os
import tempfile

import pandas as pd
from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ... import db as _db_mod
from ...db import get_session
from ...db.etl import ingest_upload
from ...db.models import Order, UploadBatch, UploadRejectedRow
from ...auth import current_active_user
from ...utils.cache import analysis_cache
from ...utils.logger import log_exc, log_operation

router = APIRouter(prefix="/upload", tags=["upload"])

_logger = logging.getLogger(__name__)

from ...config import settings as _settings
_MAX_UPLOAD_MB = _settings.max_upload_mb
_MAX_UPLOAD_BYTES = _MAX_UPLOAD_MB * 1024 * 1024
_CHUNK = 1024 * 1024  # read 1 MB at a time

_ALLOWED_EXTENSIONS = {".csv", ".xls", ".xlsx"}

# Permissive MIME whitelist: blocks obvious binary executables while accepting
# the wide variety of content-types that browsers send for CSV/Excel files.
_BLOCKED_MIME_PREFIXES = (
    "application/x-executable",
    "application/x-msdownload",
    "application/x-sh",
    "application/x-shellscript",
    "application/x-elf",
)


def _validate_upload(filename: str, content_type: str | None) -> None:
    """Raise HTTPException for disallowed file types before any I/O."""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported file type '{ext or '(none)'}'. "
                f"Only CSV and Excel files are accepted (.csv, .xls, .xlsx)."
            ),
        )
    if content_type:
        # Strip parameters (e.g. "text/csv; charset=utf-8" → "text/csv")
        mime = content_type.split(";")[0].strip().lower()
        if any(mime.startswith(blocked) for blocked in _BLOCKED_MIME_PREFIXES):
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported content type '{mime}'. Upload a CSV or Excel file.",
            )


async def _run_ingestion(
    tmp_path: str,
    filename: str,
    user_id: str,
    file_sha256: str,
    batch_id: int,
) -> None:
    """Background task: parse the temp file, run ETL, update the batch record.

    Uses its own DB session — the request session is closed by the time this
    runs. On failure, opens a second session to mark the batch as 'failed'.
    """
    try:
        def _read(path: str) -> pd.DataFrame:
            if path.lower().endswith((".xls", ".xlsx")):
                return pd.read_excel(path, dtype=str)
            return pd.read_csv(path, dtype=str)

        df = await asyncio.to_thread(_read, tmp_path)

        def _ingest(df_: pd.DataFrame) -> dict:
            with _db_mod.SyncSessionLocal() as sync_sess:
                return ingest_upload(
                    df_,
                    sync_sess,
                    filename=filename,
                    uploaded_by=user_id,
                    file_sha256=file_sha256,
                    batch_id=batch_id,
                )

        result = await asyncio.to_thread(_ingest, df)

        await analysis_cache.invalidate()
        await log_operation(
            user_id,
            "upload",
            {
                "filename": filename,
                "rows": result["inserted_rows"],
                "platform": result["platform"],
                "batch_id": result["batch_id"],
                "total_rows": result["total_rows"],
                "duplicate_rows": result["duplicate_rows"],
                "invalid_rows": result["invalid_rows"],
            },
        )

    except Exception as exc:
        log_exc(
            _logger,
            "upload_ingestion_failed",
            exc,
            batch_id=batch_id,
            filename=filename,
        )
        try:
            async with _db_mod.AsyncSessionLocal() as err_session:
                batch = await err_session.get(UploadBatch, batch_id)
                if batch is not None:
                    batch.status = "failed"
                    batch.error_message = str(exc)[:500]
                    await err_session.commit()
        except Exception as inner:
            log_exc(_logger, "failed_to_update_batch_status", inner, batch_id=batch_id)

    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.post("/", status_code=202)
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    _user=Depends(current_active_user),
):
    """
    1) Validate extension and content-type synchronously.
    2) Stream the file to a temp path, enforcing the size limit.
    3) Create an UploadBatch record with status='processing'.
    4) Return 202 with the batch_id immediately.
    5) ETL (detect platform → normalize → insert) runs as a background task.

    Clients poll GET /upload/batches/{batch_id} to learn the final status.
    """
    _validate_upload(file.filename or "", file.content_type)

    tmp_path = None
    try:
        suffix = os.path.splitext(file.filename)[1]
        hasher = hashlib.sha256()
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            total = 0
            while True:
                chunk = await file.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_UPLOAD_BYTES:
                    tmp_path = tmp.name
                    os.unlink(tmp_path)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Maximum allowed size is {_MAX_UPLOAD_MB} MB.",
                    )
                tmp.write(chunk)
                hasher.update(chunk)
            tmp_path = tmp.name
        file_sha256 = hasher.hexdigest()
    except HTTPException:
        raise
    except Exception as e:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise HTTPException(status_code=500, detail=f"Could not save upload: {e}")

    # Create the batch record immediately so the client has an ID to poll.
    batch = UploadBatch(
        filename=file.filename,
        platform="unknown",
        uploaded_by=str(_user.id),
        file_sha256=file_sha256,
        row_count=0,
        status="processing",
    )
    session.add(batch)
    await session.commit()
    await session.refresh(batch)
    batch_id = batch.id

    background_tasks.add_task(
        _run_ingestion,
        tmp_path=tmp_path,
        filename=file.filename,
        user_id=str(_user.id),
        file_sha256=file_sha256,
        batch_id=batch_id,
    )

    return {"batch_id": batch_id, "status": "processing"}


@router.get("/batches/{batch_id}")
async def get_upload_batch(
    batch_id: int,
    _user=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Return status and result for a specific upload batch."""
    batch = await session.get(UploadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")
    return {
        "id": batch.id,
        "filename": batch.filename,
        "platform": batch.platform,
        "uploaded_at": str(batch.uploaded_at) if batch.uploaded_at else None,
        "row_count": batch.row_count,
        "inserted_orders": batch.inserted_orders,
        "raw_rows_inserted": batch.raw_rows_inserted,
        "duplicate_rows": batch.duplicate_rows,
        "invalid_rows": batch.invalid_rows,
        "status": batch.status,
        "error_message": batch.error_message,
    }


@router.get("/batches/{batch_id}/rejected")
async def get_rejected_rows(
    batch_id: int,
    _user=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Return all rows rejected during ingestion for a given batch."""
    batch = await session.get(UploadBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")

    result = await session.execute(
        select(UploadRejectedRow)
        .where(UploadRejectedRow.batch_id == batch_id)
        .order_by(UploadRejectedRow.source_row_number)
    )
    rows = result.scalars().all()
    return {
        "batch_id": batch_id,
        "count": len(rows),
        "rows": [
            {
                "id": r.id,
                "source_row_number": r.source_row_number,
                "reason": r.reason,
                "raw_payload": r.raw_payload,
            }
            for r in rows
        ],
    }


@router.get("/batches")
async def list_upload_batches(
    limit: int = Query(10, ge=1, le=50),
    _user=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Return the most recent upload batches (all authenticated users)."""
    stmt = (
        select(UploadBatch)
        .order_by(UploadBatch.uploaded_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    batches = result.scalars().all()
    return [
        {
            "id": b.id,
            "filename": b.filename,
            "platform": b.platform,
            "uploaded_at": str(b.uploaded_at) if b.uploaded_at else None,
            "row_count": b.row_count,
            "inserted_orders": b.inserted_orders,
            "duplicate_rows": b.duplicate_rows,
            "invalid_rows": b.invalid_rows,
            "status": b.status,
            "error_message": b.error_message,
        }
        for b in batches
    ]


@router.get("/summary")
async def upload_summary(
    _user=Depends(current_active_user),
    session: AsyncSession = Depends(get_session),
):
    """Per-platform order count and last upload time (all authenticated users)."""
    platforms = ["youzan", "jd", "tmall"]
    result: dict = {}
    overall_last = None

    for pf in platforms:
        count = (
            await session.execute(
                select(func.count(Order.id)).where(Order.platform == pf)
            )
        ).scalar() or 0

        last_batch = (
            await session.execute(
                select(func.max(UploadBatch.uploaded_at)).where(UploadBatch.platform == pf)
            )
        ).scalar()

        result[pf] = {
            "orders": count,
            "last_upload": str(last_batch) if last_batch else None,
        }
        if last_batch and (overall_last is None or last_batch > overall_last):
            overall_last = last_batch

    return {
        "platforms": result,
        "last_upload_at": str(overall_last) if overall_last else None,
    }
