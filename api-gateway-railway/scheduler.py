"""
Job Scheduler
─────────────
Wraps APScheduler with a DB-persisted job registry so jobs survive restarts.

Job types:
  • cron     – standard cron expression (e.g. "0 9 * * 1-5")
  • interval – every N seconds/minutes/hours
  • onetime  – fire once at a specific datetime
  • webhook  – no schedule; fired externally via trigger_job()

Every run is logged to JobRunLog.  Jobs hold a reference to an APICaller
action or a custom Python callable registered in JOB_REGISTRY.
"""
import asyncio
import logging
import json
from datetime import datetime
from typing import Callable, Any
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, JSON
from sqlalchemy.orm import Session
from database import Base, SessionLocal, engine

logger = logging.getLogger("scheduler")

# ── Job Run Log model ─────────────────────────────────────────────────────────

class JobDefinition(Base):
    __tablename__ = "job_definitions"
    id           = Column(Integer, primary_key=True)
    name         = Column(String(200), unique=True, nullable=False)
    description  = Column(Text)
    job_type     = Column(String(20))          # cron | interval | onetime | webhook
    schedule     = Column(String(200))         # cron expr OR interval spec "30s/5m/2h"
    run_at       = Column(DateTime)            # for onetime
    action       = Column(String(200))         # key in JOB_REGISTRY
    action_params= Column(JSON, default=dict)  # passed to the action
    is_active    = Column(Boolean, default=True)
    last_run_at  = Column(DateTime)
    next_run_at  = Column(DateTime)
    run_count    = Column(Integer, default=0)
    fail_count   = Column(Integer, default=0)
    created_at   = Column(DateTime, default=datetime.utcnow)
    # Webhook trigger config
    webhook_secret = Column(String(200))       # optional HMAC secret


class JobRunLog(Base):
    __tablename__ = "job_run_logs"
    id           = Column(Integer, primary_key=True)
    job_id       = Column(Integer)
    job_name     = Column(String(200))
    triggered_by = Column(String(200))         # scheduler | webhook | retell | form | manual
    started_at   = Column(DateTime, default=datetime.utcnow)
    finished_at  = Column(DateTime)
    success      = Column(Boolean)
    result       = Column(Text)
    error        = Column(Text)
    duration_ms  = Column(Integer)
    context      = Column(JSON)                # payload that triggered the job


Base.metadata.create_all(bind=engine)


# ── Global action registry ────────────────────────────────────────────────────

JOB_REGISTRY: dict[str, Callable] = {}


def register_action(name: str):
    """Decorator to register a callable as a named job action."""
    def decorator(fn: Callable):
        JOB_REGISTRY[name] = fn
        logger.info(f"Registered job action: {name}")
        return fn
    return decorator


# ── Scheduler service ─────────────────────────────────────────────────────────

