"""
denticon_proxy.py  — api-gateway-railway

Proxy routes called by ins-verify-api instead of hitting Planet DDS directly.
Uses the existing APICaller + TokenManager so credentials stay in the DB
and all calls are logged in the gateway dashboard.

Endpoint name in gateway DB: "Staging-Denticon"
"""

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from database import SessionLocal
from api_caller import APICaller
import os

router = APIRouter(prefix="/proxy/denticon", tags=["denticon-proxy"])

GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "")
DENTICON_ENDPOINT = "Staging-Denticon"


def _verify_internal(request: Request):
    """Validates the shared secret sent by ins-verify-api."""
    key = request.headers.get("X-Gateway-API-Key", "")
    if not GATEWAY_API_KEY or key != GATEWAY_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid internal API key")


async def _call(method: str, path: str, params: dict = None) -> dict:
    """Makes an authenticated Denticon call via the existing APICaller."""
    db = SessionLocal()
    try:
        caller = APICaller(db)
        result = await caller.call(
            DENTICON_ENDPOINT,
            method,
            path,
            params=params,
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
    """Proxy to Denticon GetAppointments — called by ins-verify-api."""
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
    """Proxy to Denticon GetPatient."""
    _verify_internal(request)
    result = await _call("GET", f"/denticon/patients/v0/{patient_id}")
    return JSONResponse(content=result)


@router.get("/insurance/{patient_id}")
async def proxy_insurance(request: Request, patient_id: str):
    """Proxy to Denticon GetPatientInsurance."""
    _verify_internal(request)
    result = await _call(
        "GET",
        "/denticon/insurance/v0/patient/{patient_id}".replace("{patient_id}", patient_id),
    )
    return JSONResponse(content=result)


@router.get("/providers/{office_id}")
async def proxy_providers(request: Request, office_id: str):
    """Proxy to Denticon GetProvidersByOffice — used to warm NPI cache."""
    _verify_internal(request)
    result = await _call(
        "GET",
        "/denticon/providers/v0/",
        params={"OfficeId": office_id},
    )
    return JSONResponse(content=result)
