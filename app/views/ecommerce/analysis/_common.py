# rap/app/views/ecommerce/analysis/_common.py
"""Shared router and helpers for the ecommerce analysis endpoints.

The analysis endpoints are split across sibling modules by theme
(``segmentation``, ``retention``, ``customers``, ``sql_console``); they all
attach their routes to the single ``router`` defined here so the package still
exposes one ``APIRouter`` under ``/analysis``.
"""
from __future__ import annotations
import datetime as dt

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from ....services.ecommerce_metrics import _platform_filter, _window  # shared query rules
from ....db.models import Order
from ....db.order_status import counted

router = APIRouter(prefix="/analysis", tags=["analysis"])


class AnalysisDataNotReady(HTTPException):
    """Raised when order data has not been ingested."""

    def __init__(self, detail: str = "Analysis data not initialised. Upload order data first."):
        super().__init__(status_code=503, detail=detail)


async def _has_orders(session: AsyncSession) -> bool:
    result = await session.execute(select(func.count(Order.id)).limit(1))
    return result.scalar() > 0


async def _ensure_data(session: AsyncSession) -> None:
    if not await _has_orders(session):
        raise AnalysisDataNotReady(
            "Analysis data not initialised. Upload order data before using analysis endpoints."
        )
