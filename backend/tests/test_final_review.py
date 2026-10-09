"""Final review fix wave: /ask tolerance, audit privacy + IPs, extraction sanitising, PDFium lock,
registration fields, PII minimisation, citations, condition synonyms."""
from __future__ import annotations

import hashlib
import re

import pytest
from sqlalchemy import select

from app.ai import tools
from app.ai.base import set_provider_override
from app.ai.offline import OfflineProvider
from app.ingestion import extractors, pipeline
from app.ingestion.commit import commit_extraction
from app.models import AuditLog, Condition, Document, PatientProfile
from app.schemas import AskResponse, ExtractedCondition, ExtractedEncounter, ExtractedLab, ExtractionResult
from app.seed import seed_demo
from tests.fakes import FakeProvider, default_extraction


def _grant(client, auth_headers, patient, doctor, scope="read", minutes=60):
    g = client.post("/api/consent/grants", json={"access_minutes": minutes, "scope": scope},
                    headers=auth_headers(patient)).json()
    r = client.post("/api/consent/redeem", json={"dhid": g["dhid"], "otp": g["otp"]}, headers=auth_headers(doctor))
    assert r.status_code == 200, r.text
    return g


# ---- I-2: /ask history tolerance ------------------------------------------------

def test_ask_tolerates_long_history_and_trims(client, make_user, auth_headers):
    fake = FakeProvider(answer_response=AskResponse(answer="ok", mode="fake"))
    set_provider_override(fake)
    p, d = make_user("patient"), make_user("doctor")
    _grant(client, auth_headers, p, d)
    hist = [{"role": "user" if i % 2 == 0 else "assistant", "content": f"{i}:" + "x" * 20000} for i in range(200)]
    r = client.post(f"/api/doctor/patients/{p.id}/ask", json={"question": "q", "history": hist},
                    headers=auth_headers(d))
    assert r.status_code == 200, r.text
    sent = fake.calls[-1][3]
    assert len(sent) == 20 and all(len(h["content"]) <= 8000 for h in sent)
    assert sent[-1]["content"].startswith("199:")


def test_ask_still_validates_role_and_question(client, make_user, auth_headers):
    p, d = make_user("patient"), make_user("doctor")
    _grant(client, auth_headers, p, d)
    url, h = f"/api/doctor/patients/{p.id}/ask", auth_headers(d)
    assert client.post(url, json={"question": "x" * 2001}, headers=h).status_code == 422
    assert client.post(url, json={"question": "q", "history": [{"role": "system", "content": "x"}]},
                       headers=h).status_code == 422
    turns = [{"role": "user", "content": "a"}] * 201
    assert client.post(url, json={"question": "q", "history": turns}, headers=h).status_code == 422


# ---- M-5 / B-3: audit privacy and IPs -------------------------------------------

def test_ask_audit_stores_hash_not_text_and_ip(client, make_user, auth_headers, db, fake_provider):
    p, d = make_user("patient"), make_user("doctor")
    _grant(client, auth_headers, p, d)
    q = "Any hypoglycaemia?"
    client.post(f"/api/doctor/patients/{p.id}/ask", json={"question": q}, headers=auth_headers(d))
    row = db.scalar(select(AuditLog).where(AuditLog.action == "ASK_QUERY"))
    assert "question" not in row.detail
    assert row.detail["question_len"] == len(q)
    assert row.detail["question_sha256"] == hashlib.sha256(q.encode("utf-8")).hexdigest()
    assert row.ip


def test_consent_and_note_audit_rows_have_ip(client, make_user, auth_headers, db):
    p, d = make_user("patient"), make_user("doctor")
    g = _grant(client, auth_headers, p, d, scope="read_write")
    client.post("/api/consent/redeem", json={"dhid": g["dhid"], "otp": "999999"}, headers=auth_headers(d))
    client.post(f"/api/doctor/patients/{p.id}/notes", json={"reason": "x"}, headers=auth_headers(d))
    client.put("/api/patients/me/profile", json={"occupation": "Teacher"}, headers=auth_headers(p))
    client.delete(f"/api/consent/grants/{g['grant_id']}", headers=auth_headers(p))
    for action in ("GRANT_CREATED", "GRANT_REDEEMED", "GRANT_REDEEM_FAILED", "WRITE_NOTE", "PROFILE_UPDATED",
                   "GRANT_REVOKED"):
        rows = db.scalars(select(AuditLog).where(AuditLog.action == action)).all()
        assert rows and all(r.ip for r in rows), action


