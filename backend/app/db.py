"""Database engine, session, and ORM base."""

from datetime import datetime, timezone

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite-friendly)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


is_sqlite = settings.database_url.startswith("sqlite")
engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if is_sqlite else {},
)


if is_sqlite:

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


# Columns added to `leads` after the first release. Schemas are created with
# ``create_all`` (no alembic), so an existing dev DB needs these portable ALTERs.
_LEAD_ADDITIONS = {
    "fit_score": "FLOAT",
    "fit_reason": "VARCHAR(1000)",
    "what_to_sell": "VARCHAR(1000)",
}


def ensure_lead_columns() -> None:
    """Idempotently add post-release columns to the ``leads`` table."""
    if "leads" not in inspect(engine).get_table_names():
        Base.metadata.create_all(bind=engine)
        return
    existing = {col["name"] for col in inspect(engine).get_columns("leads")}
    with engine.begin() as conn:
        for name, coltype in _LEAD_ADDITIONS.items():
            if name not in existing:
                conn.execute(text(f"ALTER TABLE leads ADD COLUMN {name} {coltype}"))


def ensure_discovery_columns() -> None:
    """Idempotently add post-release columns to the ``discoveries`` table."""
    if "discoveries" not in inspect(engine).get_table_names():
        Base.metadata.create_all(bind=engine)
        return
    existing = {col["name"] for col in inspect(engine).get_columns("discoveries")}
    with engine.begin() as conn:
        if "brief" not in existing:
            conn.execute(text("ALTER TABLE discoveries ADD COLUMN brief TEXT"))


def get_db():
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
