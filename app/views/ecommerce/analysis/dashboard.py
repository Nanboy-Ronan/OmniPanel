"""Complete-window aggregates for the interactive commerce dashboard."""
import datetime as dt
from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from ....auth import current_analyst_user
from ....db import get_session
from ....services.ecommerce_metrics import commerce_metrics
from ._common import router

@router.get("/dashboard", summary="Daily trends and complete-window BI aggregates")
async def dashboard(
    start_date: dt.date = Query(...), end_date: dt.date = Query(...),
    platform: str | None = Query(None), _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    return await commerce_metrics(session, start_date, end_date, platform)
