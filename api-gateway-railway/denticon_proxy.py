"""
denticon_proxy.py  — ADD THIS TO api-gateway-railway

Proxy routes that the ins-verify-api calls instead of hitting Planet DDS directly.
Drop this file into your gateway project and register it in main.py.

All calls here:
  1. Use the existing APICaller / token management you already built
  2. Are logged automatically via the gateway's existing log middleware
  3. Can be monitored in the gateway dashboard

Mount in gateway main.py:
    from denticon_proxy import router as denticon_proxy_router
    app.include_router(denticon_proxy_router)
"""

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse
import httpx
import os

router = APIRouter(prefix="/proxy/denticon", tags=["denticon-proxy"])

# Shared secret — set GATEWAY_API_KEY in Railway vars on the gateway service
# Must match GATEWAY_API_KEY on the ins-verify-api service
GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "")

# Denticon staging config — already in your gateway .env
DENTICON_BASE_URL = os.environ.get("DENTICON_BASE_URL", "https://staging-api.planetdds.com")
DENTICON_SUBSCRIPTION_KEY = os.environ.get("DENTICON_SUBSCRIPTION_KEY", "")

# Token cache — reuse your existing token management if available,
# otherwise this module manages its own
_token_cache: dict = {"token": None, "expires_at": 0}


def _verify_internal(request: Request):
    key = request.headers.get("X-Gateway-API-Key", "")
    if not GATEWAY_API_KEY or key != GATEWAY_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid internal API key")


async def _denticon_token() -> str:
    """
    Reuse your gateway's existing token refresh logic here if possible.
    This is a standalone fallback.
    """
    import time
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]

    token_url = os.environ.get(
        "DENTICON_TOKEN_URL",
        "https://staging-idsvr-az.denticon.com/connect/token"
    )
    client_id = os.environ.get("DENTICON_CLIENT_ID", "")
    client_secret = os.environ.get("DENTICON_CLIENT_SECRET", "")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
                "scope": "api",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        _token_cache["token"] = data["access_token"]
        _token_cache["expires_at"] = now + data.get("expires_in", 3600)
        return _token_cache["token"]


def _denticon_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "PDDS-Subscription-Key": DENTICON_SUBSCRIPTION_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


@router.get("/appointments/upcoming")
async def proxy_appointments(
    request: Request,
    office_id: str = Query(...),
    window_days: int = Query(3),
):
    """Proxy to Denticon GetAppointments — called by ins-verify-api."""
    _verify_internal(request)

    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=window_days)

    token = await _denticon_token()
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{DENTICON_BASE_URL}/denticon/appointments/v0/",
            headers=_denticon_headers(token),
            params={
                "OfficeId": office_id,
                "StartDate": now.strftime("%Y-%m-%d"),
                "EndDate": end.strftime("%Y-%m-%d"),
                "PageSize": 500,
                "PageNumber": 1,
            },
        )
        resp.raise_for_status()
        return JSONResponse(content=resp.json())


@router.get("/patients/{patient_id}")
async def proxy_patient(request: Request, patient_id: str):
    """Proxy to Denticon GetPatient."""
    _verify_internal(request)
    token = await _denticon_token()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{DENTICON_BASE_URL}/denticon/patients/v0/{patient_id}",
            headers=_denticon_headers(token),
        )
        resp.raise_for_status()
        return JSONResponse(content=resp.json())


@router.get("/insurance/{patient_id}")
async def proxy_insurance(request: Request, patient_id: str):
    """Proxy to Denticon GetPatientInsurance."""
    _verify_internal(request)
    token = await _denticon_token()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{DENTICON_BASE_URL}/denticon/insurance/v0/patient/{patient_id}",
            headers=_denticon_headers(token),
        )
        resp.raise_for_status()
        return JSONResponse(content=resp.json())


@router.get("/providers/{office_id}")
async def proxy_providers(request: Request, office_id: str):
    """Proxy to Denticon GetProvidersByOffice — used to warm NPI cache."""
    _verify_internal(request)
    token = await _denticon_token()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"{DENTICON_BASE_URL}/denticon/providers/v0/",
            headers=_denticon_headers(token),
            params={"OfficeId": office_id},
        )
        resp.raise_for_status()
        return JSONResponse(content=resp.json())
