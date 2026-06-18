"""
denticon_proxy.py  — api-gateway-railway

Proxy routes called by ins-verify-api instead of hitting Planet DDS directly.
Uses the existing APICaller + TokenManager so credentials stay in the DB
and all calls are logged in the gateway dashboard.

Endpoint name controlled by DENTICON_ENDPOINT env var:
  DENTICON_ENDPOINT=denticon          (staging)
  DENTICON_ENDPOINT=denticon-prod     (production, when ready)
"""

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from database import SessionLocal
from api_caller import APICaller
import os

router = APIRouter(prefix="/proxy/denticon", tags=["denticon-proxy"])

GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "")
DENTICON_ENDPOINT = os.environ.get("DENTICON_ENDPOINT", "denticon")


def _verify_internal(request: Request):
    """Validates the shared secret sent by ins-verify-api."""
    key = request.headers.get("X-Gateway-API-Key", "")
    if not GATEWAY_API_KEY or key != GATEWAY_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid internal API key")


async def _call(method: str, path: str, params: dict = None, body: dict = None) -> dict:
    """Makes an authenticated Denticon call via the existing APICaller."""
    db = SessionLocal()
    try:
        caller = APICaller(db)
        result = await caller.call(
            DENTICON_ENDPOINT,
            method,
            path,
            params=params,
            body=body,
            triggered_by="ins-verify-proxy",
        )
        return result
    finally:
        db.close()


@router.get("/appointments/upcoming")
async def proxy_appointments(
    request: Request,
    office_id: str = Query(...),
    window_days: int = Query(3),
):
    """
    Fetch scheduled appointments for an office within the next N days.
    Denticon path: /denticon/appointments/v0/
    Filter by OfficeId + date range.
    """
    _verify_internal(request)

    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=window_days)

    result = await _call(
        "GET",
        "/denticon/appointments/v0/",
        params={
            "OfficeId": office_id,
            "StartDate": now.strftime("%Y-%m-%d"),
            "EndDate": end.strftime("%Y-%m-%d"),
            "PageSize": 500,
            "PageNumber": 1,
        },
    )
    return JSONResponse(content=result)


@router.get("/patients/{patient_id}")
async def proxy_patient(request: Request, patient_id: str):
    """
    Fetch patient demographics.
    Denticon path: /denticon/patients/v0/{patient_id}
    """
    _verify_internal(request)
    result = await _call("GET", f"/denticon/patients/v0/{patient_id}")
    return JSONResponse(content=result)


@router.get("/insurance/{patient_id}")
async def proxy_insurance(request: Request, patient_id: str):
    """
    Fetch patient insurance plans.
    Denticon path: /denticon/patients/v0/{patient_id}/insurances
    """
    _verify_internal(request)
    result = await _call("GET", f"/denticon/patients/v0/{patient_id}/insurances")
    return JSONResponse(content=result)


@router.get("/providers/{office_id}")
async def proxy_providers(request: Request, office_id: str):
    """
    Fetch all providers for an office — used to warm NPI cache.
    Denticon path: /denticon/practices/v0/providers?OfficeId=126
    Confirmed working from actions.py refresh_practice_reference.
    """
    _verify_internal(request)
    result = await _call(
        "GET",
        "/denticon/practices/v0/providers",
        params={"OfficeId": office_id},
    )
    return JSONResponse(content=result)
