def _auth_headers(client, username="dave", password="pw"):
    client.post("/auth/register", json={"username": username, "password": password})
    r = client.post("/auth/login", json={"username": username, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_create_discovery_queues_job(client):
    headers = _auth_headers(client)
    r = client.post(
        "/discoveries",
        json={"brief": "Dental Clinics in Bengaluru", "num_leads": 10},
        headers=headers,
    )
    assert r.status_code == 201
    discovery = r.json()
    assert discovery["name"] == "Dental Clinics in Bengaluru"

    jobs = client.get(f"/discoveries/{discovery['id']}/jobs", headers=headers).json()
    assert len(jobs) == 1
    assert jobs[0]["status"] == "queued"


def test_re_run_creates_new_job(client):
    headers = _auth_headers(client)
    d = client.post("/discoveries", json={"brief": "Restaurants in Mumbai"}, headers=headers).json()

    r = client.post(f"/discoveries/{d['id']}/run", headers=headers)
    assert r.status_code == 201
    assert r.json()["status"] == "queued"

    jobs = client.get(f"/discoveries/{d['id']}/jobs", headers=headers).json()
    assert len(jobs) == 2


def test_jobs_are_user_scoped(client):
    h1 = _auth_headers(client, "u1", "pw")
    d = client.post("/discoveries", json={"brief": "Gyms in Delhi"}, headers=h1).json()

    h2 = _auth_headers(client, "u2", "pw")
    r = client.get(f"/discoveries/{d['id']}/jobs", headers=h2)
    assert r.status_code == 404

    job_id = client.get(f"/discoveries/{d['id']}/jobs", headers=h1).json()[0]["id"]
    r = client.get(f"/jobs/{job_id}", headers=h2)
    assert r.status_code == 404
