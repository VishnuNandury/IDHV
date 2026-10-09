"""Doctor API: patient list, ask (consent-guarded, audited), notes (read_write scope)."""
from __future__ import annotations

from datetime import timedelta

from app.ai.base import set_provider_override
from app.ai.offline import OfflineProvider
from app.db import utcnow
from app.models import AuditLog, ConsentGrant, Encounter, Medication
from app.schemas import AskResponse
from tests.fakes import FakeProvider


def _grant(client, auth_headers, patient, doctor, scope="read", minutes=60):
    g = client.post("/api/consent/grants", json={"access_minutes": minutes, "scope": scope},
                    headers=auth_headers(patient)).json()
    r = client.post("/api/consent/redeem", json={"dhid": g["dhid"], "otp": g["otp"]}, headers=auth_headers(doctor))
    assert r.status_code == 200, r.text
    return g


def test_list_patients_only_active_grants(client, make_user, auth_headers, db):
    p1, p2, p3, d = make_user("patient"), make_user("patient"), make_user("patient"), make_user("doctor")
    g1 = _grant(client, auth_headers, p1, d, scope="read_write", minutes=1440)
    _grant(client, auth_headers, p2, d)
    g2 = db.query(ConsentGrant).filter(ConsentGrant.patient_id == p2.id).one()
    g2.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    r = client.get("/api/doctor/patients", headers=auth_headers(d))
    assert r.status_code == 200
    items = r.json()
    assert [i["patient_id"] for i in items] == [p1.id]
    item = items[0]
    assert item["full_name"] == p1.full_name and item["dhid"] == p1.patient_profile.dhid
    assert item["scope"] == "read_write" and item["grant_id"] == g1["grant_id"]
    assert item["expires_at"].endswith("Z")
    assert p3.id not in [i["patient_id"] for i in items]


def test_list_patients_requires_doctor(client, make_user, auth_headers):
    p = make_user("patient")
    assert client.get("/api/doctor/patients", headers=auth_headers(p)).status_code == 403
    assert client.get("/api/doctor/patients").status_code == 401


def test_ask_with_active_grant_offline(client, make_user, auth_headers, db):
    set_provider_override(OfflineProvider())
    p, d = make_user("patient"), make_user("doctor")
    db.add(Medication(patient_id=p.id, drug_name="Glycomet (Metformin)", dosage="500 mg", frequency="BD",
                      active=True))
    db.commit()
    _grant(client, auth_headers, p, d)
    r = client.post(f"/api/doctor/patients/{p.id}/ask", json={"question": "current medications?"},
                    headers=auth_headers(d))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "offline"
    assert "Metformin" in body["answer"]
    assert body["tool_calls"][0]["name"] == "list_medications"
    audit = db.query(AuditLog).filter(AuditLog.action == "ASK_QUERY").all()
    assert len(audit) == 1 and audit[0].detail["question_len"] == len("current medications?")
    assert "question" not in audit[0].detail  # admins must not see clinical text
    assert audit[0].patient_id == p.id and audit[0].actor_id == d.id


def test_ask_default_provider_is_offline_without_key(client, make_user, auth_headers):
    p, d = make_user("patient"), make_user("doctor")
    _grant(client, auth_headers, p, d)
    r = client.post(f"/api/doctor/patients/{p.id}/ask", json={"question": "summary"}, headers=auth_headers(d))
    assert r.status_code == 200 and r.json()["mode"] == "offline"


def test_ask_passes_history_to_provider(client, make_user, auth_headers):
    fake = FakeProvider(answer_response=AskResponse(answer="ok", mode="fake"))
    set_provider_override(fake)
    p, d = make_user("patient"), make_user("doctor")
    _grant(client, auth_headers, p, d)
    hist = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    r = client.post(f"/api/doctor/patients/{p.id}/ask", json={"question": "q", "history": hist},
                    headers=auth_headers(d))
    assert r.status_code == 200 and r.json()["answer"] == "ok"
    assert fake.calls[-1] == ("answer", p.id, "q", hist)


def test_ask_expired_grant_403(client, make_user, auth_headers, db):
    p, d = make_user("patient"), make_user("doctor")
    g = _grant(client, auth_headers, p, d)
    grant = db.get(ConsentGrant, g["grant_id"])
    grant.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    r = client.post(f"/api/doctor/patients/{p.id}/ask", json={"question": "meds"}, headers=auth_headers(d))
    assert r.status_code == 403 and r.json()["detail"] == "CONSENT_REQUIRED"


