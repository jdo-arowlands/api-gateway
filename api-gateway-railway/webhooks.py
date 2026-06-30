"""
Webhook Receivers
─────────────────
Inbound HTTP endpoints that external systems post to in order to trigger jobs.

Supported sources:
  • Retell AI    POST /webhooks/retell
  • Web Form     POST /webhooks/form/{form_id}
  • Generic      POST /webhooks/trigger/{job_name}
  • Custom       POST /webhooks/custom/{source}

Each receiver:
  1. Validates the incoming payload (HMAC if secret configured)
  2. Normalises it into a context dict
  3. Calls scheduler_service.trigger_job()
  4. Logs the inbound event
"""
import hashlib
import hmac
import json
import logging
import os
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException, Header
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, JSON
from sqlalchemy.orm import Session
from database import Base, SessionLocal, engine

logger = logging.getLogger("webhooks")

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])

# Retell webhook signing secret — set via RETELL_WEBHOOK_SECRET env var.
# When configured, all Retell inbound requests must carry a valid
# X-Retell-Signature header or they are rejected with 401.
_RETELL_WEBHOOK_SECRET = os.getenv("RETELL_WEBHOOK_SECRET", "")


# ── Inbound event log ─────────────────────────────────────────────────────────

class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id           = Column(Integer, primary_key=True)
    source       = Column(String(100))       # retell | form | generic | custom
    source_id    = Column(String(200))       # form_id, job_name, etc.
    raw_payload  = Column(Text)
    parsed       = Column(JSON)
    job_triggered= Column(String(200))
    job_run_success = Column(Boolean)
    job_error    = Column(Text)
    ip_address   = Column(String(50))
    created_at   = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


def _log_event(db: Session, source: str, source_id: str,
               raw: str, parsed: dict, job: str,
               job_success: bool, job_error: str | None, ip: str):
    db.add(WebhookEvent(
        source=source, source_id=source_id,
        raw_payload=raw, parsed=parsed,
        job_triggered=job, job_run_success=job_success,
        job_error=job_error, ip_address=ip,
    ))
    db.commit()


def _verify_hmac(secret: str, body: bytes, sig_header: str | None) -> bool:
    if not secret:
        return True   # no secret configured → open
    if not sig_header:
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig_header.removeprefix("sha256="))


# ── Retell AI webhook ─────────────────────────────────────────────────────────

RETELL_EVENTS_TO_JOBS: dict[str, str] = {
    # Map Retell event types to job names — edit to match your setup
    "call_ended":     "retell_call_ended",
    "call_analyzed":  "retell_call_analyzed",
    "call_started":   "retell_call_started",
}


@router.post("/retell")
async def retell_webhook(
    request: Request,
    x_retell_signature: str | None = Header(None),
):
    """
    Retell AI posts call events here.
    Configure this URL in your Retell dashboard under Webhooks.
    """
    body = await request.body()

    # Verify HMAC signature when a webhook secret is configured
    if not _verify_hmac(_RETELL_WEBHOOK_SECRET, body, x_retell_signature):
        raise HTTPException(401, "Invalid signature")

    db = SessionLocal()
    try:
        try:
            payload = json.loads(body)
        except Exception:
            raise HTTPException(400, "Invalid JSON")

        event_type = payload.get("event") or payload.get("event_type", "unknown")
        call_id = payload.get("call", {}).get("call_id") or payload.get("call_id", "")

        # Build normalised context
        context = {
            "source": "retell",
            "event_type": event_type,
            "call_id": call_id,
            "agent_id": payload.get("agent_id") or payload.get("call", {}).get("agent_id"),
            "from_number": payload.get("call", {}).get("from_number"),
            "to_number": payload.get("call", {}).get("to_number"),
            "duration_seconds": payload.get("call", {}).get("end_timestamp", 0) -
                                 payload.get("call", {}).get("start_timestamp", 0)
                                 if payload.get("call", {}).get("end_timestamp") else None,
            "transcript": payload.get("call", {}).get("transcript"),
            "call_analysis": payload.get("call", {}).get("call_analysis"),
            "raw": payload,
        }

        job_name = RETELL_EVENTS_TO_JOBS.get(event_type)
        job_success = True
        job_error = None

        if job_name:
            from scheduler import scheduler_service
            result = await scheduler_service.trigger_job(
                job_name, context=context, triggered_by=f"retell:{event_type}"
            )
            job_success = result.get("success", False)
            job_error = result.get("error")
        else:
            logger.info(f"Retell event '{event_type}' has no mapped job – logged only")

        _log_event(db, "retell", event_type, body.decode(), context,
                   job_name or "", job_success, job_error,
                   request.client.host if request.client else "")

        return {"received": True, "event": event_type, "job": job_name}

    finally:
        db.close()


