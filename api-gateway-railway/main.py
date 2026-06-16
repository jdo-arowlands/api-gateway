"""
API Gateway – Main Application
"""
import os
import secrets
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from typing import Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from database import get_db, APIEndpoint, APICallLog, AppSetting, SessionLocal, Project, OfficePhoneMap
import actions  # noqa — registers all @register_action decorators on startup
from api_caller import APICaller
from scheduler import scheduler_service, JobDefinition, JobRunLog, JOB_REGISTRY, register_action
from webhooks import router as webhook_router, WebhookEvent

# ── Dashboard login ───────────────────────────────────────────────────────────
# Single shared admin login. Set these in Railway → Variables.
_DASH_USER   = os.getenv("DASHBOARD_USER", "admin")
_DASH_PASS   = os.getenv("DASHBOARD_PASSWORD", "")
_SECRET_KEY  = os.getenv("SECRET_KEY", secrets.token_hex(32))
# If no password is configured the dashboard is left open (local dev convenience).
_AUTH_ENABLED = bool(_DASH_PASS)

# Paths that never require a login: the login flow itself, health check,
# and inbound webhooks (called by Retell / forms with their own secrets).
_PUBLIC_PREFIXES = ("/login", "/logout", "/health", "/webhooks", "/static")

def _is_public(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in _PUBLIC_PREFIXES)

