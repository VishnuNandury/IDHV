import re

from app.auth import hash_password, verify_password


def test_password_hashing_roundtrip():
    h = hash_password("demo1234")
    assert h != "demo1234"
    assert verify_password("demo1234", h)
    assert not verify_password("wrong", h)
    assert not verify_password("demo1234", "not-a-bcrypt-hash")


def test_register_patient_login_me(client):
    r = client.post("/api/auth/register", json={
        "email": "New@Example.in", "password": "secret123", "full_name": "New Patient", "role": "patient"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"]
    assert body["user"]["role"] == "patient"
    assert body["user"]["email"] == "new@example.in"
    assert re.fullmatch(r"DH-\d{4}-\d{4}-\d{4}", body["user"]["dhid"])

    r = client.post("/api/auth/login", json={"email": "new@example.in", "password": "secret123"})
    assert r.status_code == 200
    token = r.json()["access_token"]

    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["full_name"] == "New Patient"


def test_register_doctor_with_profile(client, db):
    r = client.post("/api/auth/register", json={
        "email": "doc@example.in", "password": "secret123", "full_name": "Dr. New", "role": "doctor",
        "license_no": "KMC-1", "specialization": "Cardiology", "qualification": "MBBS, MD",
        "facility": "City Hospital"})
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["role"] == "doctor"
    assert user["doctor_profile"]["specialization"] == "Cardiology"


def test_register_rejects_duplicate_and_admin_role(client):
    payload = {"email": "dup@example.in", "password": "secret123", "full_name": "Dup", "role": "patient"}
    assert client.post("/api/auth/register", json=payload).status_code == 200
    r = client.post("/api/auth/register", json=payload)
    assert r.status_code == 400
    assert "detail" in r.json()
    r = client.post("/api/auth/register", json={**payload, "email": "adm@example.in", "role": "admin"})
    assert r.status_code == 422


def test_login_wrong_password_and_missing_token(client, make_user):
    make_user("patient", email="p@test.in")
    assert client.post("/api/auth/login", json={"email": "p@test.in", "password": "nope"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": "x@test.in", "password": "nope"}).status_code == 401
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_login_writes_audit(client, make_user, db):
    from app.models import AuditLog

    make_user("patient", email="p2@test.in")
    assert client.post("/api/auth/login", json={"email": "p2@test.in", "password": "demo1234"}).status_code == 200
    actions = [a.action for a in db.query(AuditLog).all()]
    assert "LOGIN" in actions


def test_require_role(client, make_user, auth_headers):
    patient = make_user("patient")
    admin = make_user("admin")
    assert client.get("/api/admin/users", headers=auth_headers(patient)).status_code == 403
    assert client.get("/api/admin/users", headers=auth_headers(admin)).status_code == 200


def test_health_and_system_info(client):
    assert client.get("/api/health").json()["status"] == "ok"
    info = client.get("/api/system/info").json()
    assert info["ai_mode"] in ("claude", "offline")
    assert set(info) >= {"ai_mode", "model", "ocr_available", "ffmpeg_available"}
    assert isinstance(info["ocr_available"], bool)
