"""Task 2: ExtractionResult -> clinical rows with source refs."""
from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.ingestion.commit import CommitError, commit_extraction
from app.models import Condition, Document, Encounter, LabResult, Medication, Vital
from app.schemas import ExtractedCondition, ExtractedLab, ExtractedMedication
from tests.fakes import default_extraction


def _doc(db, patient, **kw):
    d = Document(patient_id=patient.id, filename="labs.pdf", mime="application/pdf", kind="pdf", size=3,
                 content=b"abc", status="needs_review", **kw)
    db.add(d)
    db.commit()
    return d


def test_commit_creates_rows_with_source_refs(db, make_user):
    p = make_user("patient")
    d = _doc(db, p)
    result = default_extraction()
    result.conditions = [ExtractedCondition(name="Type 2 diabetes mellitus", page=2)]
    counts = commit_extraction(db, d, result)
    assert counts == {"conditions": 1, "medications": 1, "lab_results": 1, "vitals": 1, "encounters": 1,
                      "superseded": 0}

    lab = db.scalar(select(LabResult))
    assert (lab.patient_id, lab.source_document_id, lab.source_page) == (p.id, d.id, 1)
    assert lab.value_num == 7.8 and lab.flag == "high" and lab.date == date(2025, 3, 12)
    assert lab.lab_name == "Fake Diagnostics"
    vital = db.scalar(select(Vital))
    assert vital.kind == "bp" and vital.value == "140/90" and vital.source_document_id == d.id
    med = db.scalar(select(Medication))
    assert med.drug_name == "Metformin" and med.prescriber == "Dr. Fake" and med.source_document_id == d.id
    enc = db.scalar(select(Encounter))
    assert enc.type == "diagnostic" and enc.fee == 600.0 and enc.facility == "Fake Diagnostics"
    assert enc.doctor_name == "Dr. Fake" and enc.source_document_id == d.id
    cond = db.scalar(select(Condition))
    assert cond.source_page == 2 and cond.status == "active"

    db.refresh(d)
    assert d.status == "committed" and d.doc_type == "lab_report" and d.document_date == date(2025, 3, 12)
    assert d.extraction["lab_results"][0]["test_name"] == "HbA1c"


def test_prescription_meds_active_and_lab_flag_normalised(db, make_user):
    p = make_user("patient")
    d = _doc(db, p)
    r = default_extraction()
    r.doc_type = "prescription"
    r.document_date = "15/09/2026"  # tolerant date parsing
    r.medications = [ExtractedMedication(drug_name="Glycomet", dosage="500 mg", frequency="BD")]
    r.lab_results = [ExtractedLab(test_name="LDL", value="118 mg/dL", flag="H"), ExtractedLab(test_name=None, value="1")]
    r.encounter.type = "opd"
    counts = commit_extraction(db, d, r)
    assert counts["lab_results"] == 1  # nameless lab skipped
    med = db.scalar(select(Medication))
    assert med.active is True and med.start_date == date(2026, 9, 15)
    lab = db.scalar(select(LabResult))
    assert lab.flag == "high" and lab.value_num == 118.0 and lab.date == date(2026, 9, 15)


def test_lab_report_meds_not_marked_active(db, make_user):
    p = make_user("patient")
    d = _doc(db, p)
    commit_extraction(db, d, default_extraction())  # doc_type lab_report
    assert db.scalar(select(Medication)).active is False


def test_duplicate_active_condition_not_reinserted(db, make_user):
    p = make_user("patient")
    db.add(Condition(patient_id=p.id, name="Hypertension", status="active"))
    db.commit()
    d = _doc(db, p)
    r = default_extraction()
    r.conditions = [ExtractedCondition(name="hypertension")]
    assert commit_extraction(db, d, r)["conditions"] == 0


def test_commit_twice_raises(db, make_user):
    p = make_user("patient")
    d = _doc(db, p)
    commit_extraction(db, d, default_extraction())
    with pytest.raises(CommitError):
        commit_extraction(db, d, default_extraction())
    assert len(db.scalars(select(LabResult)).all()) == 1


def test_pharmacy_bill_meds_active(db, make_user):
    p = make_user("patient")
    d = _doc(db, p)
    r = default_extraction()
    r.doc_type = "pharmacy_bill"
    commit_extraction(db, d, r)
    assert db.scalar(select(Medication)).active is True


def test_commit_requires_needs_review_conditional_update(db, make_user):
    from app.db import SessionLocal

    p = make_user("patient")
    d = _doc(db, p)
    with SessionLocal() as other:  # simulate a concurrent commit that already won
        other.get(Document, d.id).status = "committed"
        other.commit()
    with pytest.raises(CommitError):
        commit_extraction(db, d, default_extraction())  # stale in-memory status says needs_review
    assert db.scalars(select(LabResult)).all() == []


# ---- fix round 2: medication supersession -------------------------------------

DISCHARGE_MEDS = ["Tab Glycomet (Metformin) 500 mg", "Tab Januvia (Sitagliptin) 100 mg", "Tab Telma (Telmisartan) 40 mg",
                  "Tab Atorva (Atorvastatin) 20 mg", "Tab Ecosprin (Aspirin) 75 mg"]


