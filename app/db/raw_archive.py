"""Keep imported export files as received.

Every successful import moves its file into the raw archive and records a
`raw_upload_files` row; post snapshots point at that row, so any number in
the database can be traced back to the file it came from. A failed import
leaves nothing behind. Retention deletes old files but keeps the rows.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from ..config import settings
from .models import RawUploadFile

logger = logging.getLogger(__name__)


def archive_dir() -> Path:
    if settings.raw_archive_dir:
        return Path(settings.raw_archive_dir)
    return Path(settings.backup_dir) / "upload_archive"


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _target(source: str, filename: str, sha256: str) -> Path:
    now = datetime.now(timezone.utc)
    suffix = Path(filename).suffix.lower()[:10]
    directory = archive_dir() / source / now.strftime("%Y-%m")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory / f"{now.strftime('%Y%m%dT%H%M%S%f')}-{sha256[:12]}{suffix}"


@contextmanager
def archived_upload(session, path: str, *, source: str, filename: str, account_id=None,
                    uploaded_by=None, upload_batch_id=None):
    """Move *path* into the archive and add its row to *session* (flushed, not committed).

    Yields the row so the import can reference its id. If the block raises,
    the archived copy is deleted again; the caller's rollback drops the row.
    An unwritable archive never blocks the import: the row is still recorded,
    with archive_path NULL, and the error is logged.
    """
    sha256 = _sha256(path)
    size = os.path.getsize(path)
    target = None
    try:
        candidate = _target(source, filename, sha256)
        # The temp file may sit on another filesystem than the archive.
        shutil.move(path, candidate)
        target = candidate
    except OSError as exc:
        logger.error("raw_archive_unavailable dir=%s: %s", archive_dir(), exc)
    try:
        record = RawUploadFile(source=source, account_id=account_id, upload_batch_id=upload_batch_id,
                               filename=filename[:500], sha256=sha256, size_bytes=size,
                               archive_path=str(target) if target else None, uploaded_by=uploaded_by)
        session.add(record)
        session.flush()
        yield record
    except BaseException:
        if target:
            target.unlink(missing_ok=True)
        raise


def write_temp(data: bytes, suffix: str) -> str:
    """Spill an in-memory upload to a temp file so it can be archived like the others."""
    import tempfile
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(data)
        return tmp.name


def purge_expired(session, now=None) -> int:
    """Delete archived files past retention; their rows stay with archive_path cleared."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.raw_archive_retention_days)
    expired = session.execute(select(RawUploadFile).where(
        RawUploadFile.archive_path.is_not(None), RawUploadFile.created_at < cutoff)).scalars().all()
    for record in expired:
        try:
            Path(record.archive_path).unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove archived upload %s", record.id)
            continue
        record.archive_path = None
        record.purged_at = now
    session.commit()
    return len(expired)
