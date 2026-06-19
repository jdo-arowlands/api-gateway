"""
denticon_proxy.py  — api-gateway-railway

Proxy routes called by ins-verify-api.
All API paths and default params are configured in the portal as APIOperations
— zero hardcoded Denticon paths in this file.

Operations configured in portal (create these via /api/operations):
  denticon-appointments   GET  /denticon/appointments/v0/      {"PageSize":500,"PageNumber":1}
  denticon-patient        GET  /denticon/patients/v0/{id}      {}
  denticon-insurance      GET  /denticon/patients/v0/{id}/insurances  {}
  denticon-providers      GET  /denticon/practices/v0/providers  {"PageSize":1000}

DENTICON_ENDPOINT env var controls which APIEndpoint to use:
  DENTICON_ENDPOINT=denticon          (staging)
  DENTICON_ENDPOINT=denticon-prod     (production)
"""

from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import JSONResponse
from database import SessionLocal, APIOperation
from api_caller import APICaller
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo
import os

router = APIRouter(prefix="/proxy/denticon", tags=["denticon-proxy"])

GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "")
DENTICON_ENDPOINT = os.environ.get("DENTICON_ENDPOINT", "denticon")


# -- Auth --

def _verify_internal(request: Request):
    key = request.headers.get("X-Gateway-API-Key", "")
    if not GATEWAY_API_KEY or key != GATEWAY_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid internal API key")


# -- Timezone helper --

def _now_local() -> datetime:
    tz_name = os.environ.get("TZ", "America/Chicago")
    try:
        return datetime.now(ZoneInfo(tz_name))
    except Exception:
        return datetime.now(timezone.utc)


def _format_local(dt: datetime) -> str:
    tz_name = os.environ.get("TZ", "America/Chicago")
    try:
        local_dt = dt.astimezone(ZoneInfo(tz_name))
        return local_dt.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    except Exception:
        return dt.isoformat()


# -- Operation caller --

async def _op(operation_name: str, params: dict = None, body: dict = None) -> dict:
    """
    Calls a named portal operation via APICaller.call_operation().
    Falls back to a direct path call if the operation isn't configured yet.
    """
    db = SessionLocal()
    try:
        caller = APICaller(db)
        return await caller.call_operation(
            operation_name,
            params=params,
            body=body,
            triggered_by="ins-verify-proxy",
        )
    finally:
        db.close()


# -- Generic passthrough route --
# Handles ANY operation configured in the portal with zero code changes.
# For pure passthrough operations (no filtering/reshaping needed).
#
#   GET  /proxy/denticon/op/{operation_name}?OfficeId=102&PageSize=50
#   POST /proxy/denticon/op/{operation_name}   (with JSON body)
#
# Query params are passed straight through to the operation (merged with the
# operation's default_params). Path params like {patient_id} in the operation
# path are substituted from query params of the same name.

@router.api_route("/op/{operation_name}", methods=["GET", "POST"])
async def generic_operation(operation_name: str, request: Request):
    """
    Generic passthrough — call any portal operation by name.
    Add an operation in the portal and it works here immediately, no code.
    """
    _verify_internal(request)

    # Collect query params as the runtime params
    params = dict(request.query_params)

    # Body for POST
    body = None
    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = None

    # Look up the operation to handle path-param substitution ({id}, {patient_id})
    db = SessionLocal()
    try:
        caller = APICaller(db)
        op = db.query(APIOperation).filter(
            APIOperation.name == operation_name,
            APIOperation.is_active == True,
        ).first()

        if not op:
            return JSONResponse(
                status_code=404,
                content={"success": False, "error": f"Operation '{operation_name}' not found or inactive"},
            )

        # Substitute any {placeholder} in the path from matching query params,
        # then remove those params so they aren't also sent as query string.
        path = op.path
        used_keys = []
        for key, val in list(params.items()):
            token = "{" + key + "}"
            if token in path:
                path = path.replace(token, str(val))
                used_keys.append(key)
        for k in used_keys:
            params.pop(k, None)

        # Merge default params (runtime params win)
        merged = {**(op.default_params or {}), **params}

        result = await caller.call(
            op.endpoint_name,
            op.method,
            path,
            params=merged or None,
            body=body if body else (op.default_body or None),
            triggered_by="ins-verify-proxy:generic",
        )
        return JSONResponse(content=result)
    finally:
        db.close()


# -- Field mapping --

