"""Small database recovery tasks run when the background leader starts."""

from sqlalchemy import func, text, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import UploadBatch


async def recover_uploads(session: AsyncSession) -> list[tuple]:
    """Claim interrupted uploads for a single leader worker to reprocess."""
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
        if path.exists():
            batch.status = "recovering"
            claimed.append((str(path), batch.filename, str(batch.uploaded_by), batch.file_sha256, batch.id))
        else:
            batch.status = "failed"
            batch.error_message = "Upload worker stopped and the source file is unavailable; please upload again"
    await session.commit()
    return claimed