# ---- I-4: sanitise stored extraction --------------------------------------------

def test_pipeline_sanitises_provider_output_so_commit_passes(client, make_user, auth_headers, monkeypatch):
    monkeypatch.setattr(pipeline, "SYNC", True)
    bad = default_extraction()
    bad.doc_type = "rx"
    bad.confidence = 3
    bad.conditions = [ExtractedCondition(name="Type 2 diabetes mellitus", diagnosed_date="2019", page=0)]
    bad.lab_results = [ExtractedLab(test_name="HbA1c", value="7", page=20000, date="31/02/2025")]
    bad.encounter = ExtractedEncounter(type="opd", fee=-5)
    bad.summary = "s" * 6000
    set_provider_override(FakeProvider(result=bad))
    p = make_user("patient")
    h = auth_headers(p)
    doc = client.post("/api/documents", headers=h, files=[("files", ("x.txt", b"x", "text/plain"))]).json()
    doc = doc["accepted"][0]
    ext = doc["extraction"]
    assert ext["doc_type"] == "other" and doc["doc_type"] == "other"
    assert ext["confidence"] == 1.0
    assert ext["conditions"][0]["diagnosed_date"] is None and ext["conditions"][0]["page"] is None
    assert ext["lab_results"][0]["page"] is None and ext["lab_results"][0]["date"] is None
    assert ext["encounter"]["fee"] is None
    assert len(ext["summary"]) == 5000
    assert any("diagnosed_date" in w and "2019" in w for w in ext["warnings"])
    r = client.post(f"/api/documents/{doc['id']}/commit", headers=h, json={"extraction": ext})
    assert r.status_code == 200, r.text


# ---- I-3: PDFium lock ------------------------------------------------------------

def test_pdfium_render_holds_lock(monkeypatch):
    import pypdfium2 as pdfium

    assert isinstance(extractors._PDFIUM_LOCK, type(__import__("threading").Lock()))
    seen = []

    class _Doc:
        def __init__(self, data):
            seen.append(extractors._PDFIUM_LOCK.locked())

        def __getitem__(self, i):
            raise IndexError

        def close(self):
            seen.append(extractors._PDFIUM_LOCK.locked())

    monkeypatch.setattr(pdfium, "PdfDocument", _Doc)
    with pytest.raises(IndexError):
        extractors._render_pdf_pages(b"%PDF", [1])
    assert seen == [True, True] and not extractors._PDFIUM_LOCK.locked()


# ---- M-1 / M-2: registration fields and length limits ----------------------------

def test_register_patient_stores_dob_sex_phone(client, db):
    r = client.post("/api/auth/register", json={
        "email": "p1@example.in", "password": "secret123", "full_name": "P One", "role": "patient",
        "dob": "1990-05-01", "sex": "female", "phone": "+91 98450 00000"})
    assert r.status_code == 200, r.text
    prof = db.get(PatientProfile, r.json()["user"]["id"])
    assert str(prof.dob) == "1990-05-01" and prof.sex == "female" and prof.phone == "+91 98450 00000"


def test_register_and_profile_length_limits(client, make_user, auth_headers):
    base = {"email": "p2@example.in", "password": "secret123", "full_name": "P", "role": "patient"}
    assert client.post("/api/auth/register", json={**base, "phone": "9" * 33}).status_code == 422
    assert client.post("/api/auth/register", json={**base, "role": "doctor", "license_no": "L" * 65}).status_code == 422
    p = make_user("patient")
    h = auth_headers(p)
    assert client.put("/api/patients/me/profile", json={"blood_group": "O+" * 5}, headers=h).status_code == 422
    assert client.put("/api/patients/me/profile", json={"sex": "x" * 17}, headers=h).status_code == 422
    assert client.put("/api/patients/me/profile", json={"blood_group": "O+"}, headers=h).status_code == 200


# ---- PII minimisation -----------------------------------------------------------

def test_summary_drops_contact_pii_keeps_age(db):
    ids = seed_demo(db)
    p = tools.get_patient_summary(db, ids["ananya"])["patient"]
    for k in ("phone", "address", "emergency_contact", "family_members", "dob", "occupation"):
        assert k not in p, k
    assert p.get("age")


