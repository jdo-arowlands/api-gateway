"""
Custom Job Actions — add your own here
Register with @register_action("action_name")
"""
from scheduler import register_action
from database import SessionLocal
from api_caller import APICaller
import logging

logger = logging.getLogger("actions")


# ── Shared helpers for Retell → Denticon actions ──────────────────────────────

def _get_to_number(ctx: dict) -> str | None:
    """Pull the dialed number from the Retell payload, checking common spots."""
    return ((ctx.get("raw", {}).get("call", {}) or {}).get("to_number")
            or (ctx.get("call", {}) or {}).get("to_number")
            or ctx.get("to_number"))


def _resolve_office(ctx: dict, db):
    """
    Resolve the Denticon office for this call. Returns (office_dict, error_message).
    On success error_message is None; on failure office_dict is None and
    error_message is a patient-friendly line for the agent to say.
    """
    from office_map import resolve_office_id
    office = resolve_office_id(db, _get_to_number(ctx))
    if not office["found"]:
        logger.warning(f"office unresolved: {office['error']}")
        return None, ("I'm having trouble identifying which office this is for. "
                      "Let me connect you with our team.")
    return office, None


# ── Retell Custom Function: find an existing patient ──────────────────────────

@register_action("find_patient")
async def find_patient(__context__: dict = None, __db__=None):
    """
    Looks up an existing patient in Denticon by name + birth date.
    Run this FIRST in the scheduling flow — it doubles as the duplicate guard
    before create_patient.

    Expected context["args"]: first_name, last_name, date_of_birth (yyyy-mm-dd)

    Returns:
        {"success": bool, "found": bool, "patient_id": int|None,
         "message": str, "patients": [...]}
    """
    ctx  = __context__ or {}
    args = ctx.get("args", {}) or {}
    db   = __db__ or SessionLocal()

    office, err = _resolve_office(ctx, db)
    if err:
        return {"success": False, "found": False, "patient_id": None, "message": err}

    first = args.get("first_name") or args.get("firstName")
    last  = args.get("last_name")  or args.get("lastName")
    dob   = args.get("date_of_birth") or args.get("dob") or args.get("birthDate")

    missing = [f for f, v in {"first name": first, "last name": last,
                              "date of birth": dob}.items() if not v]
    if missing:
        return {"success": False, "found": False, "patient_id": None,
                "message": f"I need the patient's {', '.join(missing)} to look up the record."}

    caller = APICaller(db)
    result = await caller.call(
        "denticon",
        "POST",
        "/denticon/patients/v0/check-existing-patient",
        body={
            "officeId":  int(office["office_id"]),
            "lastName":  last,
            "firstName": first,
            "birthDate": dob,           # yyyy-mm-dd
        },
        triggered_by="retell:find_patient",
    )

    if not result.get("success"):
        logger.error(f"find_patient failed: {result.get('error')}")
        return {"success": False, "found": False, "patient_id": None,
                "message": "I wasn't able to check our records just now. Let me get someone to help."}

    data = (result.get("data") or {}).get("data") or {}
    is_existing = bool(data.get("isExistingPatient"))
    patients = data.get("patients") or []

    if is_existing and patients:
        # Single clear match → return its id. Multiple → hand back the list so
        # the agent can disambiguate (e.g. confirm which one by birth date).
        if len(patients) == 1:
            p = patients[0]
            return {
                "success": True, "found": True,
                "patient_id": p.get("patientId"),
                "patients": patients,
                "message": f"I found your record, {p.get('firstName')}. ",
            }
        return {
            "success": True, "found": True,
            "patient_id": None, "patients": patients,
            "message": (f"I found {len(patients)} records matching that name and date of birth. "
                        "Can you confirm the phone number on file so I pick the right one?"),
        }

    # No match → caller should proceed to create_patient
    return {
        "success": True, "found": False, "patient_id": None, "patients": [],
        "message": "I don't see an existing record, so I'll get you set up as a new patient.",
    }


# ── Retell Custom Function: create a new patient (two-step) ───────────────────

