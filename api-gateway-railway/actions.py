"""
Custom Job Actions — add your own here
Register with @register_action("action_name")
"""
from scheduler import register_action
from database import SessionLocal
from api_caller import APICaller
import logging

logger = logging.getLogger("actions")


# ── Retell Custom Function: add a patient ─────────────────────────────────────

@register_action("add_patient")
async def add_patient(__context__: dict = None, __db__=None):
    """
    Called by a Retell Custom Function mid-conversation to create a patient
    in Denticon (or whichever endpoint you name below), then returns a short
    message the agent can read back to the caller.

    Expected context["args"] (collected by the Retell agent):
        first_name, last_name, date_of_birth, phone, email  (adjust to your needs)

    Returns:
        {"success": bool, "message": str, "patient_id": str | None}
        — 'message' is what the agent says back to the patient.
    """
    ctx  = __context__ or {}
    args = ctx.get("args", {}) or {}

    first = args.get("first_name") or args.get("firstName")
    last  = args.get("last_name")  or args.get("lastName")
    dob   = args.get("date_of_birth") or args.get("dob")
    phone = args.get("phone")
    email = args.get("email")

    # Minimal validation so we don't push junk into the PMS.
    missing = [f for f, v in {"first_name": first, "last_name": last, "phone": phone}.items() if not v]
    if missing:
        return {
            "success": False,
            "message": f"I still need the patient's {', '.join(missing)} before I can create the record.",
            "patient_id": None,
        }

    db = __db__ or SessionLocal()
    caller = APICaller(db)

    # ─────────────────────────────────────────────────────────────────────────
    # TODO: Replace the endpoint name, path, and body with your real Denticon
    # (Planet DDS) create-patient contract. The endpoint "denticon" must already
    # exist on the Endpoints page with its bearer credentials configured.
    #
    # Check the Denticon API docs for the exact path and required fields
    # (officeId, etc.). The structure below is a placeholder.
    # ─────────────────────────────────────────────────────────────────────────
    result = await caller.call(
        "denticon",                      # ← endpoint name as configured in the dashboard
        "POST",
        "/patients",                     # ← real Denticon create-patient path
        body={
            "firstName": first,
            "lastName":  last,
            "dateOfBirth": dob,
            "phone": phone,
            "email": email,
            # "officeId": "...",        # ← Denticon often requires this; pull from settings
        },
        triggered_by="retell:add_patient",
    )

    if result.get("success"):
        # Try to surface the new patient ID if the API returns one.
        data = result.get("data") or {}
        patient_id = (data.get("patientId") or data.get("id")
                      if isinstance(data, dict) else None)
        logger.info(f"Patient created via Retell: {first} {last} (id={patient_id})")
        return {
            "success": True,
            "message": f"Great news — I've created a record for {first} {last}. You're all set.",
            "patient_id": patient_id,
        }

    # Failed — keep the spoken message friendly, log the real error.
    logger.error(f"add_patient failed: {result.get('error')}")
    return {
        "success": False,
        "message": "I'm sorry, I wasn't able to create the record just now. "
                   "Our team has been notified and will follow up.",
        "patient_id": None,
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
