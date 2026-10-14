"""MCP-only credentials. Platform JWTs and external user-id claims are not accepted."""
import hashlib
from datetime import datetime, timezone

from mcp.server.auth.provider import AccessToken
from sqlalchemy import select

from ... import db
from ...config import settings
from ...db.models import IntegrationToken, User

SCOPES = ("metadata:read", "analytics:ecommerce", "analytics:media", "reports:read")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class PlatformTokenVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if not settings.workbuddy_mcp_enabled or not token.startswith("rpa_wb_") or len(token) > 256:
            return None
        async with db.AsyncSessionLocal() as session:
            row = (await session.execute(
                select(IntegrationToken, User)
                .join(User, User.id == IntegrationToken.user_id)
                .where(IntegrationToken.token_hash == token_hash(token))
            )).one_or_none()
            if row is None:
                return None
            credential, user = row
            if (credential.revoked_at is not None or
                credential.expires_at <= datetime.now(timezone.utc) or
                not user.is_active or user.role not in {"analyst", "admin"}):
                return None
            return AccessToken(
                token=token, client_id=str(credential.id), subject=str(user.id),
                scopes=[s for s in credential.scopes if s in SCOPES],
                expires_at=int(credential.expires_at.timestamp()),
            )
