import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool

import app.db as db_mod


@pytest.fixture()
def legacy_engine(tmp_path, monkeypatch):
    """A `discoveries` table shaped like the pre-deprecation schema."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'legacy.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE discoveries (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    name VARCHAR(200) NOT NULL,
                    brief TEXT,
                    lead_type VARCHAR(200) NOT NULL,
                    location VARCHAR(200) NOT NULL,
                    industry VARCHAR(200),
                    company_size_min INTEGER,
                    company_size_max INTEGER,
                    target_roles JSON NOT NULL,
                    keywords JSON NOT NULL,
                    exclude_keywords JSON NOT NULL,
                    service_name VARCHAR(200),
                    ideal_customer TEXT,
                    target_industries JSON NOT NULL,
                    buyer_signals JSON NOT NULL,
                    enabled_sources JSON NOT NULL,
                    num_leads INTEGER NOT NULL,
                    created_at DATETIME NOT NULL
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO discoveries
                  (user_id, name, brief, lead_type, location, target_roles, keywords,
                   exclude_keywords, target_industries, buyer_signals, enabled_sources,
                   num_leads, created_at)
                VALUES
                  (1, 'Dental in Bengaluru', NULL, 'Dental Clinics', 'Bengaluru, India',
                   '[]', '[]', '[]', '[]', '[]', '[]', 10, '2024-01-01'),
                  (2, 'Brief row', 'Gyms in Delhi', '', '',
                   '[]', '[]', '[]', '[]', '[]', '[]', 5, '2024-01-01')
                """
            )
        )
    monkeypatch.setattr(db_mod, "engine", engine)
    return engine


def test_drop_deprecated_discovery_columns_backfills_brief(legacy_engine):
    db_mod.drop_deprecated_discovery_columns()

    existing = {c["name"] for c in inspect(legacy_engine).get_columns("discoveries")}
    assert existing == {"id", "user_id", "name", "brief", "num_leads", "created_at"}

    with legacy_engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name, brief, num_leads FROM discoveries ORDER BY id")
        ).fetchall()
    # Legacy form-created row: brief backfilled from name so it still runs.
    assert rows[0][1] == "Dental in Bengaluru"
    assert rows[0][2] == 10
    # Row that already had a brief is untouched.
    assert rows[1][1] == "Gyms in Delhi"
    assert rows[1][2] == 5


def test_drop_deprecated_discovery_columns_idempotent(legacy_engine):
    db_mod.drop_deprecated_discovery_columns()
    db_mod.drop_deprecated_discovery_columns()

    existing = {c["name"] for c in inspect(legacy_engine).get_columns("discoveries")}
    assert "lead_type" not in existing
    assert "brief" in existing


def test_drop_deprecated_discovery_columns_noop_on_fresh_schema(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'fresh.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db_mod.Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(db_mod, "engine", engine)

    db_mod.drop_deprecated_discovery_columns()

    existing = {c["name"] for c in inspect(engine).get_columns("discoveries")}
    for col in db_mod._DEPRECATED_DISCOVERY_COLUMNS:
        assert col not in existing
    assert "brief" in existing
    assert "num_leads" in existing