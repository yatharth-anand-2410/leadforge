import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models.lead import Lead


@pytest.fixture()
def client_and_db(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'leads.db'}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    c = TestClient(app)
    yield c, Session
    c.close()
    app.dependency_overrides.clear()
    engine.dispose()


def _headers(client, username="dave"):
    client.post("/auth/register", json={"username": username, "password": "pw"})
    r = client.post("/auth/login", json={"username": username, "password": "pw"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_list_leads_filters_shortlisted(client_and_db):
    client, Session = client_and_db
    h = _headers(client)
    d = client.post(
        "/discoveries", json={"brief": "Dental Clinics in Bengaluru"}, headers=h
    ).json()
    did = d["id"]

    db = Session()
    db.add(Lead(discovery_id=did, user_id=1, name="Good", status="shortlisted", score=80, verified=True))
    db.add(Lead(discovery_id=did, user_id=1, name="Bad", status="discarded", score=10, verified=False))
    db.commit()
    db.close()

    all_leads = client.get(f"/discoveries/{did}/leads", headers=h).json()
    assert len(all_leads) == 2

    short = client.get(f"/discoveries/{did}/leads?status=shortlisted", headers=h).json()
    assert len(short) == 1
    assert short[0]["name"] == "Good"
    assert short[0]["verified"] is True


def test_leads_are_user_scoped(client_and_db):
    client, Session = client_and_db
    h1 = _headers(client, "u1")
    d = client.post("/discoveries", json={"brief": "Gyms in Delhi"}, headers=h1).json()

    db = Session()
    db.add(Lead(discovery_id=d["id"], user_id=1, name="Gym", status="shortlisted"))
    db.commit()
    lead_id = db.query(Lead).first().id
    db.close()

    h2 = _headers(client, "u2")
    assert client.get(f"/discoveries/{d['id']}/leads", headers=h2).status_code == 404
    assert client.get(f"/leads/{lead_id}", headers=h2).status_code == 404
    assert client.get(f"/leads/{lead_id}", headers=h1).status_code == 200