def _rx(doc_type, when, names):
    r = default_extraction()
    r.doc_type = doc_type
    r.document_date = when
    r.lab_results, r.vitals, r.encounter = [], [], None
    r.medications = [ExtractedMedication(drug_name=n, dosage=None, frequency="OD") for n in names]
    return r


def _active(db, patient_id):
    return db.scalars(select(Medication).where(Medication.patient_id == patient_id, Medication.active.is_(True))).all()


def test_generic_name_normalisation():
    from app.ingestion.commit import generic_name

    cases = {
        "Glycomet (Metformin)": "metformin", "Glycomet-SR (Metformin SR)": "metformin", "Glycomet-SR": "metformin",
        "Tab Glyciphage 500 mg": "metformin", "Januvia 100": "sitagliptin", "Telma 40": "telmisartan",
        "Tab. Atorva 20mg": "atorvastatin", "Lipitor": "atorvastatin", "Ecosprin 75": "aspirin",
        "Amaryl 2 mg": "glimepiride", "Rosuvas 10": "rosuvastatin", "Asthalin inhaler": "salbutamol",
        "Budecort 200": "budesonide", "Metolar XR (Metoprolol succinate)": "metoprolol",
        "Cap Pantocid 40 mg": "pantoprazole", "Inj Insulin Glargine": "insulin glargine",
    }
    for raw, expected in cases.items():
        assert generic_name(raw) == expected, raw


def test_discharge_supersedes_seeded_regimen(db):
    from app.seed import seed_demo

    ids = seed_demo(db)
    pid = ids["ananya"]
    assert any("Glimepiride" in m.drug_name for m in _active(db, pid))
    d = Document(patient_id=pid, filename="discharge_summary.docx", kind="docx", size=1, content=b"x",
                 status="needs_review")
    db.add(d)
    db.commit()
    counts = commit_extraction(db, d, _rx("discharge_summary", "2025-11-08", DISCHARGE_MEDS))
    from app.ingestion.commit import generic_name

    active = _active(db, pid)
    generics = sorted(generic_name(m.drug_name) for m in active)
    assert generics == ["aspirin", "atorvastatin", "metformin", "sitagliptin", "telmisartan"]
    assert all(m.source_document_id == d.id for m in active)
    assert counts["superseded"] >= 4  # glimepiride, metoprolol, isosorbide + older duplicates
    glim = db.scalars(select(Medication).where(Medication.patient_id == pid,
                                               Medication.drug_name.like("%Glimepiride%"))).all()
    assert glim and not any(m.active for m in glim)
    assert any(m.end_date == date(2025, 11, 8) for m in glim)

    # a later prescription repeating atorvastatin keeps exactly one active atorvastatin
    d2 = Document(patient_id=pid, filename="rx.jpg", kind="image", size=1, content=b"x", status="needs_review")
    db.add(d2)
    db.commit()
    commit_extraction(db, d2, _rx("prescription", "2026-09-15", DISCHARGE_MEDS))
    active = _active(db, pid)
    assert [generic_name(m.drug_name) for m in active].count("atorvastatin") == 1
    assert len(active) == 5 and all(m.source_document_id == d2.id for m in active)


def test_older_document_committed_later_does_not_undo_newer_regimen(db, make_user):
    from app.ingestion.commit import generic_name

    p = make_user("patient")
    new = _doc(db, p)
    commit_extraction(db, new, _rx("prescription", "2025-11-08", ["Januvia 100", "Glycomet 500"]))
    old = _doc(db, p)
    counts = commit_extraction(db, old, _rx("prescription", "2024-01-10", ["Amaryl 1 mg", "Glycomet 500"]))
    assert counts["superseded"] == 0
    active = _active(db, p.id)
    assert sorted(generic_name(m.drug_name) for m in active) == ["metformin", "sitagliptin"]
    assert all(m.source_document_id == new.id for m in active)
    old_rows = db.scalars(select(Medication).where(Medication.source_document_id == old.id)).all()
    assert len(old_rows) == 2 and not any(m.active for m in old_rows)  # recorded as history
    assert all(m.end_date == date(2025, 11, 8) for m in old_rows)


def test_non_prescribing_doc_never_supersedes(db, make_user):
    p = make_user("patient")
    commit_extraction(db, _doc(db, p), _rx("prescription", "2025-01-01", ["Amaryl 1 mg"]))
    counts = commit_extraction(db, _doc(db, p), _rx("lab_report", "2025-06-01", ["Glycomet 500"]))
    assert counts["superseded"] == 0
    assert [m.drug_name for m in _active(db, p.id)] == ["Amaryl 1 mg"]


def test_prescribing_doc_without_meds_supersedes_nothing(db, make_user):
    p = make_user("patient")
    commit_extraction(db, _doc(db, p), _rx("prescription", "2025-01-01", ["Amaryl 1 mg"]))
    counts = commit_extraction(db, _doc(db, p), _rx("opd_note", "2025-06-01", []))
    assert counts["superseded"] == 0 and len(_active(db, p.id)) == 1
