"""
API Gateway – Main Application
"""
import os
import secrets
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from typing import Any, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from database import get_db, APIEndpoint, APICallLog, AppSetting, SessionLocal
import actions  # noqa — registers all @register_action decorators on startup
from api_caller import APICaller
from scheduler import scheduler_service, JobDefinition, JobRunLog, JOB_REGISTRY, register_action
from webhooks import router as webhook_router, WebhookEvent

# ── Optional dashboard basic-auth ─────────────────────────────────────────────
_DASH_USER = os.getenv("DASHBOARD_USER", "")
_DASH_PASS = os.getenv("DASHBOARD_PASSWORD", "")
_AUTH_ENABLED = bool(_DASH_USER and _DASH_PASS)
security = HTTPBasic(auto_error=False)

def require_auth(credentials: HTTPBasicCredentials = Depends(security)):
    if not _AUTH_ENABLED:
        return
    ok = (
        credentials is not None
        and secrets.compare_digest(credentials.username.encode(), _DASH_USER.encode())
        and secrets.compare_digest(credentials.password.encode(), _DASH_PASS.encode())
    )
    if not ok:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic realm='API Gateway'"},
        )

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

app.include_router(webhook_router)


# ═══════════════════════════════════════════════════════════════════════════════
# ENDPOINTS — grouped by resource
# ═══════════════════════════════════════════════════════════════════════════════

# ── Endpoints (API connections) ───────────────────────────────────────────────

class EndpointCreate(BaseModel):
    name: str
    base_url: str
    auth_type: str = "bearer"
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
    for k, v in data.items():
        if hasattr(e, k): setattr(e, k, v)
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


# ── Serve dashboard (auth-protected) ─────────────────────────────────────────

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

@app.get("/", dependencies=[Depends(require_auth)])
def serve_dashboard():
    index = os.path.join(_STATIC_DIR, "index.html")
    if os.path.exists(index):
        return FileResponse(index)
    return JSONResponse({"message": "API Gateway running — no frontend found"})

if os.path.exists(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
