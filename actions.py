"""
Custom Job Actions — add your own here
Register with @register_action("action_name")
"""
from scheduler import register_action
from database import SessionLocal
from api_caller import APICaller
import logging

logger = logging.getLogger("actions")


# ── Retell AI handlers ────────────────────────────────────────────────────────

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
