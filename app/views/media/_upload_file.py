"""Shared bounded temporary-file handling for media imports."""

import os
import tempfile

from fastapi import HTTPException, UploadFile

from ...config import settings


async def save_upload(file: UploadFile, suffix: str) -> str:
    limit = settings.max_upload_mb * 1024 * 1024
    path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            path = tmp.name
            total = 0
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件过大（上限 {settings.max_upload_mb} MB）。",
                    )
                tmp.write(chunk)
        return path
    except Exception:
        if path and os.path.exists(path):
            os.unlink(path)
        raise