# ── Retell AI Custom Function (synchronous, mid-conversation) ──────────────────
# Point your Retell Custom Function's URL here. The agent calls this during the
# chat, we run the matching action, and return a message the agent reads back.
#
# Map each Retell function name to a registered action in actions.py:
RETELL_FUNCTIONS_TO_ACTIONS: dict[str, str] = {
    "find_patient": "find_patient",
    "create_patient": "create_patient",
    "get_availability": "get_availability",
    "book_appointment": "book_appointment",
}


@router.post("/retell/function")
async def retell_function(
    request: Request,
    x_retell_signature: str | None = Header(None),
):
    """
    Receives a Retell Custom Function call mid-conversation.

    Retell posts a JSON body that includes the function name and the arguments
    the agent collected. We run the mapped action and return:
        {"response": "<message for the agent to say>"}
    within Retell's ~seconds-long timeout.
    """
    body = await request.body()

    # Verify HMAC signature when a webhook secret is configured
    if not _verify_hmac(_RETELL_WEBHOOK_SECRET, body, x_retell_signature):
        raise HTTPException(401, "Invalid signature")

    db = SessionLocal()
    try:
        try:
            payload = json.loads(body)
        except Exception:
            raise HTTPException(400, "Invalid JSON")

        # Retell sends the invoked function name and its arguments. Field names
        # have varied across Retell versions, so check the common spots.
        fn_name = (payload.get("name")
                   or payload.get("function_name")
                   or (payload.get("function") or {}).get("name"))
        args = (payload.get("args")
                or payload.get("arguments")
                or payload.get("parameters")
                or {})
        call = payload.get("call", {}) or {}
        call_id = call.get("call_id") or payload.get("call_id")

        action_name = RETELL_FUNCTIONS_TO_ACTIONS.get(fn_name or "")
        if not action_name:
            logger.warning(f"Retell function '{fn_name}' not mapped")
            _log_event(db, "retell_function", fn_name or "unknown", body.decode(),
                       {"args": args}, "", False, "unmapped function",
                       request.client.host if request.client else "")
            # Still 200 so the agent gets a graceful message, not a silent failure.
            return {"response": "That action isn't available right now."}

        # Run the action directly (synchronous request/response).
        from scheduler import JOB_REGISTRY
        import asyncio
        fn = JOB_REGISTRY.get(action_name)
        context = {"source": "retell_function", "function": fn_name,
                   "call_id": call_id, "args": args, "raw": payload}

        result = {}
        success = False
        error = None
        try:
            kwargs = {"__context__": context, "__db__": db}
            usable = {k: v for k, v in kwargs.items() if k in fn.__code__.co_varnames}
            result = await fn(**usable) if asyncio.iscoroutinefunction(fn) else fn(**usable)
            success = bool(result.get("success")) if isinstance(result, dict) else True
        except Exception as exc:
            error = str(exc)
            logger.exception(f"Retell function '{fn_name}' errored: {exc}")

        _log_event(db, "retell_function", fn_name, body.decode(),
                   {"args": args, "result": result}, action_name,
                   success, error, request.client.host if request.client else "")

        # The agent reads back result["message"]; fall back to a generic line.
        message = (result.get("message") if isinstance(result, dict) else None) \
                  or ("Done." if success else "Sorry, something went wrong.")
        return {"response": message}

    finally:
        db.close()