# ---- B-1: every cited marker resolves to a citation ------------------------------

_MARK = re.compile(r"\[doc:(\d+)")


def test_offline_citations_cover_every_marker(db):
    ids = seed_demo(db)
    r = OfflineProvider().answer(db, ids["ananya"], "Summarise this patient's diabetes control over 3 years", [])
    marked = {int(m) for m in _MARK.findall(r.answer)}
    assert marked
    assert marked <= {c.document_id for c in r.citations}


def test_resolve_citations_bounded_and_patient_scoped(db):
    ids = seed_demo(db)
    own = db.scalars(select(Document.id).where(Document.patient_id == ids["ananya"])).all()
    other = db.scalar(select(Document.id).where(Document.patient_id != ids["ananya"]))
    text = " ".join(f"[doc:{i}]" for i in own) + (f" [doc:{other}]" if other else "")
    cites = tools.resolve_citations(db, ids["ananya"], text, [])
    assert {c.document_id for c in cites} == set(own[:50])
    assert other not in {c.document_id for c in cites}


# ---- B-2: condition synonyms and acute discharge events -------------------------

def _doc(db, patient, doc_type="discharge_summary"):
    d = Document(patient_id=patient.id, filename="ds.docx", mime="application/octet-stream", kind="docx", size=1,
                 content=b"x", status="needs_review")
    db.add(d)
    db.commit()
    return d


@pytest.mark.parametrize("existing, new", [
    ("Essential hypertension", "Hypertension"),
    ("Hypertension", "HTN"),
    ("Type 2 diabetes mellitus", "T2DM"),
    ("T2DM", "Diabetes mellitus type 2"),
    ("Dyslipidaemia", "dyslipidemia"),
    ("Hyperlipidaemia", "Dyslipidaemia"),
    ("CAD", "Coronary artery disease"),
])
def test_condition_synonyms_deduped(db, make_user, existing, new):
    p = make_user("patient")
    db.add(Condition(patient_id=p.id, name=existing, status="active"))
    db.commit()
    r = default_extraction()
    r.conditions = [ExtractedCondition(name=new)]
    assert commit_extraction(db, _doc(db, p), r)["conditions"] == 0


def test_synonyms_within_one_extraction_deduped(db, make_user):
    p = make_user("patient")
    r = default_extraction()
    r.conditions = [ExtractedCondition(name="Hypertension"), ExtractedCondition(name="HTN")]
    assert commit_extraction(db, _doc(db, p), r)["conditions"] == 1


def test_discharge_acute_events_resolved(db, make_user):
    p = make_user("patient")
    r = default_extraction()
    r.doc_type = "discharge_summary"
    r.conditions = [ExtractedCondition(name="Hypoglycaemia", status="active"),
                    ExtractedCondition(name="Symptomatic hypoglycemia"),
                    ExtractedCondition(name="Type 2 diabetes mellitus", status="active")]
    commit_extraction(db, _doc(db, p), r)
    status = {c.name: c.status for c in db.scalars(select(Condition))}
    assert status == {"Hypoglycaemia": "resolved", "Symptomatic hypoglycemia": "resolved",
                      "Type 2 diabetes mellitus": "active"}


def test_offline_citations_cover_markers_beyond_first_rows(db, make_user):
    from datetime import date, timedelta

    from app.models import LabResult

    p = make_user("patient")
    today = date.today()
    for i in range(15):
        d = Document(patient_id=p.id, filename=f"lab_{i:02d}.pdf", kind="pdf", size=1, content=b"x",
                     status="committed")
        db.add(d)
        db.flush()
        db.add(LabResult(patient_id=p.id, test_name="HbA1c", value=f"{7 + i / 10:.1f}", value_num=7 + i / 10,
                         unit="%", date=today - timedelta(days=30 * (i + 1)), source_document_id=d.id, source_page=1))
    db.commit()
    r = OfflineProvider().answer(db, p.id, "Summarise this patient's diabetes control over 3 years", [])
    marked = {int(m) for m in _MARK.findall(r.answer)}
    assert len(marked) == 15
    cited = {c.document_id: c.filename for c in r.citations}
    assert marked <= set(cited)
    assert all(cited[i].startswith("lab_") for i in marked)
