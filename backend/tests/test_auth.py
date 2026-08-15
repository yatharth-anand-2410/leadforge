def test_register_login_me(client):
    r = client.post("/auth/register", json={"username": "alice", "password": "secret123"})
    assert r.status_code == 201
    assert r.json()["username"] == "alice"

    r = client.post("/auth/login", json={"username": "alice", "password": "secret123"})
    assert r.status_code == 200
    token = r.json()["access_token"]
    assert token

    r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "alice"


def test_login_wrong_password(client):
    client.post("/auth/register", json={"username": "bob", "password": "pw"})
    r = client.post("/auth/login", json={"username": "bob", "password": "wrong"})
    assert r.status_code == 401


def test_register_duplicate_username(client):
    client.post("/auth/register", json={"username": "carol", "password": "pw"})
    r = client.post("/auth/register", json={"username": "carol", "password": "pw"})
    assert r.status_code == 400


def test_me_requires_auth(client):
    r = client.get("/auth/me")
    assert r.status_code == 401
