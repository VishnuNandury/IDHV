"""Task 2: /api/documents upload -> pipeline -> review -> commit."""
from __future__ import annotations

import io
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import select

from app.ai.base import DigitiseError
from app.db import utcnow
from app.ingestion import pipeline
from app.models import AuditLog, ConsentGrant, Document
from tests.fakes import FakeProvider


@pytest.fixture(autouse=True)
def _sync_pipeline(monkeypatch):
    """Uploads are processed inline so assertions are deterministic."""
    monkeypatch.setattr(pipeline, "SYNC", True)


def _jpg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (800, 600), (250, 250, 245)).save(buf, format="JPEG")
    return buf.getvalue()


def _upload(client, headers, *files):
    return client.post("/api/documents", headers=headers, files=[("files", f) for f in files])


def _grant(db, patient, doctor, scope="read", minutes=60):
    now = utcnow()
    g = ConsentGrant(patient_id=patient.id, doctor_id=doctor.id, otp="123456", created_at=now,
                     otp_expires_at=now + timedelta(minutes=10), access_minutes=minutes,
                     expires_at=now + timedelta(minutes=minutes), scope=scope)
    db.add(g)
    db.commit()
    return g


def test_upload_accepts_valid_rejects_bad(client, make_user, auth_headers, fake_provider):
    p = make_user("patient")
    r = _upload(client, auth_headers(p), ("rx.jpg", _jpg(), "image/jpeg"), ("evil.exe", b"MZ\x90", "application/x-msdownload"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["accepted"]) == 1 and len(body["rejected"]) == 1
    assert body["rejected"][0]["filename"] == "evil.exe" and body["rejected"][0]["reason"]
    doc = body["accepted"][0]
    assert set(doc) >= {"id", "patient_id", "filename", "mime", "kind", "size", "status", "doc_type", "provider",
                        "error", "uploaded_at", "document_date", "extraction"}
    assert "content" not in doc and "extracted_text" not in doc
    assert doc["kind"] == "image" and doc["status"] == "needs_review" and doc["provider"] == "fake"
    assert doc["extraction"]["lab_results"][0]["test_name"] == "HbA1c"
    assert fake_provider.calls and fake_provider.calls[0][1] == "rx.jpg"


def test_oversize_rejected_individually(client, make_user, auth_headers, fake_provider, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "max_upload_mb", 1)
    p = make_user("patient")
    r = _upload(client, auth_headers(p), ("big.txt", b"a" * (1024 * 1024 + 1), "text/plain"),
                ("ok.txt", b"HbA1c 7.1 %", "text/plain"), ("empty.pdf", b"", "application/pdf"))
    body = r.json()
    assert [d["filename"] for d in body["accepted"]] == ["ok.txt"]
    assert {x["filename"] for x in body["rejected"]} == {"big.txt", "empty.pdf"}


def test_full_flow_commit_and_records(client, make_user, auth_headers, fake_provider):
    p = make_user("patient")
    h = auth_headers(p)
    doc = _upload(client, h, ("labs.txt", b"HbA1c 7.8 %", "text/plain")).json()["accepted"][0]

    detail = client.get(f"/api/documents/{doc['id']}", headers=h).json()
    assert detail["extracted_text"].startswith("HbA1c")
    assert client.get("/api/documents", headers=h).json()[0]["id"] == doc["id"]

    edited = detail["extraction"]
    edited["lab_results"][0]["value"] = "7.9"
    r = client.post(f"/api/documents/{doc['id']}/commit", headers=h, json={"extraction": edited})
    assert r.status_code == 200, r.text
    assert r.json()["document"]["status"] == "committed" and r.json()["counts"]["lab_results"] == 1

    labs = client.get(f"/api/patients/{p.id}/records/labs", headers=h).json()
    assert labs[0]["value"] == "7.9" and labs[0]["source_document_id"] == doc["id"]
    assert labs[0]["source_filename"] == "labs.txt"

    assert client.post(f"/api/documents/{doc['id']}/commit", headers=h, json={}).status_code == 409
    assert client.post(f"/api/documents/{doc['id']}/reprocess", headers=h).status_code == 409
    assert client.delete(f"/api/documents/{doc['id']}", headers=h).status_code == 409


def test_commit_without_body_uses_stored_extraction(client, make_user, auth_headers, fake_provider):
    p = make_user("patient")
    h = auth_headers(p)
    doc = _upload(client, h, ("labs.txt", b"x", "text/plain")).json()["accepted"][0]
    r = client.post(f"/api/documents/{doc['id']}/commit", headers=h)
    assert r.status_code == 200 and r.json()["counts"]["lab_results"] == 1


def test_provider_failure_falls_back_to_offline(client, make_user, auth_headers):
    from app.ai.base import set_provider_override

    set_provider_override(FakeProvider(name="claude", raise_exc=DigitiseError("refusal")))
    p = make_user("patient")
    doc = _upload(client, auth_headers(p), ("labs.txt", b"HbA1c 7.8 % 4.0-5.6\nBP 140/90", "text/plain")).json()["accepted"][0]
    assert doc["status"] == "needs_review"
    assert doc["provider"] == "offline"
    assert "AI digitisation failed (DigitiseError); used offline extraction" in doc["extraction"]["warnings"]
    assert doc["extraction"]["lab_results"][0]["test_name"] == "HbA1c"


def test_offline_provider_failure_marks_failed(client, make_user, auth_headers):
    from app.ai.base import set_provider_override

    set_provider_override(FakeProvider(name="offline", raise_exc=RuntimeError("boom")))
    p = make_user("patient")
    doc = _upload(client, auth_headers(p), ("labs.txt", b"x", "text/plain")).json()["accepted"][0]
    assert doc["status"] == "failed" and "boom" in doc["error"]


def test_extraction_error_marks_failed_then_reprocess(client, make_user, auth_headers, fake_provider):
    p = make_user("patient")
    h = auth_headers(p)
    doc = _upload(client, h, ("broken.pdf", b"%PDF-1.4 not really", "application/pdf")).json()["accepted"][0]
    assert doc["status"] == "failed" and doc["error"]
    r = client.post(f"/api/documents/{doc['id']}/reprocess", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "failed"  # still broken, but retried
    assert client.delete(f"/api/documents/{doc['id']}", headers=h).status_code == 200
    assert client.get(f"/api/documents/{doc['id']}", headers=h).status_code == 404


def test_reprocess_clears_previous_extraction(client, make_user, auth_headers, fake_provider, db):
    p = make_user("patient")
    h = auth_headers(p)
    doc = _upload(client, h, ("labs.txt", b"x", "text/plain")).json()["accepted"][0]
    fake_provider.result.doc_type = "prescription"
    r = client.post(f"/api/documents/{doc['id']}/reprocess", headers=h).json()
    assert r["status"] == "needs_review" and r["extraction"]["doc_type"] == "prescription"
    assert len(fake_provider.calls) == 2


def test_auto_commit(client, make_user, auth_headers, fake_provider, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "auto_commit", True)
    p = make_user("patient")
    doc = _upload(client, auth_headers(p), ("labs.txt", b"x", "text/plain")).json()["accepted"][0]
    assert doc["status"] == "committed"
    assert client.get(f"/api/patients/{p.id}/records/labs", headers=auth_headers(p)).json()


def test_file_download_and_access_control(client, make_user, auth_headers, fake_provider, db):
    p = make_user("patient")
    other = make_user("patient")
    doctor = make_user("doctor")
    img = _jpg()
    doc = _upload(client, auth_headers(p), ("rx.jpg", img, "image/jpeg")).json()["accepted"][0]
    url = f"/api/documents/{doc['id']}"

    r = client.get(url + "/file", headers=auth_headers(p))
    assert r.status_code == 200 and r.content == img and r.headers["content-type"] == "image/jpeg"

    for u in (other, doctor):
        assert client.get(url, headers=auth_headers(u)).status_code == 403
        assert client.get(url + "/file", headers=auth_headers(u)).json()["detail"] == "CONSENT_REQUIRED"
    assert client.get(f"/api/documents?patient_id={p.id}", headers=auth_headers(doctor)).status_code == 403
    # another patient cannot mutate
    assert client.post(url + "/commit", headers=auth_headers(other)).status_code == 403
    assert client.delete(url, headers=auth_headers(other)).status_code == 403

    _grant(db, p, doctor)
    assert client.get(url, headers=auth_headers(doctor)).status_code == 200
    assert client.get(url + "/file", headers=auth_headers(doctor)).content == img
    listed = client.get(f"/api/documents?patient_id={p.id}", headers=auth_headers(doctor)).json()
    assert [d["id"] for d in listed] == [doc["id"]]
    actions = [a.action for a in db.scalars(select(AuditLog).where(AuditLog.actor_id == doctor.id))]
    assert "VIEW_RECORD" in actions and "DOWNLOAD_REPORT" in actions
    # doctors never upload
    assert _upload(client, auth_headers(doctor), ("a.txt", b"x", "text/plain")).status_code == 403


def test_upload_is_audited(client, make_user, auth_headers, fake_provider, db):
    p = make_user("patient")
    _upload(client, auth_headers(p), ("a.txt", b"x", "text/plain"))
    assert db.scalar(select(AuditLog).where(AuditLog.action == "UPLOAD")).patient_id == p.id


def test_resume_pending_and_async_enqueue(db, make_user, fake_provider, monkeypatch):
    p = make_user("patient")
    d = Document(patient_id=p.id, filename="q.txt", mime="text/plain", kind="text", size=1, content=b"x",
                 status="processing")
    db.add(d)
    db.commit()
    monkeypatch.setattr(pipeline, "SYNC", False)
    assert pipeline.resume_pending() == 1
    pipeline.wait_idle(timeout=10)
    db.expire_all()
    assert db.get(Document, d.id).status == "needs_review"


# ---- fix round 1 -------------------------------------------------------------

def test_garbage_provider_output_marks_failed(client, make_user, auth_headers):
    from app.ai.base import set_provider_override

    class Garbage:
        name = "claude"

        def digitise(self, content, filename):
            return "this is not an ExtractionResult"

    set_provider_override(Garbage())
    p = make_user("patient")
    doc = _upload(client, auth_headers(p), ("a.txt", b"x", "text/plain")).json()["accepted"][0]
    assert doc["status"] == "failed" and doc["error"]


def test_nul_bytes_stripped_before_storing(client, make_user, auth_headers, fake_provider, db):
    fake_provider.result.summary = "bad\x00summary"
    fake_provider.result.lab_results[0].unit = "%\x00"
    p = make_user("patient")
    doc = _upload(client, auth_headers(p), ("a.txt", b"HbA1c\x00 7.8 %", "text/plain")).json()["accepted"][0]
    assert doc["status"] == "needs_review"
    stored = db.get(Document, doc["id"])
    assert "\x00" not in stored.extracted_text and stored.extracted_text.startswith("HbA1c 7.8")
    assert stored.extraction["summary"] == "badsummary" and stored.extraction["lab_results"][0]["unit"] == "%"


def test_media_extension_with_wrong_content_rejected(client, make_user, auth_headers, fake_provider):
    p = make_user("patient")
    body = _upload(client, auth_headers(p), ("photo.jpg", b"just some text", "image/jpeg"),
                   ("clip.mp4", b"#EXTM3U\n#EXTINF:-1,\nhttp://169.254.169.254/x", "video/mp4")).json()
    assert body["accepted"] == []
    assert {r["filename"] for r in body["rejected"]} == {"photo.jpg", "clip.mp4"}


def test_mime_follows_magic_bytes(client, make_user, auth_headers, fake_provider):
    p = make_user("patient")
    png = io.BytesIO()
    Image.new("RGB", (20, 20)).save(png, format="PNG")
    doc = _upload(client, auth_headers(p), ("scan.jpg", png.getvalue(), "image/jpeg")).json()["accepted"][0]
    assert doc["mime"] == "image/png"


def test_max_files_per_upload(client, make_user, auth_headers, fake_provider):
    p = make_user("patient")
    files = [(f"f{i}.txt", b"x", "text/plain") for i in range(12)]
    body = _upload(client, auth_headers(p), *files).json()
    assert len(body["accepted"]) == 7
    assert [r["filename"] for r in body["rejected"]] == [f"f{i}.txt" for i in range(7, 12)]
    assert "7" in body["rejected"][0]["reason"]


def test_reprocess_queued_conflicts_and_claim_skips(client, make_user, auth_headers, fake_provider, db):
    p = make_user("patient")
    d = Document(patient_id=p.id, filename="q.txt", mime="text/plain", kind="text", size=1, content=b"x",
                 status="queued")
    db.add(d)
    db.commit()
    assert client.post(f"/api/documents/{d.id}/reprocess", headers=auth_headers(p)).status_code == 409
    # a job that cannot claim the document (not queued) does nothing
    d.status = "needs_review"
    db.commit()
    assert pipeline.process_document(d.id) == "needs_review"
    assert fake_provider.calls == []


@pytest.mark.parametrize("patch, field", [
    ({"doc_type": "selfie"}, "doc_type"),
    ({"document_date": "not-a-date"}, "document_date"),
    ({"lab_results": [{"test_name": "HbA1c", "value": "7", "date": "31/02/2025"}]}, "date"),
    ({"lab_results": [{"test_name": "HbA1c", "value": "7", "page": 0}]}, "page"),
    ({"encounter": {"type": "opd", "fee": -5}}, "fee"),
    ({"encounter": {"type": "opd", "follow_up_date": "soon"}}, "follow_up_date"),
    ({"summary": "x" * 5001}, "summary"),
    ({"vitals": [{"kind": "bp", "value": "120/80"}] * 501}, "vitals"),
])
def test_commit_rejects_invalid_edits(client, make_user, auth_headers, fake_provider, patch, field):
    p = make_user("patient")
    h = auth_headers(p)
    doc = _upload(client, h, ("labs.txt", b"x", "text/plain")).json()["accepted"][0]
    edited = dict(doc["extraction"], **patch)
    r = client.post(f"/api/documents/{doc['id']}/commit", headers=h, json={"extraction": edited})
    assert r.status_code == 422, r.text
    assert any(field in [str(x) for x in e["loc"]] for e in r.json()["detail"]), r.json()
    assert client.get(f"/api/documents/{doc['id']}", headers=h).json()["status"] == "needs_review"


def test_commit_infinite_fee_rejected(client, make_user, auth_headers, fake_provider):
    p = make_user("patient")
    h = auth_headers(p)
    doc = _upload(client, h, ("labs.txt", b"x", "text/plain")).json()["accepted"][0]
    edited = doc["extraction"]
    edited["encounter"]["fee"] = 1e400  # serialises as Infinity
    import json as _json

    r = client.post(f"/api/documents/{doc['id']}/commit", headers={**h, "Content-Type": "application/json"},
                    content=_json.dumps({"extraction": edited}))
    assert r.status_code == 422


def test_commit_normalises_valid_dates(client, make_user, auth_headers, fake_provider):
    p = make_user("patient")
    h = auth_headers(p)
    doc = _upload(client, h, ("labs.txt", b"x", "text/plain")).json()["accepted"][0]
    edited = dict(doc["extraction"], document_date="15/09/2026")
    r = client.post(f"/api/documents/{doc['id']}/commit", headers=h, json={"extraction": edited})
    assert r.status_code == 200
    assert r.json()["document"]["document_date"] == "2026-09-15"
    assert r.json()["document"]["extraction"]["document_date"] == "2026-09-15"


# ---- fix round 2 ---------------------------------------------------------------

def test_commit_rejects_absurd_page(client, make_user, auth_headers, fake_provider):
    p = make_user("patient")
    h = auth_headers(p)
    doc = _upload(client, h, ("labs.txt", b"x", "text/plain")).json()["accepted"][0]
    edited = dict(doc["extraction"], lab_results=[{"test_name": "HbA1c", "value": "7", "page": 10001}])
    r = client.post(f"/api/documents/{doc['id']}/commit", headers=h, json={"extraction": edited})
    assert r.status_code == 422 and any("page" in map(str, e["loc"]) for e in r.json()["detail"])
    edited["lab_results"][0]["page"] = 10000
    assert client.post(f"/api/documents/{doc['id']}/commit", headers=h, json={"extraction": edited}).status_code == 200


def test_commit_strips_nul_from_edits(client, make_user, auth_headers, fake_provider, db):
    p = make_user("patient")
    h = auth_headers(p)
    doc = _upload(client, h, ("labs.txt", b"x", "text/plain")).json()["accepted"][0]
    edited = dict(doc["extraction"], summary="edited\u0000 summary")
    edited["lab_results"][0]["test_name"] = "HbA1c\u0000"
    r = client.post(f"/api/documents/{doc['id']}/commit", headers=h, json={"extraction": edited})
    assert r.status_code == 200
    assert r.json()["document"]["extraction"]["summary"] == "edited summary"
    labs = client.get(f"/api/patients/{p.id}/records/labs", headers=h).json()
    assert labs[0]["test_name"] == "HbA1c"


def test_auto_commit_audit_failure_keeps_document_committed(client, make_user, auth_headers, fake_provider,
                                                            monkeypatch):
    from app.config import settings

    def broken_log_event(*a, **k):
        raise RuntimeError("audit store down")

    monkeypatch.setattr(settings, "auto_commit", True)
    monkeypatch.setattr(pipeline, "log_event", broken_log_event)
    p = make_user("patient")
    doc = _upload(client, auth_headers(p), ("labs.txt", b"x", "text/plain")).json()["accepted"][0]
    assert doc["status"] == "committed" and doc["error"] is None
