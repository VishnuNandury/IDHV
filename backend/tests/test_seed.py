from app.audit import verify_chain
from app.consent import active_grant
from app.db import utcnow
from app.models import Document, Encounter, LabResult, Medication, User
from app.schemas import ExtractionResult
from app.seed import seed_demo


def _seed(db):
    seed_demo(db)
    users = {u.email: u for u in db.query(User).all()}
    return users


def test_seed_users_and_profiles(db):
    users = _seed(db)
    assert set(users) == {"ananya@demo.in", "ravi@demo.in", "meera@demo.in", "admin@demo.in"}
    ananya = users["ananya@demo.in"]
    assert ananya.patient_profile.dhid == "DH-8923-4102-9981"
    assert ananya.patient_profile.sex == "F" and ananya.patient_profile.dob.year == 1968
    assert users["ravi@demo.in"].patient_profile.dob.year == 1990
    assert users["meera@demo.in"].doctor_profile.qualification.startswith("MBBS")
    assert users["admin@demo.in"].role == "admin"


def test_seed_is_idempotent(db):
    seed_demo(db)
    seed_demo(db)
    assert db.query(User).count() == 4


def test_seed_ananya_history_with_sources(db):
    ananya = _seed(db)["ananya@demo.in"]
    encs = db.query(Encounter).filter_by(patient_id=ananya.id).all()
    assert len(encs) >= 8
    assert sum(1 for e in encs if e.type == "opd") >= 8
    assert sum(1 for e in encs if e.type == "ipd") == 1
    assert all(e.source_document_id for e in encs)
    hba1c = db.query(LabResult).filter_by(patient_id=ananya.id, test_name="HbA1c").order_by(LabResult.date).all()
    assert len(hba1c) >= 6
    assert all(r.source_document_id and r.source_page for r in hba1c)
    assert hba1c[0].value_num == 8.9 and hba1c[-1].value_num == 7.1
    drugs = " ".join(m.drug_name for m in db.query(Medication).filter_by(patient_id=ananya.id))
    for d in ("Metformin", "Glimepiride", "Telmisartan", "Atorvastatin", "Aspirin"):
        assert d in drugs
    active = {m.drug_name: m for m in db.query(Medication).filter_by(patient_id=ananya.id, active=True)}
    active_names = " ".join(active)
    # Medication story agreed with the upload samples (Glimepiride still active at end of seed).
    for d in ("Metformin", "Glimepiride", "Telmisartan", "Atorvastatin", "Aspirin"):
        assert d in active_names, d
    assert "Sitagliptin" not in drugs
    atorva = next(m for n, m in active.items() if "Atorvastatin" in n)
    assert atorva.dosage == "20 mg" and atorva.start_date.year == 2024
    metformin = min((m for m in db.query(Medication).filter_by(patient_id=ananya.id)
                     if "Metformin" in m.drug_name), key=lambda m: m.start_date)
    assert metformin.start_date.year == 2019


def test_seed_ananya_history_ends_before_samples(db):
    """Upload samples add 2025-11 onwards events; seeded history must end by 2025-10-31."""
    from datetime import date

    from app.models import Condition, Vital

    ananya = _seed(db)["ananya@demo.in"]
    cutoff = date(2025, 10, 31)
    for model, col in ((Encounter, "date"), (LabResult, "date"), (Vital, "date"), (Medication, "start_date"),
                       (Condition, "diagnosed_date"), (Document, "document_date")):
        for row in db.query(model).filter_by(patient_id=ananya.id):
            v = getattr(row, col)
            assert v is None or v <= cutoff, (model.__name__, v)
    for e in db.query(Encounter).filter_by(patient_id=ananya.id):
        assert e.follow_up_date is None or e.follow_up_date <= date(2026, 1, 31)


