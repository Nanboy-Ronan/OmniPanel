"""Small database recovery tasks run when the background leader starts."""

from sqlalchemy import func, text, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UploadBatch

# A batch still "processing"/"recovering" after this many recovery claims is
# treated as a poison file (it keeps killing or hanging the worker) and failed.
MAX_RECOVERY_ATTEMPTS = 3
POISON_ERROR_MESSAGE = (
    f"自动恢复已尝试 {MAX_RECOVERY_ATTEMPTS} 次仍未完成（该文件可能导致处理进程崩溃或卡死），"
    "已停止自动重试；请检查文件内容后重新上传"
)


async def recover_uploads(session: AsyncSession) -> list[tuple]:
    """Claim interrupted uploads for a single leader worker to reprocess.

    Each claim increments ``recovery_attempts``; once a batch has been
    claimed MAX_RECOVERY_ATTEMPTS times without finishing it is marked
    failed instead of being re-claimed forever.
    """
    from ..views.ecommerce.upload import upload_spool_path

    condition = (UploadBatch.status.in_(("processing", "recovering"))) & (
        UploadBatch.uploaded_at < func.now() - text("INTERVAL '2 hours'")
    )
    rows = (await session.execute(
        select(UploadBatch).where(condition).with_for_update(skip_locked=True)
    )).scalars().all()
    claimed = []
    for batch in rows:
        path = upload_spool_path(batch.id, batch.filename)
        if (batch.recovery_attempts or 0) >= MAX_RECOVERY_ATTEMPTS:
            batch.status = "failed"
            batch.error_message = POISON_ERROR_MESSAGE
        elif path.exists():
            batch.recovery_attempts = (batch.recovery_attempts or 0) + 1
            batch.status = "recovering"
            claimed.append((str(path), batch.filename, str(batch.uploaded_by), batch.file_sha256, batch.id))
        else:
            batch.status = "failed"
            batch.error_message = "Upload worker stopped and the source file is unavailable; please upload again"
    await session.commit()
    return claimed
