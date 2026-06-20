from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_analyst_user
from ..db import get_session
from ..db.models import SavedQuery

router = APIRouter(prefix="/saved-queries", tags=["saved-queries"])


class SavedQueryCreate(BaseModel):
    name: str
    filters_json: dict = {}
    is_shared: bool = False


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_saved_query(
    body: SavedQueryCreate,
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    q = SavedQuery(
        user_id=_u.id,
        name=body.name,
        filters_json=body.filters_json,
        is_shared=body.is_shared,
    )
    session.add(q)
    await session.commit()
    await session.refresh(q)
    return _serialize(q)


@router.get("/")
async def list_saved_queries(
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(SavedQuery)
        .where(or_(SavedQuery.user_id == _u.id, SavedQuery.is_shared == True))  # noqa: E712
        .order_by(SavedQuery.created_at.desc())
    )
    return [_serialize(q) for q in result.scalars().all()]


@router.delete("/{query_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_saved_query(
    query_id: str,
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(select(SavedQuery).where(SavedQuery.id == query_id))
    q = result.scalar_one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail="Saved query not found")
    if str(q.user_id) != str(_u.id) and _u.role != "admin":
        raise HTTPException(status_code=403, detail="Not allowed to delete this saved query")
    await session.delete(q)
    await session.commit()


def _serialize(q: SavedQuery) -> dict:
    return {
        "id": str(q.id),
        "user_id": str(q.user_id),
        "name": q.name,
        "filters_json": q.filters_json,
        "is_shared": q.is_shared,
        "created_at": str(q.created_at) if q.created_at else None,
    }
