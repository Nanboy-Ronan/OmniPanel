# rap/app/views/ecommerce/analysis/sql_console.py
"""Ad-hoc SQL console and Chinese natural-language (NL2SQL) query endpoints."""
from __future__ import annotations
from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ....auth import current_analyst_user
from ....db import get_session
from ....utils.logger import log_operation
from ....utils.sql_validator import validate_sql_query, enforce_limit
from ._common import router


class SqlQueryRequest(BaseModel):
    """Request body for POST /analysis/sql."""
    sql: str


class NLSqlRequest(BaseModel):
    """Request body for POST /analysis/nl-sql.

    ``provider`` / ``model`` are the user's dropdown selection; both optional and
    fall back to the server defaults. Only the provider id + model name travel —
    API keys stay server-side.
    """
    question: str
    provider: str | None = None
    model: str | None = None


@router.post("/sql", summary="Ad-hoc SQL query console (analyst+)")
async def run_sql_query(
    body: SqlQueryRequest,
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        validate_sql_query(body.sql)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        safe_sql = enforce_limit(body.sql)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    try:
        await session.execute(text("SET LOCAL transaction_read_only = on"))
        await session.execute(text("SET LOCAL statement_timeout = '10000'"))
        result = await session.execute(text(safe_sql))
        rows = result.fetchall()
        columns: list[str] = list(result.keys())
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Query execution error: {exc}",
        )

    row_data = [list(row) for row in rows]
    row_count = len(row_data)

    # session is read-only (SET LOCAL transaction_read_only = on); open a
    # separate connection for the log write rather than passing session=.
    await log_operation(
        str(_u.id),
        "sql_query",
        {"sql": body.sql, "row_count": row_count},
    )

    return {"rows": row_data, "columns": columns, "row_count": row_count}


@router.get("/nl-sql/providers", summary="中文问数据可用服务商与模型 (analyst+)")
async def nl_sql_providers(_u=Depends(current_analyst_user)):
    """Return the AI providers that have an API key configured server-side, plus
    each one's selectable models, so the UI can populate provider/model dropdowns.
    """
    from ....utils.nl_to_sql import available_providers, default_provider_id

    return {
        "providers": available_providers(),
        "default_provider": default_provider_id(),
    }


@router.post("/nl-sql", summary="中文问数据：自然语言 → SQL → 执行 (analyst+)")
async def run_nl_sql(
    body: NLSqlRequest,
    _u=Depends(current_analyst_user),
    session: AsyncSession = Depends(get_session),
):
    """Translate a Chinese question into SQL, then run it through the exact same
    read-only safety pipeline as the manual SQL console.

    Always returns the generated SQL (even on failure) so the UI can show what
    was attempted — transparency is what makes the feature trustworthy. ``error``
    is non-null when generation, validation, or execution failed.
    """
    from ....utils.nl_to_sql import (
        generate_sql,
        NLToSQLNotConfigured,
        NLToSQLError,
    )

    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="请输入问题。")

    try:
        sql, explanation = await generate_sql(question, body.provider, body.model)
    except NLToSQLNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except NLToSQLError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    result: dict[str, Any] = {
        "question": question,
        "sql": sql,
        "explanation": explanation,
        "rows": [],
        "columns": [],
        "row_count": 0,
        "error": None,
    }

    if not sql:
        result["error"] = explanation or "无法将该问题转换为查询。"
        return result

    # Reuse the SQL console guards — never trust model-generated SQL.
    try:
        validate_sql_query(sql)
        safe_sql = enforce_limit(sql)
    except ValueError as exc:
        result["error"] = f"生成的 SQL 未通过安全校验：{exc}"
        return result
    result["sql"] = safe_sql

    try:
        await session.execute(text("SET LOCAL transaction_read_only = on"))
        await session.execute(text("SET LOCAL statement_timeout = '10000'"))
        exec_result = await session.execute(text(safe_sql))
        rows = exec_result.fetchall()
        columns = list(exec_result.keys())
    except Exception as exc:  # noqa: BLE001 - report execution errors to the UI
        result["error"] = f"查询执行错误：{exc}"
        await log_operation(
            str(_u.id),
            "nl_sql_query",
            {
                "question": question,
                "sql": safe_sql,
                "provider": body.provider,
                "model": body.model,
                "error": str(exc)[:500],
            },
        )
        return result

    row_data = [list(row) for row in rows]
    result["rows"] = row_data
    result["columns"] = columns
    result["row_count"] = len(row_data)

    await log_operation(
        str(_u.id),
        "nl_sql_query",
        {
            "question": question,
            "sql": safe_sql,
            "provider": body.provider,
            "model": body.model,
            "row_count": len(row_data),
        },
    )
    return result