def test_seed_documents_realistic(db):
    _seed(db)
    docs = db.query(Document).all()
    assert len(docs) >= 20
    for d in docs:
        assert d.status == "committed" and d.provider == "seed"
        assert "[Page 1]" in d.extracted_text
        assert d.content and d.size == len(d.content)
        ExtractionResult.model_validate(d.extraction)
    discharge = [d for d in docs if d.doc_type == "discharge_summary"]
    assert len(discharge) == 1
    assert "[Page 2]" in discharge[0].extracted_text and "angina" in discharge[0].extracted_text.lower()


def test_seed_consent_state(db):
    users = _seed(db)
    meera = users["meera@demo.in"]
    g = active_grant(db, meera.id, users["ravi@demo.in"].id)
    assert g is not None and g.access_minutes == 10080 and g.expires_at > utcnow()
    assert active_grant(db, meera.id, users["ananya@demo.in"].id) is None
    assert verify_chain(db)["ok"] is True


def test_seeded_api_views(client, db, auth_headers):
    users = _seed(db)
    ananya = users["ananya@demo.in"]
    r = client.post("/api/auth/login", json={"email": "ananya@demo.in", "password": "demo1234"})
    assert r.status_code == 200
    h = auth_headers(ananya)
    ex = client.get(f"/api/patients/{ananya.id}/expenses", headers=h).json()
    fees = [e.fee for e in db.query(Encounter).filter_by(patient_id=ananya.id) if e.fee]
    assert ex["total"] == round(sum(fees), 2)
    assert all(ex["by_type"][k] > 0 for k in ("opd", "ipd", "diagnostic", "pharmacy"))
    assert abs(sum(m["amount"] for m in ex["by_month"]) - ex["total"]) < 0.01

    ov = client.get(f"/api/patients/{ananya.id}/overview", headers=h).json()
    assert len(ov["lab_trends"]["HbA1c"]) >= 6 and ov["lab_trends"]["LDL"] and ov["lab_trends"]["Creatinine"]
    assert len(ov["bp_series"]) >= 8
    # Ravi's doctor has access
    meera = users["meera@demo.in"]
    assert client.get(f"/api/patients/{users['ravi@demo.in'].id}/overview",
                      headers=auth_headers(meera)).status_code == 200
    assert client.get(f"/api/patients/{ananya.id}/overview", headers=auth_headers(meera)).status_code == 403


def test_refresh_seeded_grant(db):
    from datetime import timedelta

    from app.models import AuditLog, ConsentGrant
    from app.seed import refresh_seeded_grant

    users = _seed(db)
    meera, ravi = users["meera@demo.in"], users["ravi@demo.in"]
    assert refresh_seeded_grant(db) is None  # still active -> nothing to do
    g = db.query(ConsentGrant).filter_by(patient_id=ravi.id).one()
    g.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    new = refresh_seeded_grant(db)
    assert new is not None and new.access_minutes == 10080 and new.scope == "read_write"
    assert active_grant(db, meera.id, ravi.id).id == new.id
    assert db.query(AuditLog).filter_by(action="GRANT_REFRESHED_SEED").count() == 1
    new.revoked_at = utcnow()  # a grant the patient revoked is respected
    db.commit()
    assert refresh_seeded_grant(db) is None
    assert verify_chain(db)["ok"] is True


def test_refresh_seeded_grant_without_seed_is_noop(db):
    from app.seed import refresh_seeded_grant

    assert refresh_seeded_grant(db) is None


def test_long_extracted_values_fit():
    """Postgres enforces VARCHAR lengths: free-form extracted fields must be TEXT."""
    from app.models import Condition, Vital

    for model, cols in ((LabResult, ("test_name", "value", "unit", "ref_range", "flag", "lab_name")),
                        (Vital, ("kind", "value", "unit")),
                        (Medication, ("drug_name", "dosage", "frequency", "duration", "route", "instructions",
                                      "prescriber")),
                        (Condition, ("name", "severity", "status")),
                        (Encounter, ("type", "reason", "diagnosis", "facility", "doctor_name", "specialization")),
                        (Document, ("doc_type", "filename"))):
        for c in cols:
            col = model.__table__.c[c]
            assert getattr(col.type, "length", None) is None, f"{model.__name__}.{c} is {col.type}"