@register_action("create_patient")
async def create_patient(__context__: dict = None, __db__=None):
    """
    Two-step Denticon new-patient creation, orchestrated server-side so the
    Retell agent makes a single call:

      1. POST online-registrations/new-patients          → temporaryPatientId
      2. POST .../{temporaryPatientId}/convert           → permanent patientId

    Conversion bypasses the manual Online Registered Patients queue, so a
    permanent record exists immediately. If step 1 succeeds but step 2 fails,
    an online registration is left in the queue for staff to handle — we report
    that distinctly rather than as a generic failure.

    Expected context["args"]:
        first_name, last_name, date_of_birth (yyyy-mm-dd), sex (Male/Female/Unspecified),
        cell_phone (or phone), email   [+ optional address fields]

    Returns:
        {"success": bool, "message": str, "patient_id": int|None,
         "temporary_patient_id": int|None, "needs_staff_review": bool}
    """
    ctx  = __context__ or {}
    args = ctx.get("args", {}) or {}
    db   = __db__ or SessionLocal()

    office, err = _resolve_office(ctx, db)
    if err:
        return {"success": False, "message": err, "patient_id": None,
                "temporary_patient_id": None, "needs_staff_review": False}

    first = args.get("first_name") or args.get("firstName")
    last  = args.get("last_name")  or args.get("lastName")
    dob   = args.get("date_of_birth") or args.get("dob") or args.get("birthDate")
    sex   = args.get("sex")
    cell  = args.get("cell_phone") or args.get("phone") or args.get("cellPhone")
    email = args.get("email")

    # 'sex' is the only API-required field; collect the basics for a usable record.
    missing = [f for f, v in {"first name": first, "last name": last,
                              "date of birth": dob, "sex": sex}.items() if not v]
    if missing:
        return {"success": False, "patient_id": None, "temporary_patient_id": None,
                "needs_staff_review": False,
                "message": f"Before I create the record I need the patient's {', '.join(missing)}."}

    # Normalize sex to the accepted values.
    sex_norm = {"m": "Male", "male": "Male", "f": "Female", "female": "Female"}\
        .get(str(sex).strip().lower(), "Unspecified")

    caller = APICaller(db)
    office_id = int(office["office_id"])

    # ── Step 1: create the temporary online patient ───────────────────────────
    body = {
        "officeId":  office_id,
        "firstName": first,
        "lastName":  last,
        "birthDate": dob,
        "sex":       sex_norm,
    }
    if cell:  body["cellPhone"] = cell
    if email: body["email"] = email
    # Record voice consent for AI reminders if the agent captured it.
    if args.get("opt_in_voice") is not None:
        body["optInVoice"] = bool(args.get("opt_in_voice"))

    step1 = await caller.call(
        "denticon", "POST",
        "/denticon/patients/v0/online-registrations/new-patients",
        body=body,
        triggered_by="retell:create_patient:step1",
    )

    if not step1.get("success"):
        logger.error(f"create_patient step1 failed: {step1.get('error')}")
        return {"success": False, "patient_id": None, "temporary_patient_id": None,
                "needs_staff_review": False,
                "message": "I'm sorry, I couldn't start the new patient record just now. "
                           "Let me connect you with our team."}

    temp_id = ((step1.get("data") or {}).get("data") or {}).get("temporaryPatientId")
    if not temp_id:
        logger.error(f"create_patient step1 returned no temp id: {step1.get('data')}")
        return {"success": False, "patient_id": None, "temporary_patient_id": None,
                "needs_staff_review": False,
                "message": "I'm sorry, something went wrong starting the record. "
                           "Let me connect you with our team."}

    # ── Step 2: convert temp → permanent ──────────────────────────────────────
    step2 = await caller.call(
        "denticon", "POST",
        f"/denticon/patients/v0/online-registrations/new-patients/{temp_id}/convert",
        triggered_by="retell:create_patient:step2",
    )

    if not step2.get("success"):
        # Orphan: temp registration exists but wasn't converted. Staff must review.
        logger.error(f"create_patient step2 (convert) failed for temp {temp_id}: {step2.get('error')}")
        return {
            "success": False,
            "patient_id": None,
            "temporary_patient_id": temp_id,
            "needs_staff_review": True,
            "message": "I've started your registration and our team will finish setting it up. "
                       "Let's go ahead and find you an appointment time.",
        }

    # Convert reuses the ExistingPatientResponse shape: permanent id is in patients[].
    cdata = (step2.get("data") or {}).get("data") or {}
    patients = cdata.get("patients") or []
    patient_id = patients[0].get("patientId") if patients else None

    logger.info(f"create_patient success: {first} {last} temp={temp_id} → patientId={patient_id}")
    return {
        "success": True,
        "patient_id": patient_id,
        "temporary_patient_id": temp_id,
        "needs_staff_review": False,
        "message": f"You're all set, {first} — I've created your record. Now let's find a time.",
    }


