"""
denticon_proxy.py  — api-gateway-railway

Proxy routes called by ins-verify-api.
Handles ALL Denticon field mapping, normalization, and data enrichment here
so ins-verify-api has zero knowledge of Denticon's API shape.

Returns clean, normalized PatientRecord objects ready for the verification queue.

Endpoint controlled by DENTICON_ENDPOINT env var:
  DENTICON_ENDPOINT=denticon          (staging)
  DENTICON_ENDPOINT=denticon-prod     (production)
"""

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from database import SessionLocal
from api_caller import APICaller
from datetime import datetime, timedelta, timezone
import os

router = APIRouter(prefix="/proxy/denticon", tags=["denticon-proxy"])

GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "")
DENTICON_ENDPOINT = os.environ.get("DENTICON_ENDPOINT", "denticon")


def _verify_internal(request: Request):
    key = request.headers.get("X-Gateway-API-Key", "")
    if not GATEWAY_API_KEY or key != GATEWAY_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid internal API key")


async def _call(method: str, path: str, params: dict = None, body: dict = None) -> dict:
    db = SessionLocal()
    try:
        caller = APICaller(db)
        return await caller.call(
            DENTICON_ENDPOINT, method, path,
            params=params, body=body,
            triggered_by="ins-verify-proxy",
        )
    finally:
        db.close()


# ── Field mapping helpers ─────────────────────────────────────────────────────

def _map_appointment(appt: dict, office_id: str) -> dict | None:
    """
    Maps a raw Denticon appointment to our standard PatientRecord shape.
    Returns None for block appointments, training days, or records without
    a real patient.

    Real Denticon field names (confirmed from staging):
      appointmentId, patientId, firstName, lastName,
      cellPhone, homePhone, workPhone, email,
      appointmentDate, appointmentLength, appointmentStatus,
      providerId, operatoryId, procedureCodes[].procedureCode,
      isNewPatient, isBlock, isCancelled, isMissed
    """
    # Filter out blocks, training days, cancelled, missed
    if appt.get("isBlock"):
        return None
    if appt.get("isCancelled"):
        return None
    if appt.get("isMissed"):
        return None

    # Filter appointments with no real patient name
    first = appt.get("firstName", "").strip()
    last = appt.get("lastName", "").strip()
    if not first or not last:
        return None
    if last.upper() in ("TRAINING DAY 1", "TRAINING DAY 2", "BLOCK", "CLOSED"):
        return None

    patient_id = appt.get("patientId")
    appt_id = appt.get("appointmentId")
    provider_id = str(appt.get("providerId", ""))

    # Extract CDT procedure codes
    procedures = [
        p.get("procedureCode", "")
        for p in (appt.get("procedureCodes") or [])
        if p.get("procedureCode")
    ]

    # Phone — prefer cell, fall back to home, then work
    phone = (
        appt.get("cellPhone") or
        appt.get("homePhone") or
        appt.get("workPhone") or
        ""
    )

    return {
        "patientId": f"PT-JD-{office_id}-{patient_id or appt_id}",
        "denticonPatientId": str(patient_id) if patient_id else None,
        "firstName": first,
        "lastName": last,
        "dob": None,          # Not on appointment — fetched separately if needed
        "phone": phone,
        "email": appt.get("email"),
        "officeId": f"JD-{office_id}",
        "officeName": f"Jefferson Dental - Office {office_id}",
        "appointment": {
            "apptId": f"APT-JD-{office_id}-{appt_id}",
            "denticonApptId": appt_id,
            "date": appt.get("appointmentDate", ""),
            "provider": None,         # Resolved from provider cache
            "providerDenticonId": provider_id,
            "providerNpi": None,      # Resolved from provider cache
            "duration": appt.get("appointmentLength"),
            "status": appt.get("appointmentStatus", "Scheduled"),
            "procedures": procedures,
            "isNewPatient": appt.get("isNewPatient", False),
            "notes": None,
        },
        "insurance": {
            "primary": None,    # Fetched separately via /insurance/{patientId}
            "secondary": None,
        },
        "verificationStatus": "PENDING",
        "pulledAt": datetime.now(timezone.utc).isoformat(),
    }


def _map_provider(prov: dict) -> dict:
    """
    Maps a raw Denticon provider to our standard shape.
    Real Denticon field names (confirmed from staging):
      providerId, providerShortId, firstName, lastName, title,
      providerType, active, isBookableOnline,
      nationalProviderId (= NPI Type 1),
      licenseNumber, officeId
    """
    full_name = " ".join(x for x in [
        prov.get("title", ""),
        prov.get("firstName", ""),
        prov.get("lastName", ""),
    ] if x).strip()

    return {
        "providerId": prov.get("providerId"),
        "providerShortId": prov.get("providerShortId"),
        "fullName": full_name,
        "firstName": prov.get("firstName"),
        "lastName": prov.get("lastName"),
        "title": prov.get("title"),
        "providerType": prov.get("providerType"),
        "npi": prov.get("nationalProviderId"),      # Type 1 individual NPI
        "licenseNumber": prov.get("licenseNumber"),
        "active": prov.get("active", True),
        "isBookableOnline": prov.get("isBookableOnline", False),
        "officeId": prov.get("officeId"),
    }


