"""
Database layer — SQLite locally, PostgreSQL on Railway.
Railway injects DATABASE_URL automatically when you add the Postgres plugin.
"""
import os
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Float,
    Boolean, DateTime, JSON, ForeignKey
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship
from datetime import datetime

_raw_url = os.getenv("DATABASE_URL", "sqlite:///./api_gateway.db")

# Railway (and some other hosts) emit postgres:// — SQLAlchemy 1.4+ requires postgresql://
# Also strip ?sslmode=require and re-add as connect_args for psycopg2
DATABASE_URL = _raw_url.replace("postgres://", "postgresql://", 1)

_is_sqlite   = DATABASE_URL.startswith("sqlite")
_is_postgres = DATABASE_URL.startswith("postgresql")

_engine_kwargs: dict = {}

if _is_sqlite:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
elif _is_postgres:
    # Postgres production settings
    _engine_kwargs["pool_size"]         = 5
    _engine_kwargs["max_overflow"]      = 10
    _engine_kwargs["pool_pre_ping"]     = True   # drop stale connections
    _engine_kwargs["pool_recycle"]      = 300    # recycle every 5 min
    # Railway Postgres requires SSL
    _engine_kwargs["connect_args"]      = {"sslmode": "require"}

engine       = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base         = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Models ────────────────────────────────────────────────────────────────────

class Project(Base):
    """Logical grouping for API endpoints (e.g. 'Jefferson Dental', 'Arch Servicing')."""
    __tablename__ = "projects"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(100), unique=True, nullable=False)
    description = Column(Text)
    color       = Column(String(20), default="#2f81f7")   # accent for the UI badge
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    endpoints = relationship("APIEndpoint", back_populates="project")


class APIEndpoint(Base):
    """Configured third-party API connections."""
    __tablename__ = "api_endpoints"

    id              = Column(Integer, primary_key=True, index=True)
    name            = Column(String(100), unique=True, nullable=False)
    base_url        = Column(String(500), nullable=False)
    auth_type       = Column(String(50), default="bearer")   # bearer | basic | api_key | oauth2
    project_id      = Column(Integer, ForeignKey("projects.id"), nullable=True)
    # OAuth2 / bearer token settings
    token_url       = Column(String(500))
    client_id       = Column(String(500))
    client_secret   = Column(String(500))     # store encrypted in prod
    token_scope     = Column(String(500))
    # API-key style
    api_key         = Column(String(500))
    api_key_header  = Column(String(100), default="X-API-Key")
    # State
    current_token   = Column(Text)
    token_expires_at= Column(DateTime)
    is_active       = Column(Boolean, default=True)
    created_at      = Column(DateTime, default=datetime.utcnow)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    extra_headers   = Column(JSON, default=dict)   # any static headers to inject
    default_timeout = Column(Integer, default=30)

    calls   = relationship("APICallLog", back_populates="endpoint")
    project = relationship("Project", back_populates="endpoints")


class APICallLog(Base):
    """Immutable record of every outbound API call."""
    __tablename__ = "api_call_logs"

    id              = Column(Integer, primary_key=True, index=True)
    endpoint_id     = Column(Integer, ForeignKey("api_endpoints.id"), nullable=True)
    endpoint_name   = Column(String(100))          # denormalized for easy filtering
    # Request
    method          = Column(String(10))
    url             = Column(Text)
    request_headers = Column(JSON)
    request_body    = Column(Text)
    # Response
    status_code     = Column(Integer)
    response_headers= Column(JSON)
    response_body   = Column(Text)
    response_time_ms= Column(Float)
    # Meta
    success         = Column(Boolean)
    error_message   = Column(Text)
    triggered_by    = Column(String(200))          # job name, user, cron, etc.
    created_at      = Column(DateTime, default=datetime.utcnow, index=True)
    # Token refresh tracking
    token_refreshed = Column(Boolean, default=False)

    endpoint = relationship("APIEndpoint", back_populates="calls")


class TokenRefreshLog(Base):
    """Tracks every token acquisition / refresh event."""
    __tablename__ = "token_refresh_logs"

    id           = Column(Integer, primary_key=True, index=True)
    endpoint_id  = Column(Integer, ForeignKey("api_endpoints.id"))
    endpoint_name= Column(String(100))
    success      = Column(Boolean)
    expires_at   = Column(DateTime)
    error        = Column(Text)
    created_at   = Column(DateTime, default=datetime.utcnow)


class AppSetting(Base):
    """Key-value settings store."""
    __tablename__ = "app_settings"

    key        = Column(String(200), primary_key=True)
    value      = Column(Text)
    description= Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OfficePhoneMap(Base):
    """
    Maps an inbound phone number (the number a patient dialed) to a Denticon
    office/location ID. The Retell agent never asks which office — the called
    number identifies it. Stored E.164, e.g. '+18135550100'.
    """
    __tablename__ = "office_phone_map"

    id          = Column(Integer, primary_key=True, index=True)
    phone_number= Column(String(30), unique=True, nullable=False, index=True)
    office_id   = Column(String(100), nullable=False)   # Denticon officeId
    office_name = Column(String(200))                   # human label for the UI
    project_id  = Column(Integer, ForeignKey("projects.id"), nullable=True)
    is_active   = Column(Boolean, default=True)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DenticonReference(Base):
    """
    Cached Denticon practice reference data (production types, providers,
    operatories) per office. Refreshed on demand or on a schedule via the
    'refresh_practice_reference' action — avoids hitting the Practices API
    mid-conversation. ref_type is 'production_type' | 'provider' | 'operatory'.
    """
    __tablename__ = "denticon_reference"

    id          = Column(Integer, primary_key=True, index=True)
    office_id   = Column(String(100), nullable=False, index=True)
    ref_type    = Column(String(40), nullable=False, index=True)
    ref_id      = Column(Integer, nullable=False)    # Denticon id (productionTypeId, etc.)
    name        = Column(String(300))                # description / provider name / operatory name
    duration    = Column(Integer)                    # for production types
    bookable    = Column(Boolean, default=True)      # isBookableOnline (+ isActive)
    extra       = Column(JSON, default=dict)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


Base.metadata.create_all(bind=engine)


# ── Lightweight migration ─────────────────────────────────────────────────────
# create_all() makes new tables but won't ALTER an existing api_endpoints table,
# so add the project_id column if an older database is missing it.
def _ensure_project_column():
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if "api_endpoints" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("api_endpoints")}
    if "project_id" not in cols:
        with engine.begin() as conn:
            conn.execute(text(
                "ALTER TABLE api_endpoints ADD COLUMN project_id INTEGER"
            ))

try:
    _ensure_project_column()
except Exception as _e:
    import logging
    logging.getLogger("database").warning(f"project_id migration skipped: {_e}")