# ── Retell Custom Function: get available appointment slots ───────────────────

@register_action("get_availability")
async def get_availability(__context__: dict = None, __db__=None):
    """
    Fetches open appointment slots from Denticon and returns a short, numbered
    list the agent can read out. Each slot is returned in full so book_appointment
    can replay it without the agent handling provider/operatory IDs.

    Expected context["args"]:
        production_type_id (int, required) — the appointment type
        provider_id (int, optional)
        time_preference (str, optional: "AM"/"PM")
        days (list, optional)
        start_date (str, optional: yyyy-mm-dd)

    Returns:
        {"success": bool, "message": str, "slots": [ {index, appointmentDate,
         duration, providerId, operatoryId, productionTypeId}, ... ]}
    """
    ctx  = __context__ or {}
    args = ctx.get("args", {}) or {}
    db   = __db__ or SessionLocal()

    office, err = _resolve_office(ctx, db)
    if err:
        return {"success": False, "message": err, "slots": []}

    production_type_id = args.get("production_type_id") or args.get("productionTypeId")
    if not production_type_id:
        return {"success": False, "slots": [],
                "message": "What kind of appointment is this for? I need that to check times."}

    # Build query params; only include optional ones the agent provided.
    params = {
        "OfficeId": int(office["office_id"]),
        "ProductionTypeId": int(production_type_id),
    }
    if args.get("provider_id") or args.get("providerId"):
        params["ProviderId"] = int(args.get("provider_id") or args.get("providerId"))
    if args.get("time_preference") or args.get("timePreference"):
        params["TimePreference"] = args.get("time_preference") or args.get("timePreference")
    if args.get("start_date") or args.get("startDate"):
        params["StartDate"] = args.get("start_date") or args.get("startDate")
    if args.get("days"):
        params["Days"] = args.get("days")

    caller = APICaller(db)
    result = await caller.call(
        "denticon", "GET",
        "/denticon/appointments/v0/available-slots",
        params=params,
        triggered_by="retell:get_availability",
    )

    if not result.get("success"):
        logger.error(f"get_availability failed: {result.get('error')}")
        return {"success": False, "slots": [],
                "message": "I couldn't pull up open times just now. Let me get someone to help."}

    raw_slots = (result.get("data") or {}).get("data") or []
    if not raw_slots:
        return {"success": True, "slots": [],
                "message": "I don't see any open times matching that. Want me to try other days or another time of day?"}

    # Number the slots and keep the full payload for booking.
    slots = []
    for i, s in enumerate(raw_slots[:5], start=1):   # cap at 5 to keep the readout short
        slots.append({
            "index": i,
            "appointmentDate": s.get("appointmentDate"),
            "duration": s.get("duration"),
            "providerId": s.get("providerId"),
            "operatoryId": s.get("operatoryId"),
            "productionTypeId": s.get("productionTypeId"),
        })

    spoken = "; ".join(f"option {s['index']}: {s['appointmentDate']}" for s in slots)
    return {
        "success": True,
        "slots": slots,
        "message": f"I have these openings — {spoken}. Which works best?",
    }


