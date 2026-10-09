"""Retrieval tools (spec section 7) over the seeded demo DB."""
from __future__ import annotations

import pytest

from app.ai import tools
from app.models import Document
from app.seed import seed_demo


@pytest.fixture
def seeded(db):
    ids = seed_demo(db)
    assert ids is not None
    return ids


def _assert_sources(rows):
    for r in rows:
        assert "source_document_id" in r and "source_page" in r and "source_filename" in r


def test_lab_alias_a1c_returns_hba1c_sorted_by_date(db, seeded):
    rows = tools.get_lab_results(db, seeded["ananya"], test_name="a1c")
    assert len(rows) == 9
    assert {r["test_name"] for r in rows} == {"HbA1c"}
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates)
    assert rows[0]["value_num"] == 8.9 and rows[-1]["value_num"] == 7.1
    _assert_sources(rows)
    assert all(r["source_document_id"] for r in rows)


def test_lab_alias_sugar_returns_glycaemic_tests(db, seeded):
    rows = tools.get_lab_results(db, seeded["ananya"], test_name="Sugar")
    assert {r["test_name"] for r in rows} == {"FBS", "PPBS", "HbA1c"}


@pytest.mark.parametrize("query,expected", [
    ("cholesterol", {"Total Cholesterol", "LDL", "HDL", "Triglycerides"}),
    ("kidney", {"Creatinine", "eGFR"}),
    ("thyroid", {"TSH"}),
    ("ldl", {"LDL"}),
    ("creat", {"Creatinine"}),
])
def test_lab_aliases_and_substring(db, seeded, query, expected):
    rows = tools.get_lab_results(db, seeded["ananya"], test_name=query)
    assert {r["test_name"] for r in rows} == expected


def test_lab_date_filters(db, seeded):
    rows = tools.get_lab_results(db, seeded["ananya"], test_name="HbA1c", date_from="2025-01-01",
                                 date_to="2025-06-30")
    assert [r["value_num"] for r in rows] == [7.6, 7.4]
    assert all("2025-01-01" <= r["date"] <= "2025-06-30" for r in rows)


def test_lab_bad_date_raises(db, seeded):
    with pytest.raises(ValueError):
        tools.get_lab_results(db, seeded["ananya"], date_from="not-a-date")


def test_tools_are_patient_scoped(db, seeded):
    assert tools.get_lab_results(db, seeded["ravi"], test_name="HbA1c") == []
    ravi_meds = tools.list_medications(db, seeded["ravi"])
    assert ravi_meds and not any("Metformin" in m["drug_name"] for m in ravi_meds)


def test_list_medications_active_only(db, seeded):
    active = tools.list_medications(db, seeded["ananya"])
    assert active and all(m["active"] for m in active)
    names = " ".join(m["drug_name"] for m in active)
    assert "Metformin" in names and "Pantocid" not in names
    everything = tools.list_medications(db, seeded["ananya"], active_only=False)
    assert len(everything) > len(active)
    _assert_sources(everything)


def test_conditions_vitals_encounters(db, seeded):
    pid = seeded["ananya"]
    assert any("diabetes" in c["name"].lower() for c in tools.list_conditions(db, pid, status="active"))
    bp = tools.get_vitals(db, pid, kind="bp")
    assert len(bp) == 9 and all(v["kind"] == "bp" for v in bp)
    assert [v["date"] for v in bp] == sorted(v["date"] for v in bp)
    ipd = tools.list_encounters(db, pid, type="ipd")
    assert len(ipd) == 1 and ipd[0]["date"] == "2024-06-22"
    in_2024 = tools.list_encounters(db, pid, date_from="2024-01-01", date_to="2024-12-31")
    assert in_2024 and all(e["date"].startswith("2024") for e in in_2024)
    _assert_sources(bp + ipd)


def test_patient_summary(db, seeded):
    s = tools.get_patient_summary(db, seeded["ananya"])
    assert isinstance(s, dict)
    assert s["patient"]["dhid"] == "DH-8923-4102-9981"
    assert s["active_conditions"] and s["active_medications"] and s["latest_vitals"]
    assert s["counts"]["lab_results"] > 0
    assert "password_hash" not in str(s)


def test_search_documents_discharge_angina(db, seeded):
    hits = tools.search_documents(db, seeded["ananya"], "discharge angina")
    assert hits
    top = hits[0]
    doc = db.get(Document, top["source_document_id"])
    assert doc.filename == "Lotus_Heart_Discharge_Summary_25Jun2024.txt"
    assert top["source_filename"] == doc.filename
    assert isinstance(top["source_page"], int) and 1 <= top["source_page"] <= 3
    assert top["snippet"]
    assert len(hits) <= 5