def _map_appointment(appt: dict, office_id: str) -> dict | None:
    """Maps raw Denticon appointment to PatientRecord shape. Returns None for non-patient records."""
    if appt.get("isBlock") or appt.get("isCancelled") or appt.get("isMissed"):
        return None

    first = appt.get("firstName", "").strip()
    last = appt.get("lastName", "").strip()
    if not first or not last:
        return None
    if last.upper() in ("TRAINING DAY 1", "TRAINING DAY 2", "BLOCK", "CLOSED"):
        return None

    patient_id = appt.get("patientId")
    appt_id = appt.get("appointmentId")

    procedures = [
        p.get("procedureCode", "")
        for p in (appt.get("procedureCodes") or [])
        if p.get("procedureCode")
    ]

    phone = appt.get("cellPhone") or appt.get("homePhone") or appt.get("workPhone") or ""

    return {
        "patientId": f"PT-JD-{office_id}-{patient_id or appt_id}",
        "denticonPatientId": str(patient_id) if patient_id else None,
        "firstName": first,
        "lastName": last,
        "dob": None,
        "phone": phone,
        "email": appt.get("email"),
        "officeId": f"JD-{office_id}",
        "officeName": f"Jefferson Dental - Office {office_id}",
        "appointment": {
            "apptId": f"APT-JD-{office_id}-{appt_id}",
            "denticonApptId": appt_id,
            "date": appt.get("appointmentDate", ""),
            "provider": None,
            "providerDenticonId": str(appt.get("providerId", "")),
            "providerNpi": None,
            "duration": appt.get("appointmentLength"),
            "status": appt.get("appointmentStatus", "Scheduled"),
            "procedures": procedures,
            "isNewPatient": appt.get("isNewPatient", False),
            "notes": None,
        },
        "insurance": {"primary": None, "secondary": None},
        "verificationStatus": "PENDING",
        "pulledAt": _format_local(_now_local()),
    }


def _map_provider(prov: dict) -> dict:
    full_name = " ".join(x for x in [
        prov.get("title", ""), prov.get("firstName", ""), prov.get("lastName", "")
    ] if x).strip()
    return {
        "providerId": prov.get("providerId"),
        "providerShortId": prov.get("providerShortId"),
        "fullName": full_name,
        "firstName": prov.get("firstName"),
        "lastName": prov.get("lastName"),
        "title": prov.get("title"),
        "providerType": prov.get("providerType"),
        "npi": prov.get("nationalProviderId"),
        "licenseNumber": prov.get("licenseNumber"),
        "active": prov.get("active", True),
        "isBookableOnline": prov.get("isBookableOnline", False),
        "officeId": prov.get("officeId"),
    }


def _map_insurance(ins: dict) -> dict:
    """
    Maps a raw Denticon insurance plan to our standard shape.
    Field names confirmed from Planet DDS Insurance Plans API docs.
    insuranceType is "Primary" | "Secondary".
    """
    return {
        "insuranceType": ins.get("insuranceType"),          # Primary | Secondary
        "carrierId": ins.get("carrierId"),
        "carrierName": ins.get("carrierName") or "",
        "payerId": ins.get("payerId"),                       # ECLAIMS payer id — key for EDI
        "carrierPhone": ins.get("carrierPhone"),
        "carrierAddress": {
            "line1": ins.get("carrierAddressLine1"),
            "line2": ins.get("carrierAddressLine2"),
            "city": ins.get("carrierCity"),
            "state": ins.get("carrierState"),
            "zip": ins.get("carrierZip"),
        },
        "subscriberId": ins.get("subscriberId"),
        "groupNo": ins.get("groupNo"),
        "subscriberFirstName": ins.get("subscriberFirstName"),
        "subscriberLastName": ins.get("subscriberLastName"),
        "subscriberDob": ((ins.get("subscriberBirthDate") or "")[:10]) or None,
        "subscriberSex": ins.get("subscriberSex"),
        "relationToSubscriber": ins.get("relationToSubscriber") or "Self",
        "planType": ins.get("planType"),                     # Dental | Medical
        "planCategory": ins.get("planCategory"),
        "insurancePlanId": ins.get("insurancePlanId"),
        "anniversaryDate": ins.get("anniversaryDate"),
        # Benefit data — gold for verification
        "benefits": {
            "individualDeductible": ins.get("individualDeductible"),
            "familyDeductible": ins.get("familyDeductible"),
            "individualMaxBenefit": ins.get("individualMaxBenefit"),
            "familyMaxBenefit": ins.get("familyMaxBenefit"),
            "individualOrthoMaxBenefit": ins.get("individualOrthoMaxBenefit"),
            "individualRemainingDeductible": ins.get("individualRemainingDeductible"),
            "individualRemainingMax": ins.get("individualRemainingMax"),
            "individualRemainingOrtho": ins.get("individualRemainingOrtho"),
            "familyRemainingDeductible": ins.get("familyRemainingDeductible"),
            "familyRemainingMax": ins.get("familyRemainingMax"),
        },
    }


