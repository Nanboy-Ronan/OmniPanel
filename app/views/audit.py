"""Audit events the server cannot observe on its own.

Sign-out only clears the browser's token, and CSV downloads of already-loaded
tables are built in the browser. The console reports both here so the
operation log shows who left and who took data out.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from ..auth import current_active_user
from ..utils.logger import log_operation

router = APIRouter(tags=["audit"])


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def logout(request: Request, user=Depends(current_active_user)) -> Response:
    await log_operation(str(user.id), "logout", request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


class ClientExport(BaseModel):
    source: str = Field(..., max_length=120)
    rows: int = Field(..., ge=0)
    columns: list[str] = Field(default_factory=list, max_length=200)


@router.post("/audit/export", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def client_export(
    payload: ClientExport, request: Request, user=Depends(current_active_user),
) -> Response:
    await log_operation(
        str(user.id),
        "export_client",
        {"source": payload.source, "rows": payload.rows, "columns": [c[:60] for c in payload.columns]},
        request=request,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
