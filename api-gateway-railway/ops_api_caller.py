"""
API Caller
──────────
One-stop function for every outbound call:
  1. Resolve the endpoint config from the DB
  2. Obtain a valid token (refreshing if needed)
  3. Build headers (auth + custom)
  4. Execute the HTTP request
  5. Write a full APICallLog record regardless of success/failure
  6. Return a structured result dict

call_operation() — new method that looks up a named APIOperation from the DB,
merges default params, and dispatches via the existing call() method.
This keeps all paths/params in the portal, not in Python code.
"""
import time
import json
import logging
import httpx
from datetime import datetime
from typing import Any
from sqlalchemy.orm import Session
from database import APIEndpoint, APICallLog, APIOperation
from token_manager import TokenManager

logger = logging.getLogger("api_caller")


class APICaller:
    def __init__(self, db: Session):
        self.db = db
        self.token_mgr = TokenManager(db)

    # ── Public ────────────────────────────────────────────────────────────────

    async def call_operation(
        self,
        operation_name: str,
        *,
        params: dict | None = None,
        body: Any = None,
        extra_headers: dict | None = None,
        triggered_by: str = "system",
    ) -> dict:
        """
        Execute a named API operation configured in the portal.

        Looks up the APIOperation row by name, merges default_params with any
        runtime params passed in (runtime params take precedence), then calls
        the underlying endpoint via call().

        Returns the same structured result dict as call().
        """
        op = self.db.query(APIOperation).filter(
            APIOperation.name == operation_name,
            APIOperation.is_active == True,
        ).first()

        if not op:
            return self._err(
                f"Operation '{operation_name}' not found or inactive",
                None, 0, ""
            )

        # Merge default params from portal config with runtime params
        # Runtime params take precedence over defaults
        merged_params = {**(op.default_params or {}), **(params or {})}

        # Merge default body similarly
        merged_body = body
        if op.default_body and not body:
            merged_body = op.default_body

        return await self.call(
            op.endpoint_name,
            op.method,
            op.path,
            params=merged_params or None,
            body=merged_body,
            extra_headers=extra_headers,
            triggered_by=triggered_by,
        )

    async def call(
        self,
        endpoint_name: str,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        body: Any = None,
        extra_headers: dict | None = None,
        triggered_by: str = "system",
    ) -> dict:
        """
        Execute an authenticated call to a named endpoint.

        Returns:
            {
                "success": bool,
                "status_code": int | None,
                "data": any,
                "error": str | None,
                "response_time_ms": float,
                "log_id": int,
            }
        """
        endpoint = self._get_endpoint(endpoint_name)
        if not endpoint:
            return self._err(f"Endpoint '{endpoint_name}' not found", None, 0, "")

        if not endpoint.is_active:
            return self._err(f"Endpoint '{endpoint_name}' is disabled", endpoint, 0, "")

        url = endpoint.base_url.rstrip("/") + "/" + path.lstrip("/")
        headers = await self._build_headers(endpoint, extra_headers)
        token_refreshed = headers.pop("__token_refreshed__", False)

        start = time.monotonic()
        status_code = None
        response_body = None
        response_headers = {}
        success = False
        error_msg = None
        data = None

        try:
            async with httpx.AsyncClient(timeout=endpoint.default_timeout) as client:
                response = await client.request(
                    method.upper(),
                    url,
                    headers=headers,
                    params=params,
                    json=body if body is not None else None,
                )

            elapsed_ms = (time.monotonic() - start) * 1000
            status_code = response.status_code
            response_headers = dict(response.headers)

            try:
                data = response.json()
                response_body = json.dumps(data)
            except Exception:
                response_body = response.text
                data = response_body

            success = response.is_success
            if not success:
                error_msg = f"HTTP {status_code}: {response_body[:500]}"

        except Exception as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            error_msg = str(exc)
            logger.error(f"[{endpoint_name}] Request failed: {error_msg}")

        log = APICallLog(
            endpoint_id=endpoint.id,
            endpoint_name=endpoint_name,
            method=method.upper(),
            url=url,
            request_headers=self._safe_headers(headers),
            request_body=json.dumps(body) if body else None,
            status_code=status_code,
            response_headers=response_headers,
            response_body=response_body,
            response_time_ms=round(elapsed_ms, 2),
            success=success,
            error_message=error_msg,
            triggered_by=triggered_by,
            token_refreshed=token_refreshed,
            created_at=datetime.utcnow(),
        )
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)

        return {
            "success": success,
            "status_code": status_code,
            "data": data,
            "error": error_msg,
            "response_time_ms": round(elapsed_ms, 2),
            "log_id": log.id,
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _get_endpoint(self, name: str) -> APIEndpoint | None:
        return self.db.query(APIEndpoint).filter(APIEndpoint.name == name).first()

    async def _build_headers(self, endpoint: APIEndpoint, extra: dict | None) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token_refreshed = False

        if endpoint.extra_headers:
            headers.update(endpoint.extra_headers)

        try:
            project = endpoint.project
        except Exception:
            project = None
        if project and project.sub_key_header and project.sub_key_value:
            headers[project.sub_key_header] = project.sub_key_value

        if endpoint.auth_type in ("bearer", "oauth2"):
            old_token = endpoint.current_token
            token = await self.token_mgr.get_token(endpoint)
            if token:
                headers["Authorization"] = f"Bearer {token}"
                token_refreshed = (token != old_token)
        elif endpoint.auth_type == "api_key":
            if endpoint.api_key:
                headers[endpoint.api_key_header or "X-API-Key"] = endpoint.api_key
        elif endpoint.auth_type == "basic":
            import base64
            creds = base64.b64encode(
                f"{endpoint.client_id}:{endpoint.client_secret}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {creds}"

        if extra:
            headers.update(extra)

        headers["__token_refreshed__"] = token_refreshed
        return headers

    _SENSITIVE_HEADERS = {
        "authorization", "x-api-key", "ocp-apim-subscription-key",
        "api-key", "apikey", "x-subscription-key", "subscription-key",
        "__token_refreshed__",
    }
    _SENSITIVE_HINTS = ("secret", "token", "subscription-key", "apikey", "api-key", "password")

    def _safe_headers(self, headers: dict) -> dict:
        redacted = {}
        for k, v in headers.items():
            kl = k.lower()
            if kl in self._SENSITIVE_HEADERS or any(h in kl for h in self._SENSITIVE_HINTS):
                redacted[k] = "***REDACTED***"
            else:
                redacted[k] = v
        return redacted

    def _err(self, msg: str, endpoint, elapsed: float, url: str) -> dict:
        log = APICallLog(
            endpoint_name=endpoint.name if endpoint else "unknown",
            method="UNKNOWN",
            url=url,
            success=False,
            error_message=msg,
            response_time_ms=elapsed,
            triggered_by="system",
            created_at=datetime.utcnow(),
        )
        self.db.add(log)
        self.db.commit()
        self.db.refresh(log)
        return {"success": False, "status_code": None, "data": None,
                "error": msg, "response_time_ms": elapsed, "log_id": log.id}