def test_search_documents_scoped_and_limited(db, seeded):
    hits = tools.search_documents(db, seeded["ravi"], "angina discharge", limit=2)
    assert len(hits) <= 2
    ravi_docs = {d.id for d in db.query(Document).filter(Document.patient_id == seeded["ravi"])}
    assert all(h["source_document_id"] in ravi_docs for h in hits)
    assert tools.search_documents(db, seeded["ananya"], "   ") == []


def test_run_tool_dispatch_and_specs(db, seeded):
    names = {s["name"] for s in tools.TOOL_SPECS}
    assert names == {"get_patient_summary", "list_conditions", "list_medications", "get_lab_results",
                     "get_vitals", "list_encounters", "search_documents"}
    for spec in tools.TOOL_SPECS:
        assert spec["description"] and isinstance(spec["params"], dict)
    rows = tools.run_tool(db, seeded["ananya"], "get_lab_results", {"test_name": "a1c"})
    assert len(rows) == 9
    with pytest.raises(ValueError):
        tools.run_tool(db, seeded["ananya"], "drop_tables", {})
    with pytest.raises(ValueError):
        tools.run_tool(db, seeded["ananya"], "get_lab_results", {"patient_id": seeded["ravi"]})


def test_resolve_citations(db, seeded):
    pid = seeded["ananya"]
    rows = tools.get_lab_results(db, pid, test_name="HbA1c")
    d1, d2 = rows[0]["source_document_id"], rows[1]["source_document_id"]
    ravi_doc = db.query(Document).filter(Document.patient_id == seeded["ravi"]).first().id
    text = (f"HbA1c fell from 8.9% [doc:{d1}] to 8.6% [doc:{d2} p 1]; again [doc:{d1}] "
            f"and a hallucinated [doc:{ravi_doc}] and [doc:99999 p2].")
    cits = tools.resolve_citations(db, pid, text, rows)
    ids = [c.document_id for c in cits]
    assert ids == [d1, d2]  # deduped, ordered, foreign/unknown ids dropped
    assert cits[0].filename.startswith("CityCare_Lab_Report")
    assert cits[1].page == 1
    assert cits[0].snippet


def test_resolve_citations_compact_page_form(db, seeded):
    pid = seeded["ananya"]
    doc = db.query(Document).filter(Document.filename == "Lotus_Heart_Discharge_Summary_25Jun2024.txt").one()
    cits = tools.resolve_citations(db, pid, f"See [doc:{doc.id} p3].", [])
    assert len(cits) == 1 and cits[0].page == 3 and cits[0].filename == doc.filename


# ---- fix round 1 -------------------------------------------------------------

def test_summary_omits_contact_and_insurance(db, seeded):
    p = tools.get_patient_summary(db, seeded["ananya"])["patient"]
    assert "email" not in p and "insurance" not in p and "user_id" not in p


def test_lab_abnormal_only(db, seeded):
    rows = tools.get_lab_results(db, seeded["ananya"], abnormal_only=True)
    assert rows and all(r["flag"] in ("high", "low") for r in rows)
    assert len(rows) < len(tools.get_lab_results(db, seeded["ananya"]))


def test_serialise_for_model_strips_and_truncates():
    import json

    rows = [{"id": i, "patient_id": 1, "created_at": "x", "test_name": "HbA1c", "value": "7." + str(i),
             "source_document_id": 3, "source_page": 1} for i in range(500)]
    text, sent = tools.serialise_for_model(rows)
    data = json.loads(text)
    assert data["truncated"] is True and data["total"] == 500 and data["hint"]
    assert len(data["rows"]) == len(sent) <= tools.MAX_MODEL_ROWS
    assert "patient_id" not in data["rows"][0] and "created_at" not in data["rows"][0]
    assert "source_document_id" in data["rows"][0]

    big = [{"snippet": "x" * 5000, "source_document_id": 1, "source_page": 1} for _ in range(50)]
    text, sent = tools.serialise_for_model(big)
    assert len(text) <= tools.MAX_MODEL_CHARS
    assert json.loads(text)["truncated"] is True and 0 < len(sent) < 50

    small = [{"a": 1, "patient_id": 2}]
    text, sent = tools.serialise_for_model(small)
    assert json.loads(text) == [{"a": 1}]


def test_serialise_summary_dict():
    import json

    text, sent = tools.serialise_for_model({"patient": {"full_name": "A"},
                                            "active_medications": [{"drug_name": "X", "patient_id": 1,
                                                                    "source_document_id": 5,
                                                                    "source_page": 1}]})
    data = json.loads(text)
    assert "patient_id" not in data["active_medications"][0]
    assert sent[0]["source_document_id"] == 5