def test_ask_other_patient_without_grant_403(client, make_user, auth_headers):
    p1, p2, d = make_user("patient"), make_user("patient"), make_user("doctor")
    _grant(client, auth_headers, p1, d)
    r = client.post(f"/api/doctor/patients/{p2.id}/ask", json={"question": "meds"}, headers=auth_headers(d))
    assert r.status_code == 403


def test_ask_empty_question_422(client, make_user, auth_headers):
    p, d = make_user("patient"), make_user("doctor")
    _grant(client, auth_headers, p, d)
    r = client.post(f"/api/doctor/patients/{p.id}/ask", json={"question": ""}, headers=auth_headers(d))
    assert r.status_code == 422


def test_notes_read_write_creates_encounter_and_meds(client, make_user, auth_headers, db):
    p = make_user("patient")
    d = make_user("doctor", full_name="Dr. Test Iyer", specialization="Cardiology", facility="Lotus Heart")
    _grant(client, auth_headers, p, d, scope="read_write")
    body = {"date": "2026-09-30", "reason": "Palpitations review", "diagnosis": "Sinus tachycardia",
            "notes": "Reduce salbutamol use", "advice": "Avoid caffeine", "follow_up_date": "2026-10-30",
            "fee": 800, "medications": [{"drug_name": "Montair (Montelukast)", "dosage": "10 mg",
                                         "frequency": "OD", "duration": "30 days"}]}
    r = client.post(f"/api/doctor/patients/{p.id}/notes", json=body, headers=auth_headers(d))
    assert r.status_code == 201, r.text
    out = r.json()
    enc = db.get(Encounter, out["encounter"]["id"])
    assert enc.patient_id == p.id and enc.type == "opd" and enc.doctor_name == "Dr. Test Iyer"
    assert enc.specialization == "Cardiology" and enc.facility == "Lotus Heart"
    assert str(enc.date) == "2026-09-30" and enc.fee == 800
    meds = db.query(Medication).filter(Medication.patient_id == p.id).all()
    assert len(meds) == 1 and meds[0].prescriber == "Dr. Test Iyer" and meds[0].active
    assert str(meds[0].start_date) == "2026-09-30"
    audit = db.query(AuditLog).filter(AuditLog.action == "WRITE_NOTE").one()
    assert audit.detail["encounter_id"] == enc.id and audit.detail["medications"] == 1


def test_notes_defaults_date_today(client, make_user, auth_headers, db):
    p, d = make_user("patient"), make_user("doctor")
    _grant(client, auth_headers, p, d, scope="read_write")
    r = client.post(f"/api/doctor/patients/{p.id}/notes", json={"reason": "Follow-up"}, headers=auth_headers(d))
    assert r.status_code == 201
    assert r.json()["encounter"]["date"] == utcnow().date().isoformat()


def test_notes_read_scope_403(client, make_user, auth_headers):
    p, d = make_user("patient"), make_user("doctor")
    _grant(client, auth_headers, p, d, scope="read")
    r = client.post(f"/api/doctor/patients/{p.id}/notes", json={"reason": "x"}, headers=auth_headers(d))
    assert r.status_code == 403 and r.json()["detail"] == "CONSENT_REQUIRED"


def test_notes_by_patient_forbidden(client, make_user, auth_headers):
    p = make_user("patient")
    r = client.post(f"/api/doctor/patients/{p.id}/notes", json={"reason": "x"}, headers=auth_headers(p))
    assert r.status_code == 403


def test_ask_request_validation_422(client, make_user, auth_headers):
    p, d = make_user("patient"), make_user("doctor")
    _grant(client, auth_headers, p, d)
    url = f"/api/doctor/patients/{p.id}/ask"
    h = auth_headers(d)
    assert client.post(url, json={"question": "x" * 2001}, headers=h).status_code == 422
    assert client.post(url, json={"question": "q", "history": [{"role": "system", "content": "x"}]},
                       headers=h).status_code == 422
    # long history / long turns are tolerated (trimmed server-side), up to 200 items
    assert client.post(url, json={"question": "q", "history": [{"role": "user", "content": "x" * 8001}]},
                       headers=h).status_code == 200
    turns = [{"role": "user", "content": "a"}] * 201
    assert client.post(url, json={"question": "q", "history": turns}, headers=h).status_code == 422
    ok = [{"role": "user", "content": "a"}] * 200
    assert client.post(url, json={"question": "q", "history": ok}, headers=h).status_code == 200
