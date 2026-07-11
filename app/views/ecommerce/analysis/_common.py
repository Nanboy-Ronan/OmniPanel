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

from ....db.models import Order

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


def _platform_filter(platform: str | None):
    if platform:
        return Order.platform == platform
    return None


def _window(stmt, start_date: dt.date, end_date: dt.date, pf):
    """Apply the standard order-date window and optional platform filter.

    Centralises the ``where(order_date.between(...))`` + optional platform
    clause that nearly every analysis query repeats.
    """
    stmt = stmt.where(Order.order_date.between(start_date, end_date))
    if pf is not None:
        stmt = stmt.where(pf)
    return stmt