# ── Web form webhook ──────────────────────────────────────────────────────────

@router.post("/form/{form_id}")
async def form_webhook(
    form_id: str,
    request: Request,
):
    """
    Generic form submission receiver.
    POST JSON or form-urlencoded from any web form.
    Map form_id → job_name in FORM_JOB_MAP below.
    """
    FORM_JOB_MAP: dict[str, str] = {
        # "contact-us":   "crm_new_lead",
        # "callback-req": "schedule_callback",
    }

    body = await request.body()
    content_type = request.headers.get("content-type", "")
    db = SessionLocal()
    try:
        if "application/json" in content_type:
            try:
                payload = json.loads(body)
            except Exception:
                raise HTTPException(400, "Invalid JSON")
        elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            form = await request.form()
            payload = dict(form)
        else:
            payload = {"raw": body.decode()}

        context = {
            "source": "form",
            "form_id": form_id,
            "submitted_at": datetime.utcnow().isoformat(),
            "fields": payload,
            "ip": request.client.host if request.client else None,
        }

        job_name = FORM_JOB_MAP.get(form_id, f"form_{form_id}")
        from scheduler import scheduler_service
        result = await scheduler_service.trigger_job(
            job_name, context=context, triggered_by=f"form:{form_id}"
        )

        _log_event(db, "form", form_id, body.decode(), context,
                   job_name, result.get("success", False), result.get("error"),
                   request.client.host if request.client else "")

        return {"received": True, "form_id": form_id, "job": job_name}

    finally:
        db.close()


# ── Generic job trigger ───────────────────────────────────────────────────────

@router.post("/trigger/{job_name}")
async def trigger_job_webhook(
    job_name: str,
    request: Request,
    x_webhook_secret: str | None = Header(None),
):
    """
    Fire any job by name.  Accepts an optional JSON body as context.
    Protect with X-Webhook-Secret header or leave open for internal use.
    """
    body = await request.body()
    db = SessionLocal()
    try:
        context = {}
        if body:
            try:
                context = json.loads(body)
            except Exception:
                context = {"raw": body.decode()}

        context["source"] = "generic_trigger"
        context["triggered_at"] = datetime.utcnow().isoformat()

        from scheduler import scheduler_service
        result = await scheduler_service.trigger_job(
            job_name, context=context, triggered_by="webhook"
        )

        _log_event(db, "generic", job_name, body.decode(), context,
                   job_name, result.get("success", False), result.get("error"),
                   request.client.host if request.client else "")

        if not result.get("success") and "not found" in (result.get("error") or ""):
            raise HTTPException(404, result["error"])

        return result

    finally:
        db.close()


# ── Custom source webhook ─────────────────────────────────────────────────────

@router.post("/custom/{source}")
async def custom_webhook(source: str, request: Request):
    """
    Extensible catch-all. Route source → job in CUSTOM_SOURCE_MAP.
    Each entry can also specify a payload transformer function.
    """
    CUSTOM_SOURCE_MAP: dict[str, str] = {
        # "zapier":     "zapier_trigger_job",
        # "n8n":        "n8n_trigger_job",
        # "twilio-sms": "sms_inbound_handler",
    }

    body = await request.body()
    db = SessionLocal()
    try:
        try:
            payload = json.loads(body)
        except Exception:
            payload = {"raw": body.decode()}

        context = {
            "source": source,
            "payload": payload,
            "received_at": datetime.utcnow().isoformat(),
        }

        job_name = CUSTOM_SOURCE_MAP.get(source, f"custom_{source}")
        from scheduler import scheduler_service
        result = await scheduler_service.trigger_job(
            job_name, context=context, triggered_by=f"custom:{source}"
        )

        _log_event(db, "custom", source, body.decode(), context,
                   job_name, result.get("success", False), result.get("error"),
                   request.client.host if request.client else "")

        return result

    finally:
        db.close()