# ── Retell Custom Function: book the appointment ──────────────────────────────

@register_action("book_appointment")
async def book_appointment(__context__: dict = None, __db__=None):
    """
    Books a slot returned by get_availability. The agent passes back the chosen
    slot's fields plus the patient identity.

    Expected context["args"]:
        Slot (from get_availability):
            appointment_date (str, required), duration (int, required),
            provider_id (int, required), operatory_id (int, required),
            production_type_id (int, required)
        Patient — ONE of:
            patient_id (int)  → existing patient
            OR first_name, last_name, birth_date (+ phone/email) for a new patient
        is_new_patient (bool), has_insurance (bool, default false)
        appointment_note (str, optional)

    Returns:
        {"success": bool, "message": str, "appointment_id": int|None}
    """
    ctx  = __context__ or {}
    args = ctx.get("args", {}) or {}
    db   = __db__ or SessionLocal()

    office, err = _resolve_office(ctx, db)
    if err:
        return {"success": False, "message": err, "appointment_id": None}

    # Slot fields — all required by the booking API.
    appt_date = args.get("appointment_date") or args.get("appointmentDate")
    duration  = args.get("duration")
    provider  = args.get("provider_id") or args.get("providerId")
    operatory = args.get("operatory_id") or args.get("operatoryId")
    prod_type = args.get("production_type_id") or args.get("productionTypeId")

    slot_missing = [k for k, v in {
        "appointment time": appt_date, "duration": duration,
        "provider": provider, "operatory": operatory, "appointment type": prod_type,
    }.items() if v in (None, "")]
    if slot_missing:
        return {"success": False, "appointment_id": None,
                "message": "I lost the details of that time slot — let me pull up the openings again."}

    patient_id = args.get("patient_id") or args.get("patientId")
    is_new = args.get("is_new_patient")
    if is_new is None:
        is_new = not bool(patient_id)
    has_insurance = bool(args.get("has_insurance") or args.get("hasInsurance") or False)

    body = {
        "officeId":         int(office["office_id"]),
        "providerId":       int(provider),
        "operatoryId":      int(operatory),
        "productionTypeId": int(prod_type),
        "appointmentDate":  appt_date,
        "duration":         int(duration),
        "isNewPatient":     bool(is_new),
        "hasInsurance":     has_insurance,
    }

    # Existing patient → patientId. New patient with no record yet → demographics.
    if patient_id:
        body["patientId"] = int(patient_id)
    else:
        if args.get("first_name") or args.get("firstName"):
            body["firstName"] = args.get("first_name") or args.get("firstName")
        if args.get("last_name") or args.get("lastName"):
            body["lastName"] = args.get("last_name") or args.get("lastName")
        if args.get("birth_date") or args.get("birthDate") or args.get("date_of_birth"):
            body["birthDate"] = (args.get("birth_date") or args.get("birthDate")
                                 or args.get("date_of_birth"))
        if args.get("phone"): body["phone"] = args.get("phone")
        if args.get("email"): body["email"] = args.get("email")

    if has_insurance and args.get("insurer_name"):
        body["insurerName"] = args.get("insurer_name")
    if args.get("appointment_note"):
        body["appointmentNote"] = args.get("appointment_note")
    if args.get("opt_in_voice") is not None:
        body["optInVoice"] = bool(args.get("opt_in_voice"))

    caller = APICaller(db)
    result = await caller.call(
        "denticon", "POST",
        "/denticon/appointments/v0/",
        body=body,
        triggered_by="retell:book_appointment",
    )

    if not result.get("success"):
        logger.error(f"book_appointment failed: {result.get('error')}")
        return {"success": False, "appointment_id": None,
                "message": "I'm sorry, that time didn't go through — it may have just been taken. "
                           "Let me check what else is open."}

    data = (result.get("data") or {}).get("data") or {}
    appt_id = data.get("appointmentId")
    logger.info(f"Appointment booked: id={appt_id} office={office['office_id']} at {appt_date}")
    return {
        "success": True,
        "appointment_id": appt_id,
        "message": f"You're booked for {appt_date}. We'll see you then!",
    }


