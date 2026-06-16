"""
Admin Tools
───────────
A small registry of operational actions surfaced in the dashboard's
"Admin Tools" page as clickable cards. Each tool declares:
  • key          unique id
  • label        button/card title
  • description   one line of help
  • category      groups cards in the UI
  • params        optional inputs the UI renders as fields
  • handler       async fn(params: dict, db) -> dict result

To add a new tool, append a TOOLS entry and write its handler. The UI and the
run endpoint pick it up automatically — no per-tool wiring.
"""
import logging
from typing import Callable

logger = logging.getLogger("admin_tools")


class Tool:
    def __init__(self, key: str, label: str, description: str,
                 category: str, handler: Callable, params: list | None = None,
                 confirm: bool = False):
        self.key = key
        self.label = label
        self.description = description
        self.category = category
        self.handler = handler
        self.params = params or []     # [{name, label, type, required, placeholder, help}]
        self.confirm = confirm         # ask "are you sure?" before running

    def to_dict(self) -> dict:
        return {
            "key": self.key, "label": self.label, "description": self.description,
            "category": self.category, "params": self.params, "confirm": self.confirm,
        }


# ── Tool handlers ─────────────────────────────────────────────────────────────

async def _refresh_reference(params: dict, db):
    from actions import refresh_practice_reference
    office_id = (params.get("office_id") or "").strip() or None
    result = await refresh_practice_reference(office_id=office_id, __db__=db)
    offices = result.get("offices", {})
    if not offices:
        return {"ok": True, "summary": "No offices to refresh — add an office mapping first."}

    lines = []
    for oid, info in offices.items():
        counts = info.get("counts", {})
        errors = info.get("errors", {})
        if errors:
            for kind, msg in errors.items():
                lines.append(f"Office {oid} — {kind}: FAILED — {msg}")
        else:
            lines.append(f"Office {oid}: {counts.get('production_type',0)} types, "
                         f"{counts.get('provider',0)} providers, "
                         f"{counts.get('operatory',0)} operatories")
    ok = result.get("success", True)
    summary = "Reference data refreshed." if ok else "Refresh hit errors — see details."
    return {"ok": ok, "summary": summary, "detail": lines}


async def _refresh_tokens(params: dict, db):
    from token_manager import TokenManager
    from database import APIEndpoint
    tm = TokenManager(db)
    eps = (db.query(APIEndpoint)
             .filter(APIEndpoint.is_active == True,
                     APIEndpoint.auth_type.in_(["bearer", "oauth2"]))
             .all())
    refreshed, failed = [], []
    for e in eps:
        try:
            tok = await tm.get_token(e)
            (refreshed if tok else failed).append(e.name)
        except Exception as exc:
            logger.error(f"token refresh failed for {e.name}: {exc}")
            failed.append(e.name)
    return {"ok": not failed, "summary": f"Refreshed {len(refreshed)} token(s).",
            "detail": ([f"OK: {n}" for n in refreshed] + [f"FAILED: {n}" for n in failed])}


async def _resolve_office(params: dict, db):
    from office_map import resolve_office_id
    r = resolve_office_id(db, params.get("to_number"))
    if r["found"]:
        return {"ok": True, "summary": f"{r['phone']} → office {r['office_id']}"
                + (f" ({r['office_name']})" if r['office_name'] else "")}
    return {"ok": False, "summary": r["error"] or "No match"}


async def _test_endpoint_token(params: dict, db):
    from database import APIEndpoint
    from token_manager import TokenManager
    name = (params.get("endpoint_name") or "").strip()
    e = db.query(APIEndpoint).filter(APIEndpoint.name == name).first()
    if not e:
        return {"ok": False, "summary": f"No endpoint named '{name}'"}
    tm = TokenManager(db)
    tok = await tm.get_token(e)
    return {"ok": bool(tok),
            "summary": f"{name}: token {'OK' if tok else 'could not be obtained'}"}


async def _list_offices(params: dict, db):
    from api_caller import APICaller
    caller = APICaller(db)
    r = await caller.call("denticon", "GET",
                          "/denticon/practices/v0/offices",
                          params={"PageSize": 1000},
                          triggered_by="admin:list_offices")
    if not r.get("success"):
        return {"ok": False,
                "summary": f"Couldn't list offices (status {r.get('status_code')}): {r.get('error')}"}
    offices = (r.get("data") or {}).get("data") or []
    if not offices:
        return {"ok": True, "summary": "Authenticated OK, but no offices returned."}
    lines = [f"{o.get('officeId')}: {o.get('officeName')}"
             + (f" — {o.get('city')}, {o.get('state')}" if o.get('city') else "")
             for o in offices]
    return {"ok": True, "summary": f"Found {len(offices)} office(s).", "detail": lines}


# ── Registry ──────────────────────────────────────────────────────────────────

TOOLS: list[Tool] = [
    Tool(
        key="refresh_reference",
        label="Refresh Denticon Reference",
        description="Pull production types, providers, and operatories from Denticon "
                    "into the local cache so appointment types resolve by name.",
        category="Denticon",
        params=[{"name": "office_id", "label": "Office ID (blank = all mapped offices)",
                 "type": "text", "required": False, "placeholder": "e.g. 101"}],
        handler=_refresh_reference,
    ),
    Tool(
        key="list_offices",
        label="List Denticon Offices",
        description="Fetch every office from Denticon with its real ID and name — "
                    "use this to confirm the Office IDs to put in the Office Map.",
        category="Denticon",
        handler=_list_offices,
    ),
    Tool(
        key="refresh_tokens",
        label="Refresh All Tokens",
        description="Proactively fetch fresh bearer tokens for every active endpoint.",
        category="Auth",
        handler=_refresh_tokens,
    ),
    Tool(
        key="test_endpoint_token",
        label="Test Endpoint Credentials",
        description="Verify an endpoint can obtain a token with its current credentials.",
        category="Auth",
        params=[{"name": "endpoint_name", "label": "Endpoint name",
                 "type": "text", "required": True, "placeholder": "denticon"}],
        handler=_test_endpoint_token,
    ),
    Tool(
        key="resolve_office",
        label="Resolve Office by Phone",
        description="Check which Denticon office a dialed number maps to.",
        category="Diagnostics",
        params=[{"name": "to_number", "label": "Dialed number",
                 "type": "text", "required": True, "placeholder": "+18135550100"}],
        handler=_resolve_office,
    ),
]

TOOLS_BY_KEY = {t.key: t for t in TOOLS}


def list_tools() -> list[dict]:
    return [t.to_dict() for t in TOOLS]


async def run_tool(key: str, params: dict, db) -> dict:
    tool = TOOLS_BY_KEY.get(key)
    if not tool:
        return {"ok": False, "summary": f"Unknown tool '{key}'"}
    try:
        return await tool.handler(params or {}, db)
    except Exception as exc:
        logger.exception(f"tool '{key}' failed")
        return {"ok": False, "summary": f"Error: {exc}"}
