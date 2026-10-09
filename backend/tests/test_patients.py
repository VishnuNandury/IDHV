from datetime import date

from app.models import Condition, Document, Encounter, LabResult, Medication, Vital


def _populate(db, patient):
    doc = Document(patient_id=patient.id, filename="lab.pdf", mime="application/pdf", kind="pdf", size=3,
                   status="committed", doc_type="lab_report", extracted_text="[Page 1]\nHbA1c 8.1",
                   provider="seed", document_date=date(2025, 1, 10))
    db.add(doc)
    db.flush()
    src = {"patient_id": patient.id, "source_document_id": doc.id, "source_page": 1}
    db.add_all([
        Condition(name="Type 2 diabetes mellitus", diagnosed_date=date(2019, 3, 1), status="active", **src),
        Condition(name="Viral fever", diagnosed_date=date(2020, 1, 1), status="resolved", **src),
        Medication(drug_name="Glycomet (Metformin)", dosage="500 mg", frequency="BD", active=True,
                   start_date=date(2024, 1, 1), **src),
        Medication(drug_name="Amaryl (Glimepiride)", dosage="1 mg", frequency="OD", active=False,
                   start_date=date(2023, 1, 1), **src),
        LabResult(test_name="HbA1c", value="8.1", value_num=8.1, unit="%", flag="high", date=date(2025, 1, 10), **src),
        LabResult(test_name="HbA1c", value="7.5", value_num=7.5, unit="%", flag="high", date=date(2025, 4, 10), **src),
        LabResult(test_name="LDL", value="120", value_num=120, unit="mg/dL", flag="high", date=date(2025, 1, 10), **src),
        Vital(kind="bp", value="140/90", unit="mmHg", date=date(2025, 1, 10), **src),
        Vital(kind="bp", value="130/80", unit="mmHg", date=date(2025, 4, 10), **src),
        Vital(kind="weight", value="70", value_num=70, unit="kg", date=date(2025, 4, 10), **src),
        Encounter(type="opd", date=date(2025, 1, 10), facility="Clinic", doctor_name="Dr. A", reason="Diabetes review",
                  fee=800, **src),
        Encounter(type="diagnostic", date=date(2025, 1, 9), facility="Lab", fee=1200.5, **src),
        Encounter(type="pharmacy", date=date(2025, 2, 2), facility="Pharmacy", fee=999.5, **src),
        Encounter(type="ipd", date=date(2025, 2, 20), end_date=date(2025, 2, 23), facility="Hospital", fee=50000,
                  **src),
    ])
    db.commit()
    return doc


def test_profile_get_put_and_bmi(client, make_user, auth_headers):
    p = make_user("patient", height_cm=160, weight_kg=64, blood_group="O+")
    r = client.get("/api/patients/me/profile", headers=auth_headers(p))
    assert r.status_code == 200
    prof = r.json()
    assert prof["bmi"] == 25.0 and prof["blood_group"] == "O+" and prof["dhid"].startswith("DH-")

    r = client.put("/api/patients/me/profile", headers=auth_headers(p), json={
        "weight_kg": 60, "allergies": ["Penicillin"], "dob": "1980-05-01", "full_name": "Renamed"})
    assert r.status_code == 200, r.text
    prof = r.json()
    assert prof["weight_kg"] == 60 and prof["allergies"] == ["Penicillin"]
    assert prof["dob"] == "1980-05-01" and prof["full_name"] == "Renamed" and prof["bmi"] == 23.4


def test_profile_me_requires_patient(client, make_user, auth_headers):
    d = make_user("doctor")
    assert client.get("/api/patients/me/profile", headers=auth_headers(d)).status_code == 403


