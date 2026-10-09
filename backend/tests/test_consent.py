import importlib.util
from datetime import timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.consent import ConsentError, active_grant, create_grant, patient_access, redeem_grant
from app.db import get_db, utcnow
from app.models import AuditLog, ConsentGrant


def _doctor_router_installed() -> bool:
    return importlib.util.find_spec("app.routers.doctor") is not None


def _dhid(user):
    return user.patient_profile.dhid


def _grant_and_redeem(client, auth_headers, patient, doctor, scope="read", minutes=60):
    r = client.post("/api/consent/grants", json={"access_minutes": minutes, "scope": scope},
                    headers=auth_headers(patient))
    assert r.status_code == 200, r.text
    g = r.json()
    r = client.post("/api/consent/redeem", json={"dhid": _dhid(patient), "otp": g["otp"]},
                    headers=auth_headers(doctor))
    assert r.status_code == 200, r.text
    return g


def test_patient_cannot_read_other_patient(client, make_user, auth_headers):
    a, b = make_user("patient"), make_user("patient")
    for path in ("overview", "timeline", "records/labs", "expenses"):
        r = client.get(f"/api/patients/{a.id}/{path}", headers=auth_headers(b))
        assert r.status_code == 403, path
        assert r.json()["detail"] == "CONSENT_REQUIRED"
    assert client.get(f"/api/patients/{a.id}/overview", headers=auth_headers(a)).status_code == 200


def test_doctor_without_grant_forbidden(client, make_user, auth_headers):
    p, d = make_user("patient"), make_user("doctor")
    r = client.get(f"/api/patients/{p.id}/overview", headers=auth_headers(d))
    assert r.status_code == 403
    assert r.json()["detail"] == "CONSENT_REQUIRED"


def test_admin_has_no_clinical_access(client, make_user, auth_headers):
    p, admin = make_user("patient"), make_user("admin")
    assert client.get(f"/api/patients/{p.id}/overview", headers=auth_headers(admin)).status_code == 403


def test_full_grant_flow_and_revoke(client, make_user, auth_headers, db):
    p, d = make_user("patient"), make_user("doctor")
    r = client.post("/api/consent/grants", json={"access_minutes": 60, "scope": "read"}, headers=auth_headers(p))
    assert r.status_code == 200
    g = r.json()
    assert len(g["otp"]) == 6 and g["otp"].isdigit()
    assert '"dhid"' in g["qr_payload"] and g["otp"] in g["qr_payload"]

    wrong = "000000" if g["otp"] != "000000" else "111111"
    r = client.post("/api/consent/redeem", json={"dhid": _dhid(p), "otp": wrong}, headers=auth_headers(d))
    assert r.status_code == 400

    r = client.post("/api/consent/redeem", json={"dhid": _dhid(p), "otp": g["otp"]}, headers=auth_headers(d))
    assert r.status_code == 200, r.text
    assert r.json()["patient_id"] == p.id

    r = client.get(f"/api/patients/{p.id}/overview", headers=auth_headers(d))
    assert r.status_code == 200
    views = db.query(AuditLog).filter_by(action="VIEW_RECORD", actor_id=d.id, patient_id=p.id).count()
    assert views == 1

    grants = client.get("/api/consent/grants", headers=auth_headers(p)).json()
    assert grants[0]["status"] == "active"
    assert grants[0]["doctor_name"] == d.full_name

    assert client.delete(f"/api/consent/grants/{g['grant_id']}", headers=auth_headers(p)).status_code == 200
    r = client.get(f"/api/patients/{p.id}/overview", headers=auth_headers(d))
    assert r.status_code == 403 and r.json()["detail"] == "CONSENT_REQUIRED"
    assert client.get("/api/consent/grants", headers=auth_headers(p)).json()[0]["status"] == "revoked"
    actions = {a.action for a in db.query(AuditLog).all()}
    assert {"GRANT_CREATED", "GRANT_REDEEMED", "GRANT_REVOKED", "VIEW_RECORD"} <= actions