def _split_insurance(plans: list) -> dict:
    """Splits insurance plans into primary/secondary by insuranceType."""
    mapped = [_map_insurance(p) for p in plans]
    primary = next((p for p in mapped if (p.get("insuranceType") or "").lower() == "primary"), None)
    secondary = next((p for p in mapped if (p.get("insuranceType") or "").lower() == "secondary"), None)
    # Fallback: if types not labelled, first = primary, second = secondary
    if not primary and mapped:
        primary = mapped[0]
    if not secondary and len(mapped) > 1:
        secondary = mapped[1]
    return {"primary": primary, "secondary": secondary, "all": mapped}


# -- Per-patient enrichment helpers --

async def _fetch_patient_dob(patient_id: str) -> str | None:
    """Fetch a single patient's DOB via denticon-patient operation."""
    db = SessionLocal()
    try:
        caller = APICaller(db)
        op = db.query(APIOperation).filter(
            APIOperation.name == "denticon-patient",
            APIOperation.is_active == True,
        ).first()
        if not op:
            return None
        path = op.path.replace("{patient_id}", patient_id).replace("{id}", patient_id)
        result = await caller.call(op.endpoint_name, op.method, path, triggered_by="ins-verify-proxy:enrich")
        if not result.get("success"):
            return None
        outer = result.get("data") or {}
        patient = outer.get("data") if isinstance(outer, dict) else None
        if isinstance(patient, dict):
            return (patient.get("birthDate") or "")[:10] or None
    finally:
        db.close()
    return None


async def _fetch_patient_insurance(patient_id: str) -> dict:
    """Fetch a single patient's insurance via denticon-insurance operation."""
    db = SessionLocal()
    try:
        caller = APICaller(db)
        op = db.query(APIOperation).filter(
            APIOperation.name == "denticon-insurance",
            APIOperation.is_active == True,
        ).first()
        if not op:
            return {"primary": None, "secondary": None, "all": []}
        path = op.path.replace("{patient_id}", patient_id).replace("{id}", patient_id)
        result = await caller.call(op.endpoint_name, op.method, path, triggered_by="ins-verify-proxy:enrich")
        if not result.get("success"):
            return {"primary": None, "secondary": None, "all": []}
        plans = _extract_list(result)
        if isinstance(plans, dict):
            plans = [plans]
        return _split_insurance(plans)
    finally:
        db.close()


def _enrich_with_providers(records: list, providers: list) -> list:
    provider_map = {str(p["providerId"]): p for p in providers}
    for rec in records:
        prov_id = rec["appointment"].get("providerDenticonId")
        prov = provider_map.get(str(prov_id))
        if prov:
            rec["appointment"]["provider"] = prov["fullName"]
            rec["appointment"]["providerNpi"] = prov["npi"]
    return records


def _extract_list(result: dict) -> list:
    """Safely extract the data array from an APICaller result."""
    outer = result.get("data") or {}
    if isinstance(outer, dict) and "data" in outer:
        return outer.get("data") or []
    if isinstance(outer, list):
        return outer
    return []


# -- Proxy endpoints --

@router.get("/appointments/upcoming")
async def proxy_appointments(
    request: Request,
    office_id: str = Query(...),
    window_days: int = Query(3),
    enrich: bool = Query(False),
):
    """
    Fetch and normalize upcoming appointments for an office.
    Uses portal operation: denticon-appointments

    enrich=false (default) — fast: names, appointment, provider NPI only
    enrich=true            — full: also fetches DOB + insurance per patient
                             (slower; use for background batch pulls)
    """
    _verify_internal(request)

    now = datetime.now(timezone.utc)
    end = now + timedelta(days=window_days)

    result = await _op(
        "denticon-appointments",
        params={
            "OfficeId": office_id,
            "StartDate": now.strftime("%Y-%m-%d"),
            "EndDate": end.strftime("%Y-%m-%d"),
        },
    )

    if not result.get("success"):
        return JSONResponse(content={"success": False, "error": result.get("error"), "patients": [], "total": 0})

    raw = _extract_list(result)

    # Filter to window — Denticon ignores date params so we filter in Python
    window_start = now.replace(tzinfo=None) - timedelta(days=1)
    window_end = end.replace(tzinfo=None)
    filtered = []
    for appt in raw:
        try:
            appt_date = datetime.fromisoformat(
                appt.get("appointmentDate", "").replace("Z", "").replace("+00:00", "")
            )
            if window_start <= appt_date <= window_end:
                filtered.append(appt)
        except Exception:
            continue

    records = [r for r in [_map_appointment(a, office_id) for a in filtered] if r]

    if not records:
        return JSONResponse(content={
            "success": True, "patients": [], "total": 0,
            "pulledAt": _format_local(_now_local()),
        })

    # Enrich with provider NPIs
    prov_result = await _op("denticon-providers", params={"OfficeId": office_id})
    providers = []
    if prov_result.get("success"):
        providers = [_map_provider(p) for p in _extract_list(prov_result)]

    records = _enrich_with_providers(records, providers)

    # Optional deep enrichment — DOB + insurance per patient
    if enrich:
        import asyncio
        for rec in records:
            pid = rec.get("denticonPatientId")
            if not pid:
                continue
            dob, ins = await asyncio.gather(
                _fetch_patient_dob(pid),
                _fetch_patient_insurance(pid),
            )
            if dob:
                rec["dob"] = dob
            rec["insurance"]["primary"] = ins.get("primary")
            rec["insurance"]["secondary"] = ins.get("secondary")

    return JSONResponse(content={
        "success": True,
        "patients": records,
        "total": len(records),
        "enriched": enrich,
        "pulledAt": _format_local(_now_local()),
        "officeId": office_id,
        "windowDays": window_days,
    })