class JobScheduler:
    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self._started = False

    def start(self):
        if not self._started:
            self.scheduler.start()
            self._started = True
            logger.info("APScheduler started")
            self._reload_jobs_from_db()

    def stop(self):
        if self._started:
            self.scheduler.shutdown()
            self._started = False

    # ── Job CRUD ──────────────────────────────────────────────────────────────

    def add_job(self, db: Session, job_def: dict) -> JobDefinition:
        """Create a new job definition and schedule it."""
        j = JobDefinition(**job_def)
        db.add(j)
        db.commit()
        db.refresh(j)
        if j.is_active and j.job_type != "webhook":
            self._schedule(j)
        logger.info(f"Added job: {j.name} ({j.job_type})")
        return j

    def update_job(self, db: Session, job_id: int, updates: dict) -> JobDefinition | None:
        j = db.query(JobDefinition).filter(JobDefinition.id == job_id).first()
        if not j:
            return None
        for k, v in updates.items():
            setattr(j, k, v)
        db.commit()
        db.refresh(j)
        # Re-schedule
        try:
            self.scheduler.remove_job(f"job_{j.id}")
        except Exception:
            pass
        if j.is_active and j.job_type != "webhook":
            self._schedule(j)
        return j

    def delete_job(self, db: Session, job_id: int) -> bool:
        j = db.query(JobDefinition).filter(JobDefinition.id == job_id).first()
        if not j:
            return False
        try:
            self.scheduler.remove_job(f"job_{j.id}")
        except Exception:
            pass
        db.delete(j)
        db.commit()
        return True

    def pause_job(self, job_id: int):
        try:
            self.scheduler.pause_job(f"job_{job_id}")
        except Exception as e:
            logger.warning(f"Pause failed for job {job_id}: {e}")

    def resume_job(self, job_id: int):
        try:
            self.scheduler.resume_job(f"job_{job_id}")
        except Exception as e:
            logger.warning(f"Resume failed for job {job_id}: {e}")

    # ── External trigger (webhook / event) ───────────────────────────────────

    async def trigger_job(
        self,
        job_name: str,
        context: dict | None = None,
        triggered_by: str = "manual",
    ) -> dict:
        """Fire a job immediately regardless of its schedule."""
        db = SessionLocal()
        try:
            j = db.query(JobDefinition).filter(JobDefinition.name == job_name).first()
            if not j:
                return {"success": False, "error": f"Job '{job_name}' not found"}
            if not j.is_active:
                return {"success": False, "error": "Job is disabled"}

            result = await self._run_job(j.id, j.name, j.action, j.action_params,
                                         triggered_by, context or {}, db)
            return result
        finally:
            db.close()

    # ── Internal scheduling ───────────────────────────────────────────────────

    def _reload_jobs_from_db(self):
        db = SessionLocal()
        try:
            jobs = db.query(JobDefinition).filter(
                JobDefinition.is_active == True,
                JobDefinition.job_type != "webhook",
            ).all()
            for j in jobs:
                self._schedule(j)
            logger.info(f"Loaded {len(jobs)} jobs from DB")
        finally:
            db.close()

    def _schedule(self, j: JobDefinition):
        job_id = f"job_{j.id}"
        try:
            trigger = self._make_trigger(j)
            self.scheduler.add_job(
                self._run_scheduled_job,
                trigger=trigger,
                id=job_id,
                name=j.name,
                args=[j.id, j.name, j.action, j.action_params],
                replace_existing=True,
                misfire_grace_time=60,
            )
            logger.info(f"Scheduled: {j.name} [{j.job_type}]")
        except Exception as e:
            logger.error(f"Failed to schedule {j.name}: {e}")

    def _make_trigger(self, j: JobDefinition):
        if j.job_type == "cron":
            return CronTrigger.from_crontab(j.schedule, timezone="UTC")
        elif j.job_type == "interval":
            return IntervalTrigger(**self._parse_interval(j.schedule))
        elif j.job_type == "onetime":
            return DateTrigger(run_date=j.run_at, timezone="UTC")
        raise ValueError(f"Unknown job_type: {j.job_type}")

    @staticmethod
    def _parse_interval(spec: str) -> dict:
        """Parse '30s', '5m', '2h', '1d' into kwargs for IntervalTrigger."""
        unit_map = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
        unit = spec[-1]
        value = int(spec[:-1])
        return {unit_map[unit]: value}

    async def _run_scheduled_job(self, job_id, job_name, action, params):
        db = SessionLocal()
        try:
            await self._run_job(job_id, job_name, action, params, "scheduler", {}, db)
        finally:
            db.close()

    async def _run_job(
        self,
        job_id: int,
        job_name: str,
        action: str,
        params: dict,
        triggered_by: str,
        context: dict,
        db: Session,
    ) -> dict:
        started = datetime.utcnow()
        success = False
        result_str = None
        error_str = None

        fn = JOB_REGISTRY.get(action)
        if not fn:
            error_str = f"Action '{action}' not in registry"
            logger.error(error_str)
        else:
            try:
                merged_params = {**(params or {}), "__context__": context, "__db__": db}
                if asyncio.iscoroutinefunction(fn):
                    result = await fn(**{k: v for k, v in merged_params.items()
                                        if k in fn.__code__.co_varnames})
                else:
                    result = fn(**{k: v for k, v in merged_params.items()
                                   if k in fn.__code__.co_varnames})
                success = True
                result_str = json.dumps(result) if result is not None else "OK"
            except Exception as exc:
                error_str = str(exc)
                logger.exception(f"Job '{job_name}' failed: {exc}")

        finished = datetime.utcnow()
        duration_ms = int((finished - started).total_seconds() * 1000)

        run_log = JobRunLog(
            job_id=job_id,
            job_name=job_name,
            triggered_by=triggered_by,
            started_at=started,
            finished_at=finished,
            success=success,
            result=result_str,
            error=error_str,
            duration_ms=duration_ms,
            context=context,
        )
        db.add(run_log)

        # Update counters on the definition
        j = db.query(JobDefinition).filter(JobDefinition.id == job_id).first()
        if j:
            j.last_run_at = finished
            j.run_count = (j.run_count or 0) + 1
            if not success:
                j.fail_count = (j.fail_count or 0) + 1
        db.commit()

        return {
            "success": success,
            "result": result_str,
            "error": error_str,
            "duration_ms": duration_ms,
            "triggered_by": triggered_by,
        }


# Singleton
scheduler_service = JobScheduler()