# ── Retell AI post-call handlers ──────────────────────────────────────────────

@register_action("retell_call_ended")
async def retell_call_ended(__context__: dict = None, __db__=None):
    """
    Fired when Retell posts a call_ended event.
    Context includes: call_id, transcript, duration_seconds, from_number, to_number
    """
    ctx = __context__ or {}
    call_id    = ctx.get("call_id")
    transcript = ctx.get("transcript")
    duration   = ctx.get("duration_seconds")
    from_num   = ctx.get("from_number")

    logger.info(f"Call ended: {call_id} from {from_num} ({duration}s)")

    # Example: log to your CRM endpoint
    # db = __db__ or SessionLocal()
    # caller = APICaller(db)
    # await caller.call("your_crm", "POST", "/calls", body={
    #     "call_id": call_id, "transcript": transcript,
    #     "duration": duration, "phone": from_num,
    # }, triggered_by="retell:call_ended")

    return {"call_id": call_id, "processed": True}


@register_action("retell_call_analyzed")
async def retell_call_analyzed(__context__: dict = None, __db__=None):
    """
    Fired when Retell finishes AI analysis of a call.
    Context includes: call_analysis (sentiment, summary, custom_analysis_data)
    """
    ctx = __context__ or {}
    call_id  = ctx.get("call_id")
    analysis = ctx.get("call_analysis", {})

    logger.info(f"Call analyzed: {call_id} — sentiment={analysis.get('user_sentiment')}")

    # Example: update appointment status based on analysis outcome
    # if analysis.get("call_successful"):
    #     await update_appointment(call_id, status="confirmed")

    return {"call_id": call_id, "analysis_received": True}


@register_action("retell_call_started")
async def retell_call_started(__context__: dict = None, __db__=None):
    ctx = __context__ or {}
    logger.info(f"Call started: {ctx.get('call_id')} to {ctx.get('to_number')}")
    return {"call_id": ctx.get("call_id")}


# ── Form handlers ─────────────────────────────────────────────────────────────

@register_action("form_contact_us")
async def form_contact_us(__context__: dict = None, __db__=None):
    """Handle a contact form submission."""
    ctx    = __context__ or {}
    fields = ctx.get("fields", {})
    logger.info(f"Contact form from {fields.get('email')} — {fields.get('subject')}")
    # TODO: push to CRM, send notification, etc.
    return {"email": fields.get("email"), "queued": True}


# ── Scheduled / periodic jobs ─────────────────────────────────────────────────

@register_action("health_check_all")
async def health_check_all(__context__: dict = None, __db__=None):
    """Poll every active endpoint's base URL to check it's reachable."""
    from database import APIEndpoint, SessionLocal
    db = __db__ or SessionLocal()
    endpoints = db.query(APIEndpoint).filter(APIEndpoint.is_active == True).all()
    caller = APICaller(db)
    results = {}
    for e in endpoints:
        r = await caller.call(e.name, "GET", "/", triggered_by="health_check")
        results[e.name] = r.get("status_code")
        logger.info(f"Health {e.name}: {r.get('status_code')}")
    return results


@register_action("token_refresh_all")
async def token_refresh_all(__context__: dict = None, __db__=None):
    """Proactively refresh tokens for all bearer endpoints."""
    from database import APIEndpoint, SessionLocal
    from token_manager import TokenManager
    db = __db__ or SessionLocal()
    tm = TokenManager(db)
    endpoints = db.query(APIEndpoint).filter(
        APIEndpoint.is_active == True,
        APIEndpoint.auth_type.in_(["bearer", "oauth2"])
    ).all()
    refreshed = []
    for e in endpoints:
        token = await tm.get_token(e)
        if token:
            refreshed.append(e.name)
    logger.info(f"Tokens refreshed: {refreshed}")
    return {"refreshed": refreshed}