logging.basicConfig(level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("main")


# ── Register built-in job actions ─────────────────────────────────────────────

@register_action("api_call")
async def action_api_call(endpoint_name: str, method: str, path: str,
                          body: dict = None, __context__: dict = None, __db__: Session = None):
    """Generic action: make an authenticated API call."""
    db = __db__ or SessionLocal()
    caller = APICaller(db)
    return await caller.call(
        endpoint_name, method, path,
        body=body,
        triggered_by=(__context__ or {}).get("source", "scheduler"),
    )


@register_action("http_get")
async def action_http_get(endpoint_name: str, path: str,
                          __context__: dict = None, __db__: Session = None):
    db = __db__ or SessionLocal()
    caller = APICaller(db)
    return await caller.call(endpoint_name, "GET", path,
                             triggered_by=(__context__ or {}).get("source", "scheduler"))


@register_action("log_retell_call")
async def action_log_retell_call(__context__: dict = None, __db__: Session = None):
    """Log a Retell AI call event — extend with your CRM write logic."""
    ctx = __context__ or {}
    logger.info(f"Retell call logged: {ctx.get('call_id')} event={ctx.get('event_type')}")
    # TODO: push to your CRM, update appointment status, etc.
    return {"call_id": ctx.get("call_id"), "event": ctx.get("event_type")}


# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler_service.start()
    logger.info("API Gateway started")
    yield
    scheduler_service.stop()
    logger.info("API Gateway stopped")


app = FastAPI(title="API Gateway", version="1.0.0", lifespan=lifespan)

app.add_middleware(CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """Block every non-public path unless the session is logged in."""
    if not _AUTH_ENABLED or _is_public(request.url.path):
        return await call_next(request)

    if request.session.get("authed"):
        return await call_next(request)

    # Not logged in. API calls get a clean 401; browser navigations get redirected.
    if request.url.path.startswith("/api"):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return RedirectResponse(url="/login", status_code=302)


# Added AFTER auth_gate so it becomes the OUTER middleware and runs first,
# populating request.session before auth_gate reads it.
app.add_middleware(
    SessionMiddleware,
    secret_key=_SECRET_KEY,
    session_cookie="gw_session",
    max_age=14 * 24 * 3600,
    same_site="lax",
    https_only=False,   # Railway terminates TLS at the edge; cookie still travels over HTTPS
)


app.include_router(webhook_router)


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — grouped by resource
# ═══════════════════════════════════════════════════════════════════════════════

# ── Projects ──────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    color: Optional[str] = "#2f81f7"


@app.get("/api/projects")
def list_projects(db: Session = Depends(get_db)):
    rows = db.query(Project).order_by(Project.name).all()
    return [_project_out(p, db) for p in rows]


@app.post("/api/projects", status_code=201)
def create_project(data: ProjectCreate, db: Session = Depends(get_db)):
    if db.query(Project).filter(Project.name == data.name).first():
        raise HTTPException(400, "A project with that name already exists")
    p = Project(**data.model_dump())
    db.add(p); db.commit(); db.refresh(p)
    return _project_out(p, db)


@app.patch("/api/projects/{pid}")
def update_project(pid: int, data: dict, db: Session = Depends(get_db)):
    p = db.query(Project).filter(Project.id == pid).first()
    if not p: raise HTTPException(404, "Not found")
    for k, v in data.items():
        if hasattr(p, k) and k not in ("id", "created_at"):
            setattr(p, k, v)
    db.commit(); db.refresh(p)
    return _project_out(p, db)


@app.delete("/api/projects/{pid}", status_code=204)
def delete_project(pid: int, db: Session = Depends(get_db)):
    p = db.query(Project).filter(Project.id == pid).first()
    if not p: raise HTTPException(404, "Not found")
    # Unassign endpoints rather than deleting them
    db.query(APIEndpoint).filter(APIEndpoint.project_id == pid).update(
        {APIEndpoint.project_id: None})
    db.delete(p); db.commit()


def _project_out(p: Project, db: Session) -> dict:
    count = db.query(APIEndpoint).filter(APIEndpoint.project_id == p.id).count()
    return {
        "id": p.id, "name": p.name, "description": p.description,
        "color": p.color, "endpoint_count": count,
        "created_at": p.created_at,
    }


# ── Office phone map ──────────────────────────────────────────────────────────

class OfficeMapCreate(BaseModel):
    phone_number: str
    office_id: str
    office_name: Optional[str] = None
    project_id: Optional[int] = None
    is_active: Optional[bool] = True


@app.get("/api/office-map")
def list_office_map(db: Session = Depends(get_db)):
    rows = db.query(OfficePhoneMap).order_by(OfficePhoneMap.office_name).all()
    return [_office_out(o) for o in rows]


@app.post("/api/office-map", status_code=201)
def create_office_map(data: OfficeMapCreate, db: Session = Depends(get_db)):
    from office_map import normalize_phone
    phone = normalize_phone(data.phone_number)
    if not phone:
        raise HTTPException(400, "Invalid phone number")
    if db.query(OfficePhoneMap).filter(OfficePhoneMap.phone_number == phone).first():
        raise HTTPException(400, f"{phone} is already mapped")
    payload = data.model_dump()
    payload["phone_number"] = phone
    o = OfficePhoneMap(**payload)
    db.add(o); db.commit(); db.refresh(o)
    return _office_out(o)


@app.patch("/api/office-map/{oid}")
def update_office_map(oid: int, data: dict, db: Session = Depends(get_db)):
    o = db.query(OfficePhoneMap).filter(OfficePhoneMap.id == oid).first()
    if not o: raise HTTPException(404, "Not found")
    from office_map import normalize_phone
    for k, v in data.items():
        if k in ("id", "created_at"):
            continue
        if k == "phone_number":
            v = normalize_phone(v)
            if not v:
                raise HTTPException(400, "Invalid phone number")
            # guard against collision with another row
            clash = (db.query(OfficePhoneMap)
                       .filter(OfficePhoneMap.phone_number == v,
                               OfficePhoneMap.id != oid).first())
            if clash:
                raise HTTPException(400, f"{v} is already mapped")
        if hasattr(o, k):
            setattr(o, k, v)
    db.commit(); db.refresh(o)
    return _office_out(o)


@app.delete("/api/office-map/{oid}", status_code=204)
def delete_office_map(oid: int, db: Session = Depends(get_db)):
    o = db.query(OfficePhoneMap).filter(OfficePhoneMap.id == oid).first()
    if not o: raise HTTPException(404, "Not found")
    db.delete(o); db.commit()


@app.get("/api/office-map/resolve")
def resolve_office(to_number: str, db: Session = Depends(get_db)):
    """Test helper: see which office a dialed number maps to."""
    from office_map import resolve_office_id
    return resolve_office_id(db, to_number)


def _office_out(o: OfficePhoneMap) -> dict:
    return {
        "id": o.id, "phone_number": o.phone_number,
        "office_id": o.office_id, "office_name": o.office_name,
        "project_id": o.project_id, "is_active": o.is_active,
        "created_at": o.created_at,
    }


# ── Endpoints (API connections) ───────────────────────────────────────────────

class EndpointCreate(BaseModel):
    name: str
    base_url: str
    auth_type: str = "bearer"
    project_id: Optional[int] = None
    token_url: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    token_scope: Optional[str] = None
    api_key: Optional[str] = None
    api_key_header: Optional[str] = "X-API-Key"
    extra_headers: Optional[dict] = {}
    default_timeout: Optional[int] = 30


@app.get("/api/endpoints")
def list_endpoints(db: Session = Depends(get_db)):
    rows = db.query(APIEndpoint).all()
    return [_endpoint_out(e) for e in rows]


@app.post("/api/endpoints", status_code=201)
def create_endpoint(data: EndpointCreate, db: Session = Depends(get_db)):
    e = APIEndpoint(**data.model_dump())
    db.add(e); db.commit(); db.refresh(e)
    return _endpoint_out(e)


@app.get("/api/endpoints/{eid}")
def get_endpoint(eid: int, db: Session = Depends(get_db)):
    e = db.query(APIEndpoint).filter(APIEndpoint.id == eid).first()
    if not e: raise HTTPException(404, "Not found")
    return _endpoint_out(e)


@app.patch("/api/endpoints/{eid}")
def update_endpoint(eid: int, data: dict, db: Session = Depends(get_db)):
    e = db.query(APIEndpoint).filter(APIEndpoint.id == eid).first()
    if not e: raise HTTPException(404, "Not found")

    # Never let these be set directly from the client.
    protected = {"id", "current_token", "token_expires_at", "created_at"}
    # Changing any of these invalidates the cached token.
    auth_fields = {"auth_type", "token_url", "client_id", "client_secret",
                   "token_scope", "api_key", "api_key_header", "base_url"}
    auth_changed = False

    for k, v in data.items():
        if k in protected:
            continue
        if hasattr(e, k):
            if k in auth_fields and getattr(e, k) != v:
                auth_changed = True
            setattr(e, k, v)

    # Force a fresh token on next call if credentials/auth changed.
    if auth_changed:
        e.current_token = None
        e.token_expires_at = None

    db.commit(); db.refresh(e)
    return _endpoint_out(e)


@app.delete("/api/endpoints/{eid}", status_code=204)
def delete_endpoint(eid: int, db: Session = Depends(get_db)):
    e = db.query(APIEndpoint).filter(APIEndpoint.id == eid).first()
    if not e: raise HTTPException(404, "Not found")
    db.delete(e); db.commit()


@app.post("/api/endpoints/{eid}/test")
async def test_endpoint(eid: int, db: Session = Depends(get_db)):
    """Manually fire a token refresh to verify credentials."""
    e = db.query(APIEndpoint).filter(APIEndpoint.id == eid).first()
    if not e: raise HTTPException(404, "Not found")
    from token_manager import TokenManager
    tm = TokenManager(db)
    token = await tm.get_token(e)
    return {"success": bool(token), "token_preview": (token or "")[:20] + "..." if token else None,
            "expires_at": e.token_expires_at}


def _endpoint_out(e: APIEndpoint) -> dict:
    return {
        "id": e.id, "name": e.name, "base_url": e.base_url,
        "auth_type": e.auth_type, "token_url": e.token_url,
        "client_id": e.client_id,
        "api_key_header": e.api_key_header,
        "extra_headers": e.extra_headers,
        "default_timeout": e.default_timeout,
        "is_active": e.is_active,
        "project_id": e.project_id,
        "project_name": e.project.name if e.project else None,
        "project_color": e.project.color if e.project else None,
        "token_expires_at": e.token_expires_at,
        "created_at": e.created_at,
        "updated_at": e.updated_at,
    }


# ── Call Logs ─────────────────────────────────────────────────────────────────

@app.get("/api/logs")
def list_logs(
    endpoint_name: Optional[str] = None,
    success: Optional[bool] = None,
    limit: int = Query(50, le=500),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(APICallLog).order_by(APICallLog.created_at.desc())
    if endpoint_name: q = q.filter(APICallLog.endpoint_name == endpoint_name)
    if success is not None: q = q.filter(APICallLog.success == success)
    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    return {"total": total, "items": [_log_out(r) for r in rows]}


@app.get("/api/logs/{log_id}")
def get_log(log_id: int, db: Session = Depends(get_db)):
    r = db.query(APICallLog).filter(APICallLog.id == log_id).first()
    if not r: raise HTTPException(404, "Not found")
    return _log_out(r, full=True)


def _log_out(r: APICallLog, full=False) -> dict:
    d = {
        "id": r.id, "endpoint_name": r.endpoint_name,
        "method": r.method, "url": r.url,
        "status_code": r.status_code,
        "success": r.success, "response_time_ms": r.response_time_ms,
        "triggered_by": r.triggered_by, "token_refreshed": r.token_refreshed,
        "created_at": r.created_at, "error_message": r.error_message,
    }
    if full:
        d.update({
            "request_headers": r.request_headers,
            "request_body": r.request_body,
            "response_headers": r.response_headers,
            "response_body": r.response_body,
        })
    return d


# ── Stats / dashboard ─────────────────────────────────────────────────────────

@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db)):
    from sqlalchemy import func
    total_calls = db.query(APICallLog).count()
    success_calls = db.query(APICallLog).filter(APICallLog.success == True).count()
    fail_calls = total_calls - success_calls
    avg_rt = db.query(func.avg(APICallLog.response_time_ms)).scalar() or 0
    total_jobs = db.query(JobDefinition).count()
    active_jobs = db.query(JobDefinition).filter(JobDefinition.is_active == True).count()
    recent_runs = db.query(JobRunLog).order_by(JobRunLog.started_at.desc()).limit(5).all()
    webhook_count = db.query(WebhookEvent).count()
    return {
        "calls": {"total": total_calls, "success": success_calls, "failed": fail_calls,
                  "avg_response_ms": round(float(avg_rt), 1)},
        "jobs": {"total": total_jobs, "active": active_jobs},
        "webhooks": {"total": webhook_count},
        "recent_job_runs": [
            {"job_name": r.job_name, "success": r.success,
             "triggered_by": r.triggered_by, "started_at": r.started_at}
            for r in recent_runs
        ],
    }


# ── Jobs ──────────────────────────────────────────────────────────────────────

class JobCreate(BaseModel):
    name: str
    description: Optional[str] = None
    job_type: str   # cron | interval | onetime | webhook
    schedule: Optional[str] = None
    run_at: Optional[datetime] = None
    action: str
    action_params: Optional[dict] = {}
    webhook_secret: Optional[str] = None


@app.get("/api/jobs")
def list_jobs(db: Session = Depends(get_db)):
    jobs = db.query(JobDefinition).order_by(JobDefinition.created_at.desc()).all()
    return [_job_out(j) for j in jobs]


@app.post("/api/jobs", status_code=201)
def create_job(data: JobCreate, db: Session = Depends(get_db)):
    j = scheduler_service.add_job(db, data.model_dump())
    return _job_out(j)


@app.patch("/api/jobs/{jid}")
def update_job(jid: int, data: dict, db: Session = Depends(get_db)):
    j = scheduler_service.update_job(db, jid, data)
    if not j: raise HTTPException(404, "Not found")
    return _job_out(j)


@app.delete("/api/jobs/{jid}", status_code=204)
def delete_job(jid: int, db: Session = Depends(get_db)):
    if not scheduler_service.delete_job(db, jid):
        raise HTTPException(404, "Not found")


@app.post("/api/jobs/{jid}/pause")
def pause_job(jid: int):
    scheduler_service.pause_job(jid)
    return {"paused": True}


@app.post("/api/jobs/{jid}/resume")
def resume_job(jid: int):
    scheduler_service.resume_job(jid)
    return {"resumed": True}


@app.post("/api/jobs/{jid}/run")
async def run_job_now(jid: int, db: Session = Depends(get_db)):
    j = db.query(JobDefinition).filter(JobDefinition.id == jid).first()
    if not j: raise HTTPException(404, "Not found")
    result = await scheduler_service.trigger_job(j.name, triggered_by="manual")
    return result


@app.get("/api/job-runs")
def list_job_runs(
    job_name: Optional[str] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(JobRunLog).order_by(JobRunLog.started_at.desc())
    if job_name: q = q.filter(JobRunLog.job_name == job_name)
    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    return {"total": total, "items": [_run_out(r) for r in rows]}


@app.get("/api/actions")
def list_actions():
    """Return registered action names for the job builder UI."""
    return {"actions": list(JOB_REGISTRY.keys())}


def _job_out(j: JobDefinition) -> dict:
    return {
        "id": j.id, "name": j.name, "description": j.description,
        "job_type": j.job_type, "schedule": j.schedule, "run_at": j.run_at,
        "action": j.action, "action_params": j.action_params,
        "is_active": j.is_active, "run_count": j.run_count,
        "fail_count": j.fail_count, "last_run_at": j.last_run_at,
        "next_run_at": j.next_run_at, "created_at": j.created_at,
    }


def _run_out(r: JobRunLog) -> dict:
    return {
        "id": r.id, "job_name": r.job_name, "triggered_by": r.triggered_by,
        "started_at": r.started_at, "finished_at": r.finished_at,
        "success": r.success, "result": r.result, "error": r.error,
        "duration_ms": r.duration_ms, "context": r.context,
    }


# ── Webhook Events ────────────────────────────────────────────────────────────

@app.get("/api/webhook-events")
def list_webhook_events(
    source: Optional[str] = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    q = db.query(WebhookEvent).order_by(WebhookEvent.created_at.desc())
    if source: q = q.filter(WebhookEvent.source == source)
    total = q.count()
    rows = q.offset(offset).limit(limit).all()
    return {"total": total, "items": [
        {"id": r.id, "source": r.source, "source_id": r.source_id,
         "job_triggered": r.job_triggered, "job_run_success": r.job_run_success,
         "job_error": r.job_error, "ip_address": r.ip_address, "created_at": r.created_at}
        for r in rows
    ]}


# ── Settings ──────────────────────────────────────────────────────────────────

@app.get("/api/settings")
def list_settings(db: Session = Depends(get_db)):
    rows = db.query(AppSetting).all()
    return {r.key: {"value": r.value, "description": r.description} for r in rows}


@app.put("/api/settings/{key}")
def upsert_setting(key: str, body: dict, db: Session = Depends(get_db)):
    s = db.query(AppSetting).filter(AppSetting.key == key).first()
    if s:
        s.value = body.get("value", s.value)
        if "description" in body: s.description = body["description"]
    else:
        s = AppSetting(key=key, value=body.get("value"), description=body.get("description"))
        db.add(s)
    db.commit()
    return {"key": key, "value": s.value}


# ── Manual API call ───────────────────────────────────────────────────────────

class ManualCallRequest(BaseModel):
    endpoint_name: str
    method: str = "GET"
    path: str
    params: Optional[dict] = None
    body: Optional[Any] = None


@app.post("/api/call")
async def manual_call(req: ManualCallRequest, db: Session = Depends(get_db)):
    caller = APICaller(db)
    result = await caller.call(
        req.endpoint_name, req.method, req.path,
        params=req.params, body=req.body, triggered_by="manual_ui"
    )
    return result


# ── Health check (Railway uses this) ─────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


# ── Login / Logout ────────────────────────────────────────────────────────────

_LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in · API Gateway</title>
<style>
  :root {{ --bg:#0d1117; --surface:#161b22; --border:#30363d; --text:#e6edf3;
           --muted:#7d8590; --accent:#2f81f7; --red:#f85149; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--text); min-height:100vh;
          display:flex; align-items:center; justify-content:center;
          font-family:'Inter',system-ui,sans-serif; }}
  .card {{ background:var(--surface); border:1px solid var(--border);
           border-radius:12px; padding:36px 32px; width:340px; }}
  .logo {{ display:flex; align-items:center; gap:10px; margin-bottom:24px; }}
  .logo .icon {{ width:36px; height:36px; background:var(--accent); border-radius:9px;
                 display:flex; align-items:center; justify-content:center; font-size:18px; }}
  .logo h1 {{ font-size:16px; font-weight:600; }}
  .logo span {{ font-size:12px; color:var(--muted); display:block; }}
  label {{ font-size:12px; color:var(--muted); display:block; margin-bottom:6px; }}
  input {{ width:100%; background:#1c2128; border:1px solid var(--border); border-radius:7px;
           color:var(--text); padding:10px 12px; font-size:14px; outline:none; margin-bottom:16px; }}
  input:focus {{ border-color:var(--accent); }}
  button {{ width:100%; background:var(--accent); color:#fff; border:none; border-radius:7px;
            padding:11px; font-size:14px; font-weight:500; cursor:pointer; }}
  button:hover {{ background:#1f6feb; }}
  .err {{ background:rgba(248,81,73,0.1); border:1px solid rgba(248,81,73,0.3);
          color:var(--red); font-size:13px; padding:9px 12px; border-radius:7px; margin-bottom:16px; }}
</style></head><body>
  <form class="card" method="post" action="/login">
    <div class="logo">
      <div class="icon">⚡</div>
      <div><h1>API Gateway</h1><span>Sign in to continue</span></div>
    </div>
    {error}
    <label>Username</label>
    <input name="username" autocomplete="username" autofocus required>
    <label>Password</label>
    <input name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Sign in</button>
  </form>
</body></html>"""


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if not _AUTH_ENABLED or request.session.get("authed"):
        return RedirectResponse(url="/", status_code=302)
    return HTMLResponse(_LOGIN_PAGE.format(error=""))


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request,
                 username: str = Form(...), password: str = Form(...)):
    ok = (
        secrets.compare_digest(username.encode(), _DASH_USER.encode())
        and secrets.compare_digest(password.encode(), _DASH_PASS.encode())
    )
    if ok:
        request.session["authed"] = True
        request.session["user"] = username
        return RedirectResponse(url="/", status_code=302)
    err = '<div class="err">Incorrect username or password.</div>'
    return HTMLResponse(_LOGIN_PAGE.format(error=err), status_code=401)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


@app.get("/api/me")
def whoami(request: Request):
    return {"user": request.session.get("user", _DASH_USER),
            "auth_enabled": _AUTH_ENABLED}


# ── Serve dashboard ───────────────────────────────────────────────────────────
# The auth_gate middleware already protects this route.

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

@app.get("/")
def serve_dashboard():
    index = os.path.join(_STATIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return JSONResponse({"message": "API Gateway running — no frontend found"})

if os.path.exists(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