def test_expired_grant_forbidden(client, make_user, auth_headers, db):
    p, d = make_user("patient"), make_user("doctor")
    g = _grant_and_redeem(client, auth_headers, p, d)
    assert client.get(f"/api/patients/{p.id}/overview", headers=auth_headers(d)).status_code == 200
    grant = db.get(ConsentGrant, g["grant_id"])
    grant.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    r = client.get(f"/api/patients/{p.id}/overview", headers=auth_headers(d))
    assert r.status_code == 403 and r.json()["detail"] == "CONSENT_REQUIRED"


def test_expired_grant_blocks_ask(client, make_user, auth_headers, db, fake_provider):
    """Review focus #1: grant expiring mid-session must block /ask (route provided by Task 3)."""
    if not _doctor_router_installed():
        pytest.skip("doctor router (Task 3) not installed yet")
    p, d = make_user("patient"), make_user("doctor")
    g = _grant_and_redeem(client, auth_headers, p, d)
    grant = db.get(ConsentGrant, g["grant_id"])
    grant.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    r = client.post(f"/api/doctor/patients/{p.id}/ask", json={"question": "current meds?"},
                    headers=auth_headers(d))
    assert r.status_code == 403 and r.json()["detail"] == "CONSENT_REQUIRED"


def test_stale_otp_cannot_be_redeemed(client, make_user, auth_headers, db):
    p, d = make_user("patient"), make_user("doctor")
    g = client.post("/api/consent/grants", json={"access_minutes": 60, "scope": "read"},
                    headers=auth_headers(p)).json()
    grant = db.get(ConsentGrant, g["grant_id"])
    grant.created_at = utcnow() - timedelta(minutes=11)
    grant.otp_expires_at = grant.created_at + timedelta(minutes=10)
    db.commit()
    r = client.post("/api/consent/redeem", json={"dhid": _dhid(p), "otp": g["otp"]}, headers=auth_headers(d))
    assert r.status_code == 400
    assert client.get("/api/consent/grants", headers=auth_headers(p)).json()[0]["status"] == "otp_expired"


def test_otp_single_use(client, make_user, auth_headers):
    p, d1, d2 = make_user("patient"), make_user("doctor"), make_user("doctor")
    g = _grant_and_redeem(client, auth_headers, p, d1)
    r = client.post("/api/consent/redeem", json={"dhid": _dhid(p), "otp": g["otp"]}, headers=auth_headers(d2))
    assert r.status_code == 400
    assert client.get(f"/api/patients/{p.id}/overview", headers=auth_headers(d2)).status_code == 403


def test_redeem_requires_doctor_and_grant_requires_patient(client, make_user, auth_headers):
    p, d = make_user("patient"), make_user("doctor")
    assert client.post("/api/consent/grants", json={"access_minutes": 60, "scope": "read"},
                       headers=auth_headers(d)).status_code == 403
    g = client.post("/api/consent/grants", json={"access_minutes": 60, "scope": "read"},
                    headers=auth_headers(p)).json()
    assert client.post("/api/consent/redeem", json={"dhid": _dhid(p), "otp": g["otp"]},
                       headers=auth_headers(p)).status_code == 403


def test_invalid_grant_parameters(client, make_user, auth_headers):
    p = make_user("patient")
    assert client.post("/api/consent/grants", json={"access_minutes": 5, "scope": "read"},
                       headers=auth_headers(p)).status_code in (400, 422)
    assert client.post("/api/consent/grants", json={"access_minutes": 60, "scope": "all"},
                       headers=auth_headers(p)).status_code in (400, 422)


def test_cannot_revoke_someone_elses_grant(client, make_user, auth_headers):
    a, b = make_user("patient"), make_user("patient")
    g = client.post("/api/consent/grants", json={"access_minutes": 60, "scope": "read"},
                    headers=auth_headers(a)).json()
    assert client.delete(f"/api/consent/grants/{g['grant_id']}", headers=auth_headers(b)).status_code == 404