def _map_insurance(ins: dict) -> dict:
    """
    Maps a raw Denticon insurance record to our standard shape.
    Field names TBD — will update once we confirm from staging response.
    """
    return {
        "carrierId": str(ins.get("insuranceCarrierId", ins.get("carrierId", ""))),
        "carrierName": (
            ins.get("carrierName") or
            ins.get("insuranceCarrierName") or
            ins.get("planName") or ""
        ),
        "subscriberFirstName": (
            ins.get("subscriberFirstName") or
            ins.get("insuredFirstName")
        ),
        "subscriberLastName": (
            ins.get("subscriberLastName") or
            ins.get("insuredLastName")
        ),
        "subscriberDob": (
            (ins.get("subscriberBirthDate") or
             ins.get("insuredBirthDate") or "")[:10] or None
        ),
        "memberId": (
            ins.get("memberId") or
            ins.get("subscriberId") or
            ins.get("insuredId")
        ),
        "groupNo": (
            ins.get("groupNumber") or
            ins.get("groupNo")
        ),
        "relationship": (
            ins.get("relationship") or
            ins.get("patientRelationship") or
            "Self"
        ),
        "planType": (
            ins.get("planType") or
            ins.get("insuranceType") or
            "PPO"
        ),
        "sequence": ins.get("insuranceSequence", ins.get("sequence", 1)),
    }


def _enrich_with_providers(records: list[dict], providers: list[dict]) -> list[dict]:
    """
    Adds provider name and NPI to each appointment record using
    the provider list pulled for this office.
    """
    provider_map = {str(p["providerId"]): p for p in providers}
    for rec in records:
        prov_id = rec["appointment"].get("providerDenticonId")
        prov = provider_map.get(str(prov_id))
        if prov:
            rec["appointment"]["provider"] = prov["fullName"]
            rec["appointment"]["providerNpi"] = prov["npi"]
    return records


# ── Proxy endpoints ───────────────────────────────────────────────────────────

@router.get("/appointments/upcoming")
async def proxy_appointments(
    request: Request,
    office_id: str = Query(...),
    window_days: int = Query(3),
):
    """
    Fetches scheduled appointments within the verification window,
    normalizes to PatientRecord shape, and enriches with provider NPI.
    Filters out blocks, training days, cancelled and missed appointments.
    """
    _verify_internal(request)

    now = datetime.now(timezone.utc)
    end = now + timedelta(days=window_days)

    # Fetch appointments
    appt_result = await _call(
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

    if not appt_result.get("success"):
        return JSONResponse(content={
            "success": False,
            "error": appt_result.get("error"),
            "patients": [],
            "total": 0,
        })

    raw_appointments = (appt_result.get("data") or {}).get("data") or []

    # Filter appointments to only the window (Denticon may ignore date params)
    window_start = now.replace(tzinfo=timezone.utc)
    window_end = end.replace(tzinfo=timezone.utc)
    filtered = []
    for appt in raw_appointments:
        appt_date_str = appt.get("appointmentDate", "")
        try:
            appt_date = datetime.fromisoformat(appt_date_str.replace("Z", "+00:00"))
            if window_start <= appt_date <= window_end:
                filtered.append(appt)
        except Exception:
            continue

    # Map to normalized shape, drop None (blocks etc.)
    records = [r for r in [_map_appointment(a, office_id) for a in filtered] if r]

    if not records:
        return JSONResponse(content={
            "success": True,
            "patients": [],
            "total": 0,
            "pulledAt": datetime.now(timezone.utc).isoformat(),
        })

    # Fetch providers to enrich with names + NPIs
    prov_result = await _call(
        "GET",
        "/denticon/practices/v0/providers",
        params={"OfficeId": office_id},
    )
    providers = []
    if prov_result.get("success"):
        raw_providers = (prov_result.get("data") or {}).get("data") or []
        providers = [_map_provider(p) for p in raw_providers]

    records = _enrich_with_providers(records, providers)

    return JSONResponse(content={
        "success": True,
        "patients": records,
        "total": len(records),
        "pulledAt": datetime.now(timezone.utc).isoformat(),
        "officeId": office_id,
        "windowDays": window_days,
    })


@router.get("/patients/{patient_id}")
async def proxy_patient(request: Request, patient_id: str):
    """Fetch patient demographics."""
    _verify_internal(request)
    result = await _call("GET", f"/denticon/patients/v0/{patient_id}")
    return JSONResponse(content=result)


@router.get("/insurance/{patient_id}")
async def proxy_insurance(request: Request, patient_id: str):
    """
    Fetch and normalize patient insurance plans.
    Returns primary and secondary in our standard shape.
    """
    _verify_internal(request)
    result = await _call("GET", f"/denticon/patients/v0/{patient_id}/insurances")

    if not result.get("success"):
        return JSONResponse(content=result)

    raw = (result.get("data") or {}).get("data") or []
    if isinstance(raw, dict):
        raw = [raw]

    plans = [_map_insurance(i) for i in raw]
    primary = next((p for p in plans if p.get("sequence") == 1), None)
    secondary = next((p for p in plans if p.get("sequence") == 2), None)

    return JSONResponse(content={
        "success": True,
        "primary": primary,
        "secondary": secondary,
        "all": plans,
    })


@router.get("/providers/{office_id}")
async def proxy_providers(request: Request, office_id: str):
    """
    Fetch and normalize all providers for an office.
    Includes NPI (nationalProviderId) for EDI use.
    """
    _verify_internal(request)
    result = await _call(
        "GET",
        "/denticon/practices/v0/providers",
        params={"OfficeId": office_id},
    )

    if not result.get("success"):
        return JSONResponse(content=result)

    raw = (result.get("data") or {}).get("data") or []
    providers = [_map_provider(p) for p in raw]

    return JSONResponse(content={
        "success": True,
        "providers": providers,
        "total": len(providers),
    })
