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


# Columns of the legacy structured-discovery form, removed from the ORM when the
# form was deprecated (the agent now derives everything from the brief).
_DEPRECATED_DISCOVERY_COLUMNS = (
    "lead_type",
    "location",
    "industry",
    "company_size_min",
    "company_size_max",
    "target_roles",
    "keywords",
    "exclude_keywords",
    # Older form iteration found in existing deployments.
    "service_name",
    "service_description",
    "ideal_customer",
    "target_industries",
    "buyer_signals",
    "enabled_sources",
)


def drop_deprecated_discovery_columns() -> None:
    """Idempotently drop legacy form columns from ``discoveries``.

    Destructive: legacy form-created rows lose their structured fields. Their
    ``brief`` is backfilled with the discovery ``name`` first so those rows can
    still produce a usable agent task (the run's target comes from the brief).

    Any column not in the current ORM model is dropped (SQLite >= 3.35 and
    Postgres both support ``ALTER TABLE ... DROP COLUMN``), so both the repo's
    structured fields and orphaned columns from older form schemas are removed.
    """
    from .models.discovery import Discovery

    if "discoveries" not in inspect(engine).get_table_names():
        return
    expected = {c.name for c in Discovery.__table__.columns}
    existing = {col["name"] for col in inspect(engine).get_columns("discoveries")}
    present = sorted(existing - expected)
    if not present:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE discoveries SET brief = name "
                "WHERE brief IS NULL OR trim(brief) = ''"
            )
        )
        for col in present:
            conn.execute(text(f"ALTER TABLE discoveries DROP COLUMN {col}"))


def get_db():
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