@router.get("/patients/{patient_id}")
async def proxy_patient(request: Request, patient_id: str):
    """
    Fetch patient demographics.
    Uses portal operation: denticon-patient
    Operation path should be: /denticon/patients/v0/{patient_id}
    Pass patient_id as runtime param to substitute in path.
    """
    _verify_internal(request)
    # For path-param operations, pass the ID as a param so the operation
    # path template can include it, or we append to the base path
    db = SessionLocal()
    try:
        caller = APICaller(db)
        op = db.query(APIOperation).filter(
            APIOperation.name == "denticon-patient",
            APIOperation.is_active == True,
        ).first()
        if op:
            path = op.path.replace("{patient_id}", patient_id).replace("{id}", patient_id)
            result = await caller.call(op.endpoint_name, op.method, path, triggered_by="ins-verify-proxy")
        else:
            result = {"success": False, "error": "Operation 'denticon-patient' not configured"}
    finally:
        db.close()
    return JSONResponse(content=result)


@router.get("/insurance/{patient_id}")
async def proxy_insurance(request: Request, patient_id: str):
    """
    Fetch and normalize patient insurance.
    Uses portal operation: denticon-insurance
    """
    _verify_internal(request)
    db = SessionLocal()
    try:
        caller = APICaller(db)
        op = db.query(APIOperation).filter(
            APIOperation.name == "denticon-insurance",
            APIOperation.is_active == True,
        ).first()
        if op:
            path = op.path.replace("{patient_id}", patient_id).replace("{id}", patient_id)
            result = await caller.call(op.endpoint_name, op.method, path, triggered_by="ins-verify-proxy")
        else:
            result = {"success": False, "error": "Operation 'denticon-insurance' not configured"}
    finally:
        db.close()

    if not result.get("success"):
        return JSONResponse(content=result)

    raw = _extract_list(result)
    if isinstance(raw, dict):
        raw = [raw]
    split = _split_insurance(raw)

    return JSONResponse(content={"success": True, **split})


@router.get("/providers/{office_id}")
async def proxy_providers(request: Request, office_id: str):
    """
    Fetch and normalize providers for an office.
    Uses portal operation: denticon-providers
    """
    _verify_internal(request)
    result = await _op("denticon-providers", params={"OfficeId": office_id})

    if not result.get("success"):
        return JSONResponse(content=result)

    providers = [_map_provider(p) for p in _extract_list(result)]
    return JSONResponse(content={"success": True, "providers": providers, "total": len(providers)})


@router.get("/patient-detail/{office_id}/{patient_id}")
async def proxy_patient_detail(request: Request, office_id: str, patient_id: str):
    """
    On-demand single-patient detail — DOB + insurance in one call.
    Used by the UI when a verifier opens a specific patient (load-as-you-go).
    Calls denticon-patient + denticon-insurance operations.
    """
    _verify_internal(request)

    import asyncio
    dob, ins = await asyncio.gather(
        _fetch_patient_dob(patient_id),
        _fetch_patient_insurance(patient_id),
    )

    return JSONResponse(content={
        "success": True,
        "denticonPatientId": patient_id,
        "officeId": f"JD-{office_id}",
        "dob": dob,
        "insurance": {
            "primary": ins.get("primary"),
            "secondary": ins.get("secondary"),
            "all": ins.get("all", []),
        },
        "pulledAt": _format_local(_now_local()),
    })