def test_records_include_source_refs(client, make_user, auth_headers, db):
    p = make_user("patient")
    doc = _populate(db, p)
    for kind, n in (("conditions", 2), ("medications", 2), ("labs", 3), ("vitals", 3), ("encounters", 4)):
        r = client.get(f"/api/patients/{p.id}/records/{kind}", headers=auth_headers(p))
        assert r.status_code == 200, kind
        rows = r.json()
        assert len(rows) == n, kind
        for row in rows:
            assert row["source_document_id"] == doc.id
            assert row["source_page"] == 1
            assert row["source_filename"] == "lab.pdf"
    assert client.get(f"/api/patients/{p.id}/records/bogus", headers=auth_headers(p)).status_code == 404
    labs = client.get(f"/api/patients/{p.id}/records/labs", headers=auth_headers(p)).json()
    assert labs[0]["date"] >= labs[-1]["date"]


def test_overview_contents(client, make_user, auth_headers, db):
    p = make_user("patient", height_cm=158, weight_kg=71)
    _populate(db, p)
    ov = client.get(f"/api/patients/{p.id}/overview", headers=auth_headers(p)).json()
    assert ov["patient"]["dhid"] == p.patient_profile.dhid
    assert [c["name"] for c in ov["active_conditions"]] == ["Type 2 diabetes mellitus"]
    assert [m["drug_name"] for m in ov["active_medications"]] == ["Glycomet (Metformin)"]
    assert set(ov["lab_trends"]) >= {"HbA1c", "FBS", "LDL", "Creatinine"}
    assert [x["value_num"] for x in ov["lab_trends"]["HbA1c"]] == [8.1, 7.5]  # ascending by date
    assert ov["lab_trends"]["HbA1c"][0]["unit"] == "%"
    assert ov["bp_series"] == [
        {"date": "2025-01-10", "systolic": 140, "diastolic": 90, "source_document_id": ov["bp_series"][0]["source_document_id"]},
        {"date": "2025-04-10", "systolic": 130, "diastolic": 80, "source_document_id": ov["bp_series"][1]["source_document_id"]},
    ]
    assert ov["latest_vitals"]["bp"]["value"] == "130/80"
    assert ov["counts"]["documents"] == 1 and ov["counts"]["encounters"] == 4


def test_timeline_merged_desc(client, make_user, auth_headers, db):
    p = make_user("patient")
    doc = _populate(db, p)
    tl = client.get(f"/api/patients/{p.id}/timeline", headers=auth_headers(p)).json()
    assert len(tl) == 5
    assert {i["kind"] for i in tl} == {"encounter", "document"}
    dates = [i["date"] for i in tl]
    assert dates == sorted(dates, reverse=True)
    d = next(i for i in tl if i["kind"] == "document")
    assert d["id"] == doc.id and d["source_document_id"] == doc.id and d["type"] == "lab_report"
    for item in tl:
        assert set(item) >= {"date", "kind", "type", "title", "subtitle", "id", "source_document_id"}


def test_expenses(client, make_user, auth_headers, db):
    p = make_user("patient")
    _populate(db, p)
    ex = client.get(f"/api/patients/{p.id}/expenses", headers=auth_headers(p)).json()
    assert ex["total"] == 53000.0
    assert ex["by_type"] == {"opd": 800.0, "ipd": 50000.0, "diagnostic": 1200.5, "pharmacy": 999.5}
    assert ex["by_month"] == [{"month": "2025-01", "amount": 2000.5}, {"month": "2025-02", "amount": 50999.5}]


def test_empty_patient_endpoints(client, make_user, auth_headers):
    p = make_user("patient")
    h = auth_headers(p)
    assert client.get(f"/api/patients/{p.id}/timeline", headers=h).json() == []
    ex = client.get(f"/api/patients/{p.id}/expenses", headers=h).json()
    assert ex == {"total": 0.0, "by_type": {"opd": 0.0, "ipd": 0.0, "diagnostic": 0.0, "pharmacy": 0.0},
                  "by_month": []}
    ov = client.get(f"/api/patients/{p.id}/overview", headers=h).json()
    assert ov["bp_series"] == [] and ov["lab_trends"]["HbA1c"] == []
