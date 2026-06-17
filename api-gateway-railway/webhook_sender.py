"""
webhook_sender.py  — ADD THIS TO api-gateway-railway

Sends webhook events to ins-verify-api when Denticon data changes.
Drop this file into your gateway project and call fire_webhook()
from wherever appointment/patient changes are detected.

Usage:
    from webhook_sender import fire_webhook
    await fire_webhook("appointment.created", office_id="126", data={...})
"""

import httpx
import os
from datetime import datetime, timezone
import structlog

log = structlog.get_logger()

INS_VERIFY_WEBHOOK_URL = os.environ.get(
    "INS_VERIFY_WEBHOOK_URL",
    "https://ins-verify-api.railway.app/webhooks/denticon"
)
GATEWAY_API_KEY = os.environ.get("GATEWAY_API_KEY", "")


async def fire_webhook(event: str, office_id: str, data: dict) -> bool:
    """
    Fire a webhook event to ins-verify-api.
    Non-blocking — failures are logged but don't raise.

    Events:
        appointment.created
        appointment.updated
        appointment.cancelled
        patient.insurance_updated
    """
    payload = {
        "event": event,
        "officeId": office_id,
        "data": data,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                INS_VERIFY_WEBHOOK_URL,
                json=payload,
                headers={
                    "X-Webhook-Secret": GATEWAY_API_KEY,
                    "Content-Type": "application/json",
                },
            )
            resp.raise_for_status()
            log.info("webhook.sent", event=event, office_id=office_id)
            return True
    except Exception as e:
        # Never let webhook failures break the main flow
        log.warning("webhook.failed", event=event, error=str(e))
        return False


# ── Hook these into your existing gateway action handlers ─────────────────────
#
# In your actions.py, after a successful Denticon appointment call, add:
#
#   from webhook_sender import fire_webhook
#
#   # After book_appointment succeeds:
#   await fire_webhook("appointment.created", office_id=office_id, data=result)
#
#   # After any patient insurance update:
#   await fire_webhook("patient.insurance_updated", office_id=office_id, data={"PatientId": patient_id})
