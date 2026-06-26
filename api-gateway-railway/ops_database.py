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

DATABASE_URL = _raw_url.replace("postgres://", "postgresql://", 1)

_is_sqlite   = DATABASE_URL.startswith("sqlite")
_is_postgres = DATABASE_URL.startswith("postgresql")

_engine_kwargs: dict = {}

if _is_sqlite:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
elif _is_postgres:
    _engine_kwargs["pool_size"]    = 5
    _engine_kwargs["max_overflow"] = 10
    _engine_kwargs["pool_pre_ping"] = True
    _engine_kwargs["pool_recycle"] = 300
    _engine_kwargs["connect_args"] = {"sslmode": "require"}

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

    id             = Column(Integer, primary_key=True, index=True)
    name           = Column(String(100), unique=True, nullable=False)
    description    = Column(Text)
    color          = Column(String(20), default="#2f81f7")
    sub_key_header = Column(String(100))
    sub_key_value  = Column(Text)
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    endpoints  = relationship("APIEndpoint", back_populates="project")
    operations = relationship("APIOperation", back_populates="project")


class APIEndpoint(Base):
    """Configured third-party API connections."""
    __tablename__ = "api_endpoints"

    id               = Column(Integer, primary_key=True, index=True)
    name             = Column(String(100), unique=True, nullable=False)
    base_url         = Column(String(500), nullable=False)
    auth_type        = Column(String(50), default="bearer")
    project_id       = Column(Integer, ForeignKey("projects.id"), nullable=True)
    token_url        = Column(String(500))
    client_id        = Column(String(500))
    client_secret    = Column(String(500))
    token_scope      = Column(String(500))
    api_key          = Column(String(500))
    api_key_header   = Column(String(100), default="X-API-Key")
    current_token    = Column(Text)
    token_expires_at = Column(DateTime)
    is_active        = Column(Boolean, default=True)
    created_at       = Column(DateTime, default=datetime.utcnow)
    updated_at       = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    extra_headers    = Column(JSON, default=dict)
    default_timeout  = Column(Integer, default=30)

    calls      = relationship("APICallLog", back_populates="endpoint")
    project    = relationship("Project", back_populates="endpoints")
    operations = relationship("APIOperation", back_populates="endpoint")


class APIOperation(Base):
    """
    A named, configurable API operation — a specific path + method on an endpoint.
    Keeps paths and default params out of Python code and into the portal DB.

    Example:
        name:            denticon-appointments
        endpoint_name:   denticon
        method:          GET
        path:            /denticon/appointments/v0/
        default_params:  {"PageSize": 500, "PageNumber": 1}
        description:     Fetch scheduled appointments by office + date range
        tags:            ["denticon", "scheduling"]
    """
    __tablename__ = "api_operations"

    id             = Column(Integer, primary_key=True, index=True)
    name           = Column(String(200), unique=True, nullable=False)  # slug e.g. denticon-appointments
    label          = Column(String(200))                               # human label for UI
    description    = Column(Text)
    endpoint_id    = Column(Integer, ForeignKey("api_endpoints.id"), nullable=True)
    endpoint_name  = Column(String(100), nullable=False)               # denormalized for fast lookup
    project_id     = Column(Integer, ForeignKey("projects.id"), nullable=True)
    method         = Column(String(10), default="GET")                 # GET | POST | PUT | PATCH | DELETE
    path           = Column(String(500), nullable=False)               # e.g. /denticon/appointments/v0/
    default_params = Column(JSON, default=dict)                        # merged with runtime params
    default_body   = Column(JSON, default=dict)                        # for POST/PUT operations
    response_map   = Column(JSON, default=dict)                        # optional field mapping hints
    is_active      = Column(Boolean, default=True)
    tags           = Column(JSON, default=list)                        # e.g. ["denticon", "scheduling"]
    created_at     = Column(DateTime, default=datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    endpoint = relationship("APIEndpoint", back_populates="operations")
    project  = relationship("Project", back_populates="operations")


class APICallLog(Base):
    """Immutable record of every outbound API call."""
    __tablename__ = "api_call_logs"

    id               = Column(Integer, primary_key=True, index=True)
    endpoint_id      = Column(Integer, ForeignKey("api_endpoints.id"), nullable=True)
    endpoint_name    = Column(String(100))
    method           = Column(String(10))
    url              = Column(Text)
    request_headers  = Column(JSON)
    request_body     = Column(Text)
    status_code      = Column(Integer)
    response_headers = Column(JSON)
    response_body    = Column(Text)
    response_time_ms = Column(Float)
    success          = Column(Boolean)
    error_message    = Column(Text)
    triggered_by     = Column(String(200))
    created_at       = Column(DateTime, default=datetime.utcnow, index=True)
    token_refreshed  = Column(Boolean, default=False)

    endpoint = relationship("APIEndpoint", back_populates="calls")


class TokenRefreshLog(Base):
    """Tracks every token acquisition / refresh event."""
    __tablename__ = "token_refresh_logs"

    id            = Column(Integer, primary_key=True, index=True)
    endpoint_id   = Column(Integer, ForeignKey("api_endpoints.id"))
    endpoint_name = Column(String(100))
    success       = Column(Boolean)
    expires_at    = Column(DateTime)
    error         = Column(Text)
    created_at    = Column(DateTime, default=datetime.utcnow)


class AppSetting(Base):
    """Key-value settings store."""
    __tablename__ = "app_settings"

    key        = Column(String(200), primary_key=True)
    value      = Column(Text)
    description= Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OfficePhoneMap(Base):
    __tablename__ = "office_phone_map"

    id           = Column(Integer, primary_key=True, index=True)
    phone_number = Column(String(30), unique=True, nullable=False, index=True)
    office_id    = Column(String(100), nullable=False)
    office_name  = Column(String(200))
    project_id   = Column(Integer, ForeignKey("projects.id"), nullable=True)
    is_active    = Column(Boolean, default=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DenticonReference(Base):
    __tablename__ = "denticon_reference"

    id        = Column(Integer, primary_key=True, index=True)
    office_id = Column(String(100), nullable=False, index=True)
    ref_type  = Column(String(40), nullable=False, index=True)
    ref_id    = Column(Integer, nullable=False)
    name      = Column(String(300))
    duration  = Column(Integer)
    bookable  = Column(Boolean, default=True)
    extra     = Column(JSON, default=dict)
    updated_at= Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


Base.metadata.create_all(bind=engine)


# ── Lightweight migration ─────────────────────────────────────────────────────
def _ensure_column(table, column, ddl_type):
    from sqlalchemy import inspect, text
    insp = inspect(engine)
    if table not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns(table)}
    if column not in cols:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


def _run_migrations():
    _ensure_column("api_endpoints", "project_id", "INTEGER")
    _ensure_column("projects", "sub_key_header", "VARCHAR(100)")
    _ensure_column("projects", "sub_key_value", "TEXT")
    # APIOperation table is created by create_all — no column migrations needed yet

try:
    _run_migrations()
except Exception as _e:
    import logging
    logging.getLogger("database").warning(f"migration skipped: {_e}")