def _write_app():
    app = FastAPI()

    @app.post("/p/{patient_id}/write")
    def write(access=Depends(patient_access(write=True))):
        user, patient = access
        return {"user": user.id, "patient": patient.id}

    @app.get("/p/{patient_id}/read")
    def read(access=Depends(patient_access())):
        return {"patient": access[1].id}

    return app


def test_read_scope_cannot_write(make_user, auth_headers, db):
    p, d_read, d_rw = make_user("patient"), make_user("doctor"), make_user("doctor")
    for doc, scope in ((d_read, "read"), (d_rw, "read_write")):
        g = create_grant(db, p, 60, scope)
        redeem_grant(db, doc, _dhid(p), g.otp)
    tc = TestClient(_write_app())
    r = tc.post(f"/p/{p.id}/write", headers=auth_headers(d_read))
    assert r.status_code == 403 and r.json()["detail"] == "CONSENT_REQUIRED"
    assert tc.get(f"/p/{p.id}/read", headers=auth_headers(d_read)).status_code == 200
    assert tc.post(f"/p/{p.id}/write", headers=auth_headers(d_rw)).json() == {"user": d_rw.id, "patient": p.id}
    assert tc.post(f"/p/{p.id}/write", headers=auth_headers(p)).status_code == 200


def test_read_scope_doctor_cannot_post_notes(client, make_user, auth_headers):
    if not _doctor_router_installed():
        pytest.skip("doctor router (Task 3) not installed yet")
    p, d = make_user("patient"), make_user("doctor")
    _grant_and_redeem(client, auth_headers, p, d, scope="read")
    r = client.post(f"/api/doctor/patients/{p.id}/notes", json={"reason": "Follow-up"}, headers=auth_headers(d))
    assert r.status_code == 403 and r.json()["detail"] == "CONSENT_REQUIRED"


def test_active_grant_helper(make_user, db):
    p, d = make_user("patient"), make_user("doctor")
    assert active_grant(db, d.id, p.id) is None
    g = create_grant(db, p, 1440, "read")
    assert active_grant(db, d.id, p.id) is None  # not yet redeemed
    redeem_grant(db, d, _dhid(p), g.otp)
    found = active_grant(db, d.id, p.id)
    assert found is not None and found.id == g.id
    assert found.expires_at > utcnow() + timedelta(minutes=1439)


# ---- fix round 1 ---------------------------------------------------------------

def _create(client, auth_headers, patient):
    r = client.post("/api/consent/grants", json={"access_minutes": 60, "scope": "read"}, headers=auth_headers(patient))
    assert r.status_code == 200
    return r.json()


def _wrong(otp):
    return "000000" if otp != "000000" else "111111"


def test_otp_brute_force_revokes_after_five_failures(client, make_user, auth_headers, db):
    p, d = make_user("patient"), make_user("doctor")
    g = _create(client, auth_headers, p)
    for _ in range(5):
        r = client.post("/api/consent/redeem", json={"dhid": _dhid(p), "otp": _wrong(g["otp"])},
                        headers=auth_headers(d))
        assert r.status_code == 400
    db.expire_all()
    grant = db.get(ConsentGrant, g["grant_id"])
    assert grant.failed_attempts == 5 and grant.revoked_at is not None
    r = client.post("/api/consent/redeem", json={"dhid": _dhid(p), "otp": g["otp"]}, headers=auth_headers(d))
    assert r.status_code == 400
    fails = db.query(AuditLog).filter_by(action="GRANT_REDEEM_FAILED", actor_id=d.id).order_by(AuditLog.id).all()
    assert len(fails) == 6 and fails[0].detail["dhid"] == _dhid(p)


def test_failed_attempts_below_threshold_still_redeemable(client, make_user, auth_headers):
    p, d = make_user("patient"), make_user("doctor")
    g = _create(client, auth_headers, p)
    for _ in range(4):
        client.post("/api/consent/redeem", json={"dhid": _dhid(p), "otp": _wrong(g["otp"])}, headers=auth_headers(d))
    r = client.post("/api/consent/redeem", json={"dhid": _dhid(p), "otp": g["otp"]}, headers=auth_headers(d))
    assert r.status_code == 200


