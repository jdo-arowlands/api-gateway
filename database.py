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

class APIEndpoint(Base):
    """Configured third-party API connections."""
    __tablename__ = "api_endpoints"

    id              = Column(Integer, primary_key=True, index=True)
    name            = Column(String(100), unique=True, nullable=False)
    base_url        = Column(String(500), nullable=False)
    auth_type       = Column(String(50), default="bearer")   # bearer | basic | api_key | oauth2
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

    calls = relationship("APICallLog", back_populates="endpoint")


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


Base.metadata.create_all(bind=engine)
