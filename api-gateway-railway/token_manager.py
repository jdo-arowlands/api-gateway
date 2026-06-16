"""
Token Manager
─────────────
Handles the full lifecycle of bearer tokens:
  • Fetches a new token when none exists
  • Detects expiry (with a 60-second safety buffer) and refreshes proactively
  • Stores token + expiry back to the DB after every refresh
  • Logs every token event to token_refresh_logs
"""
import httpx
import logging
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from database import APIEndpoint, TokenRefreshLog

logger = logging.getLogger("token_manager")


class TokenManager:
    # Refresh this many seconds before the token actually expires
    BUFFER_SECONDS = 60

    def __init__(self, db: Session):
        self.db = db

    # ── Public ────────────────────────────────────────────────────────────────

    async def get_token(self, endpoint: APIEndpoint) -> str | None:
        """Return a valid token, refreshing if necessary."""
        if endpoint.auth_type not in ("bearer", "oauth2"):
            return None

        if self._token_is_valid(endpoint):
            return endpoint.current_token

        logger.info(f"[{endpoint.name}] Token missing or expired – refreshing...")
        return await self._refresh_token(endpoint)

    def _token_is_valid(self, endpoint: APIEndpoint) -> bool:
        if not endpoint.current_token:
            return False
        if not endpoint.token_expires_at:
            return True   # no expiry info → assume valid
        cutoff = datetime.utcnow() + timedelta(seconds=self.BUFFER_SECONDS)
        return endpoint.token_expires_at > cutoff

    # ── Private ───────────────────────────────────────────────────────────────

    async def _refresh_token(self, endpoint: APIEndpoint) -> str | None:
        if not endpoint.token_url:
            logger.warning(f"[{endpoint.name}] No token_url configured – skipping refresh")
            return None

        payload = {
            "grant_type": "client_credentials",
            "client_id": endpoint.client_id,
            "client_secret": endpoint.client_secret,
        }
        if endpoint.token_scope:
            payload["scope"] = endpoint.token_scope

        success = False
        token = None
        expires_at = None
        error_msg = None

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(endpoint.token_url, data=payload)
                response.raise_for_status()
                data = response.json()

            token = data.get("access_token")
            expires_in = data.get("expires_in", 3600)
            expires_at = datetime.utcnow() + timedelta(seconds=int(expires_in))

            # Persist to DB
            endpoint.current_token = token
            endpoint.token_expires_at = expires_at
            endpoint.updated_at = datetime.utcnow()
            self.db.commit()
            self.db.refresh(endpoint)

            success = True
            logger.info(f"[{endpoint.name}] Token refreshed. Expires: {expires_at}")

        except Exception as exc:
            error_msg = str(exc)
            logger.error(f"[{endpoint.name}] Token refresh failed: {error_msg}")
            self.db.rollback()

        # Always log the attempt
        self.db.add(TokenRefreshLog(
            endpoint_id=endpoint.id,
            endpoint_name=endpoint.name,
            success=success,
            expires_at=expires_at,
            error=error_msg,
        ))
        self.db.commit()

        return token