def test_per_doctor_failure_cap_returns_429(client, make_user, auth_headers):
    d = make_user("doctor")
    patients = [make_user("patient") for _ in range(3)]
    for i in range(10):
        r = client.post("/api/consent/redeem", json={"dhid": _dhid(patients[i % 3]), "otp": "999999"},
                        headers=auth_headers(d))
        assert r.status_code == 400
    p = make_user("patient")
    g = _create(client, auth_headers, p)
    r = client.post("/api/consent/redeem", json={"dhid": _dhid(p), "otp": g["otp"]}, headers=auth_headers(d))
    assert r.status_code == 429
    other = make_user("doctor")  # cap is per doctor
    r = client.post("/api/consent/redeem", json={"dhid": _dhid(p), "otp": g["otp"]}, headers=auth_headers(other))
    assert r.status_code == 200


def test_per_doctor_cap_window_is_ten_minutes(make_user, db):
    from sqlalchemy import update

    from app.consent import ConsentRateLimited

    d, p = make_user("doctor"), make_user("patient")
    for _ in range(10):
        with pytest.raises(ConsentError):
            redeem_grant(db, d, _dhid(p), "999999")
    with pytest.raises(ConsentRateLimited):
        redeem_grant(db, d, _dhid(p), "999999")
    db.execute(update(AuditLog).where(AuditLog.action == "GRANT_REDEEM_FAILED")
               .values(ts=utcnow() - timedelta(minutes=11)))
    db.commit()
    g = create_grant(db, p, 60, "read")
    assert redeem_grant(db, d, _dhid(p), g.otp).doctor_id == d.id


def test_concurrent_redeem_conditional_update(make_user, db, monkeypatch):
    """A grant claimed by another session between SELECT and UPDATE must not be re-bound."""
    import app.consent as consent_mod
    from app.db import SessionLocal
    from app.models import User

    p, d1, d2 = make_user("patient"), make_user("doctor"), make_user("doctor")
    g = create_grant(db, p, 60, "read")
    other = SessionLocal()
    try:
        stale = other.get(ConsentGrant, g.id)  # loaded while still unclaimed
        assert stale.doctor_id is None
        redeem_grant(db, d1, _dhid(p), g.otp)
        monkeypatch.setattr(consent_mod, "_find_pending", lambda *a, **k: stale)  # SELECT saw it unclaimed
        with pytest.raises(ConsentError):
            redeem_grant(other, other.get(User, d2.id), _dhid(p), g.otp)
    finally:
        other.close()
    db.expire_all()
    assert db.get(ConsentGrant, g.id).doctor_id == d1.id


def test_doctor_with_grant_for_a_cannot_read_b(client, make_user, auth_headers):
    a, b, d = make_user("patient"), make_user("patient"), make_user("doctor")
    _grant_and_redeem(client, auth_headers, a, d)
    assert client.get(f"/api/patients/{a.id}/overview", headers=auth_headers(d)).status_code == 200
    for path in ("overview", "timeline", "records/labs", "expenses"):
        r = client.get(f"/api/patients/{b.id}/{path}", headers=auth_headers(d))
        assert r.status_code == 403 and r.json()["detail"] == "CONSENT_REQUIRED", path


def test_api_datetimes_are_utc_z(client, make_user, auth_headers):
    p, d = make_user("patient"), make_user("doctor")
    g = _create(client, auth_headers, p)
    assert g["otp_expires_at"].endswith("Z")
    r = client.post("/api/consent/redeem", json={"dhid": _dhid(p), "otp": g["otp"]}, headers=auth_headers(d))
    assert r.json()["expires_at"].endswith("Z")
    item = client.get("/api/consent/grants", headers=auth_headers(p)).json()[0]
    for k in ("created_at", "otp_expires_at", "expires_at"):
        assert item[k].endswith("Z"), k
    assert item["revoked_at"] is None
    assert client.get("/api/auth/me", headers=auth_headers(p)).json()["created_at"].endswith("Z")
