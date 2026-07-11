# rap/app/views/ecommerce/analysis/__init__.py
"""Ecommerce analysis endpoints, split by theme onto one shared router.

Importing this package registers every analysis route (the submodules attach
themselves to ``_common.router`` at import time) and re-exports the handful of
names other modules and tests depend on, so ``app.views.ecommerce.analysis``
keeps the same public surface it had as a single module.
"""
from __future__ import annotations

from ....utils.sql_validator import validate_sql_query, enforce_limit
from ._common import (
    router,
    AnalysisDataNotReady,
    _has_orders,
    _ensure_data,
    _platform_filter,
    _window,
)

# Import submodules for their side effect of attaching routes to ``router``.
# Order here fixes the route-registration order in the generated OpenAPI schema.
from . import segmentation, retention, customers, sql_console  # noqa: E402,F401
from .retention import build_cohort_matrix, _month_index  # noqa: E402
from .sql_console import SqlQueryRequest, NLSqlRequest  # noqa: E402

__all__ = [
    "router",
    "AnalysisDataNotReady",
    "build_cohort_matrix",
    "validate_sql_query",
    "enforce_limit",
    "SqlQueryRequest",
    "NLSqlRequest",
]
