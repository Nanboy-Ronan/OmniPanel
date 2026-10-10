# rap/app/views/ecommerce/analysis/sql_console.py
"""Ad-hoc SQL console and Chinese natural-language (NL2SQL) query endpoints."""
from __future__ import annotations
from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from ....auth import current_analyst_user
from ....config import settings
from ....db.sql_console import SqlConsoleNotConfigured, console_session
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


# query_to_xml() and friends can fold any number of rows into a single cell, so
# the row cap alone does not bound the response.
MAX_RESULT_CHARS = 5_000_000


async def _execute_console_sql(sql: str) -> tuple[list[list[Any]], list[str]]:
    """Run validated SQL as the restricted console role; raise on oversize."""
    async with console_session() as session:
        result = await session.execute(text(sql))
        columns = list(result.keys())
        rows: list[list[Any]] = []
        size = 0
        for row in result:
            values = list(row)
            size += sum(len(str(value)) for value in values if value is not None)
            if size > MAX_RESULT_CHARS:
                raise ValueError("result too large")
            rows.append(values)
    return rows, columns


@router.post("/sql", summary="Ad-hoc SQL query console (analyst+)")
async def run_sql_query(
    body: SqlQueryRequest,
    _u=Depends(current_analyst_user),
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
        row_data, columns = await _execute_console_sql(safe_sql)
    except SqlConsoleNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError:
        raise HTTPException(status_code=400, detail="查询结果过大，请减少返回的列或行。")
    except Exception:
        raise HTTPException(
            status_code=400,
            detail="查询失败。请检查字段、表名或权限。",
        )

    row_count = len(row_data)

    # The console connection is read-only; the log write uses the app connection.
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
    if not settings.sql_console_database_url:
        # Fail before paying for a model call whose SQL could not run anyway.
        raise HTTPException(status_code=503, detail=str(SqlConsoleNotConfigured()))

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
        row_data, columns = await _execute_console_sql(safe_sql)
    except SqlConsoleNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - report execution errors to the UI
        result["error"] = (
            "查询结果过大，请减少返回的列或行。"
            if isinstance(exc, ValueError)
            else "查询执行错误：请检查字段、表名或权限。"
        )
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
