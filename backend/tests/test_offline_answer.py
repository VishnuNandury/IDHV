"""OfflineProvider: keyword intent router -> markdown answer with citations (spec section 7)."""
from __future__ import annotations

import pytest

from app.ai.offline import OFFLINE_BANNER, OfflineProvider
from app.models import Document
from app.schemas import AskResponse, ExtractedContent, ExtractionResult
from app.seed import seed_demo


@pytest.fixture
def seeded(db):
    return seed_demo(db)


def _ask(db, pid, q) -> AskResponse:
    return OfflineProvider().answer(db, pid, q, [])


def _tools(resp):
    return [t.name for t in resp.tool_calls]


def test_provider_shape():
    p = OfflineProvider()
    assert p.name == "offline"


def test_current_medications(db, seeded):
    r = _ask(db, seeded["ananya"], "What are her current medications?")
    assert r.mode == "offline"
    assert _tools(r) == ["list_medications"]
    assert "Metformin" in r.answer and "Pantocid" not in r.answer
    assert "|" in r.answer and OFFLINE_BANNER in r.answer
    assert r.citations
    ananya_docs = {d.id for d in db.query(Document).filter(Document.patient_id == seeded["ananya"])}
    assert all(c.document_id in ananya_docs for c in r.citations)
    assert r.tool_calls[0].row_count > 0


def test_hba1c_trend_table(db, seeded):
    r = _ask(db, seeded["ananya"], "Show the HbA1c trend")
    assert _tools(r) == ["get_lab_results"]
    assert r.tool_calls[0].args.get("test_name") == "HbA1c"
    lines = [ln for ln in r.answer.splitlines() if ln.startswith("|")]
    assert len(lines) == 2 + 9  # header + separator + 9 rows
    # newest first in the table
    assert "2025-10-13" in lines[2] and "2023-10-10" in lines[-1]
    assert "8.9" in r.answer and "7.1" in r.answer
    assert "falling" in r.answer.lower()
    assert r.citations


def test_sugar_alias_in_question(db, seeded):
    r = _ask(db, seeded["ananya"], "how is her blood sugar control?")
    assert "get_lab_results" in _tools(r)
    assert "FBS" in r.answer and "PPBS" in r.answer


def test_bp_question_uses_vitals(db, seeded):
    r = _ask(db, seeded["ananya"], "BP readings over time")
    assert _tools(r) == ["get_vitals"]
    assert "148/92" in r.answer


def test_admission_question_uses_encounters(db, seeded):
    r = _ask(db, seeded["ananya"], "Any hospital admission?")
    assert _tools(r)[0] == "list_encounters"
    assert "Lotus" in r.answer


def test_allergy_question_uses_summary(db, seeded):
    r = _ask(db, seeded["ananya"], "Does she have any allergies?")
    assert _tools(r) == ["get_patient_summary"]
    assert "Allerg" in r.answer


def test_unknown_question_falls_back_to_search(db, seeded):
    r = _ask(db, seeded["ananya"], "what did the angiography show?")
    assert _tools(r) == ["search_documents"]
    assert r.citations
    assert any("Discharge" in c.filename for c in r.citations)


def test_no_data_message(db, seeded):
    r = _ask(db, seeded["ravi"], "HbA1c trend")
    assert "No matching" in r.answer or "no matching" in r.answer.lower()
    assert r.mode == "offline"


def test_offline_digitise_without_extractor(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "app.ai.offline_extract":
            raise ImportError("missing")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    res = OfflineProvider().digitise(ExtractedContent(text="hello"), "x.txt")
    assert isinstance(res, ExtractionResult)
    assert "offline extractor unavailable" in res.warnings


# ---- fix round 1: the spec's suggested demo questions --------------------------

def _table_rows(answer):
    return [ln for ln in answer.splitlines() if ln.startswith("|")][2:]


def test_demo_q_diabetes_control(db, seeded):
    r = _ask(db, seeded["ananya"], "Summarise this patient's diabetes control over 3 years")
    assert {t.name for t in r.tool_calls} == {"get_lab_results"}
    assert {t.args["test_name"] for t in r.tool_calls} == {"FBS", "PPBS", "HbA1c"}
    assert all("date_from" in t.args for t in r.tool_calls)
    rows = _table_rows(r.answer)
    assert rows and any("HbA1c" in x for x in rows) and any("FBS" in x for x in rows)
    assert "falling" in r.answer.lower() and r.citations


def test_demo_q_current_meds_and_interactions(db, seeded):
    r = _ask(db, seeded["ananya"], "What medications is she currently on and any interactions?")
    assert [t.name for t in r.tool_calls] == ["list_medications"]
    assert r.tool_calls[0].args == {"active_only": True}
    rows = _table_rows(r.answer)
    assert len(rows) == 7 and "Pantocid" not in r.answer
    assert "interaction" in r.answer.lower() and r.citations


def test_demo_q_abnormal_labs_last_6_months(db, seeded):
    r = _ask(db, seeded["ananya"], "Any abnormal labs in the last 6 months?")
    assert r.tool_calls[0].name == "get_lab_results"
    assert r.tool_calls[0].args.get("abnormal_only") is True
    rows = _table_rows(r.answer)
    assert rows and all(("high" in x or "low" in x) for x in rows)
    # seed data ends Oct 2025, so the window is anchored to the latest result and that is said
    assert "latest" in r.answer.lower()
    assert all(x.split("|")[1].strip() >= "2025-04-13" for x in rows)
    assert r.citations


def test_demo_q_admissions_with_discharge_diagnoses(db, seeded):
    r = _ask(db, seeded["ananya"], "List all hospital admissions with discharge diagnoses")
    assert [t.name for t in r.tool_calls] == ["list_encounters"]
    assert r.tool_calls[0].args == {"type": "ipd"}
    rows = _table_rows(r.answer)
    assert len(rows) == 1 and "angina" in rows[0].lower() and "Lotus" in rows[0]
    assert r.citations


def test_relative_window_today(db, seeded):
    from datetime import timedelta

    from app.db import utcnow
    from app.models import LabResult

    db.add(LabResult(patient_id=seeded["ananya"], test_name="HbA1c", value="6.9", value_num=6.9, unit="%",
                     flag="high", date=utcnow().date() - timedelta(days=10)))
    db.commit()
    r = _ask(db, seeded["ananya"], "HbA1c in the last 2 months")
    rows = _table_rows(r.answer)
    assert len(rows) == 1 and "6.9" in rows[0]


def test_med_history_words_word_boundary(db, seeded):
    r = _ask(db, seeded["ananya"], "any medicines for fever?")
    assert r.tool_calls[0].args == {"active_only": True}
    r = _ask(db, seeded["ananya"], "medicines she has ever taken")
    assert r.tool_calls[0].args == {"active_only": False}
