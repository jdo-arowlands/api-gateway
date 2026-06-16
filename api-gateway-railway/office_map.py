"""
Office resolution
─────────────────
Maps an inbound phone number (the number a patient dialed) to a Denticon
office ID, using the office_phone_map table.

Today: one phone number per office, so the called number identifies the office.
Future: a call-center number could route to a zip-code lookup instead — callers
of resolve_office_id() won't need to change, they'll just receive an office_id
from a different source.
"""
import re
import logging
from sqlalchemy.orm import Session
from database import OfficePhoneMap

logger = logging.getLogger("office_map")


def normalize_phone(raw: str | None) -> str | None:
    """
    Normalize a phone number to E.164-ish '+<digits>' for consistent matching.
    Retell typically sends E.164 already (e.g. '+18135550100'); this guards
    against stray spaces, dashes, or parentheses.
    """
    if not raw:
        return None
    digits = re.sub(r"[^\d+]", "", raw.strip())
    if not digits:
        return None
    if digits.startswith("+"):
        return digits
    # Assume US if 10 digits, or 11 starting with 1.
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits


def resolve_office_id(db: Session, to_number: str | None) -> dict:
    """
    Resolve the office for a dialed number.

    Returns:
        {"found": bool, "office_id": str | None, "office_name": str | None,
         "phone": str | None, "error": str | None}
    """
    phone = normalize_phone(to_number)
    if not phone:
        return {"found": False, "office_id": None, "office_name": None,
                "phone": None, "error": "No called number provided"}

    row = (db.query(OfficePhoneMap)
             .filter(OfficePhoneMap.phone_number == phone,
                     OfficePhoneMap.is_active == True)
             .first())
    if not row:
        logger.warning(f"No office mapped for dialed number {phone}")
        return {"found": False, "office_id": None, "office_name": None,
                "phone": phone, "error": f"No office is mapped to {phone}"}

    return {"found": True, "office_id": row.office_id,
            "office_name": row.office_name, "phone": phone, "error": None}
