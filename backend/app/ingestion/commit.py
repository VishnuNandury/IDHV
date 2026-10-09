"""Write a reviewed ``ExtractionResult`` into clinical tables with source refs (spec section 5, step 4)."""
from __future__ import annotations

import re
from datetime import date, datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import Condition, Document, Encounter, LabResult, Medication, Vital
from app.schemas import ExtractionResult

# Documents whose medication list evidences current therapy -> rows are marked active. Indian pharmacy bills
# list what the patient is actually buying/taking, so they count too.
PRESCRIBING_DOC_TYPES = frozenset({"prescription", "discharge_summary", "opd_note", "pharmacy_bill"})
ENCOUNTER_TYPES = frozenset({"opd", "ipd", "diagnostic", "pharmacy"})
VITAL_KINDS = frozenset({"bp", "weight", "glucose", "pulse", "spo2", "temp"})
_FLAGS = {"h": "high", "high": "high", "hi": "high", "l": "low", "low": "low", "lo": "low",
          "n": "normal", "normal": "normal", "within range": "normal"}
_ENCOUNTER_FOR_DOC_TYPE = {"lab_report": "diagnostic", "imaging_report": "diagnostic", "pharmacy_bill": "pharmacy",
                           "discharge_summary": "ipd"}


class CommitError(ValueError):
    """The document cannot be committed (already committed). Routers map this to 409."""


def strip_nul(value):
    """Remove NUL characters (Postgres TEXT/JSON rejects them) from str / nested dict / list values."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, dict):
        return {strip_nul(k): strip_nul(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_nul(v) for v in value]
    return value


# ---------------------------------------------------------------------------
# Medication generic names (for supersession / de-duplication)
# ---------------------------------------------------------------------------

BRAND_TO_GENERIC: dict[str, str] = {
    "glycomet": "metformin", "glyciphage": "metformin", "gluconorm": "metformin", "obimet": "metformin",
    "januvia": "sitagliptin", "istavel": "sitagliptin", "galvus": "vildagliptin",
    "amaryl": "glimepiride", "glimy": "glimepiride",
    "telma": "telmisartan", "telmikind": "telmisartan",
    "atorva": "atorvastatin", "lipitor": "atorvastatin", "storvas": "atorvastatin",
    "rosuvas": "rosuvastatin", "crestor": "rosuvastatin",
    "ecosprin": "aspirin", "disprin": "aspirin",
    "metolar": "metoprolol", "betaloc": "metoprolol",
    "sorbitrate": "isosorbide dinitrate",
    "pantocid": "pantoprazole", "pan": "pantoprazole",
    "asthalin": "salbutamol", "budecort": "budesonide", "montair": "montelukast",
    "thyronorm": "levothyroxine", "eltroxin": "levothyroxine",
}
_FORM_PREFIX = re.compile(r"^\s*(?:tab|tabs|tablet|cap|caps|capsule|syp|syr|syrup|inj|injection)\b\.?\s*")
_STRENGTH = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|ml|iu|units?|%)?")
_DROP_TOKENS = frozenset({
    # release modifiers
    "sr", "xr", "er", "cr", "mr", "xl", "la", "od", "ds", "retard",
    # salts
    "succinate", "tartrate", "hydrochloride", "hcl", "besylate", "maleate", "mesylate", "fumarate",
    # units / dosage forms
    "mg", "mcg", "g", "ml", "iu", "unit", "units", "tab", "tablet", "cap", "capsule", "syrup", "inhaler",
    "rotacap", "rotacaps", "respules", "drops", "cream", "ointment", "gel",
})


def _generic_tokens(s: str) -> list[str]:
    s = _STRENGTH.sub(" ", s)
    return [t for t in re.split(r"[^a-z]+", s) if t and t not in _DROP_TOKENS]


def generic_name(drug_name: str | None) -> str:
    """Normalised generic name: ``"Glycomet (Metformin) 500 mg"`` -> ``metformin``; ``"Tab Amaryl 2 mg"`` ->
    ``glimepiride`` (via ``BRAND_TO_GENERIC``); unknown names -> lowercase name without form/strength/modifiers."""
    s = (drug_name or "").lower().strip()
    tokens: list[str] = []
    m = re.search(r"\(([^)]*)\)", s)
    if m:
        tokens = _generic_tokens(m.group(1))
    if not tokens:
        tokens = _generic_tokens(_FORM_PREFIX.sub("", re.sub(r"\([^)]*\)", " ", s)))
    if not tokens:
        return s
    joined = " ".join(tokens)
    return BRAND_TO_GENERIC.get(joined) or BRAND_TO_GENERIC.get(tokens[0]) or joined


def parse_date(value) -> date | None:
    """ISO ``YYYY-MM-DD`` (also accepts a datetime prefix) or Indian ``dd/mm/yyyy`` / ``dd-mm-yyyy``."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", s)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        y = y + 2000 if y < 100 else y
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    return None


def _num(value) -> float | None:
    if value is None:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(m.group()) if m else None


def _flag(value) -> str | None:
    if not value:
        return None
    return _FLAGS.get(str(value).strip().lower())


def _clean(value) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


# ---------------------------------------------------------------------------
# Condition names (for de-duplication)
# ---------------------------------------------------------------------------

_CONDITION_SYNONYMS: dict[str, str] = {
    "hypertension": "hypertension", "essential hypertension": "hypertension", "htn": "hypertension",
    "primary hypertension": "hypertension", "systemic hypertension": "hypertension",
    "type 2 diabetes mellitus": "t2dm", "t2dm": "t2dm", "diabetes mellitus type 2": "t2dm",
    "type 2 diabetes": "t2dm", "type ii diabetes mellitus": "t2dm", "dm2": "t2dm", "t2 dm": "t2dm",
    "dyslipidaemia": "dyslipidaemia", "dyslipidemia": "dyslipidaemia", "hyperlipidaemia": "dyslipidaemia",
    "hyperlipidemia": "dyslipidaemia", "mixed dyslipidaemia": "dyslipidaemia", "mixed dyslipidemia": "dyslipidaemia",
    "cad": "cad", "coronary artery disease": "cad",
}
# Acute events recorded on a discharge summary are over by the time of discharge -> stored as resolved.
_ACUTE_EVENT_RE = re.compile(r"hypoglyc(?:a)?emi", re.IGNORECASE)


def condition_key(name: str | None) -> str:
    """Canonical key for de-duplicating conditions: lowercase, punctuation/extra spaces collapsed, then mapped
    through a small synonym table (``Essential hypertension`` / ``HTN`` -> ``hypertension``)."""
    s = re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()
    return _CONDITION_SYNONYMS.get(s, s)


def commit_extraction(db: Session, document: Document, result: ExtractionResult) -> dict[str, int]:
    """Insert rows for ``result`` (each with ``source_document_id``/``source_page``), store the final
    extraction on the document, set ``doc_type``/``document_date``/``status='committed'`` and commit.

    Rules: row dates default to the document date; labs/meds/vitals/conditions without a name are skipped;
    conditions already recorded for the patient (same name or a known synonym, see ``condition_key``) are not
    duplicated; acute events (hypoglycaemia) on a discharge summary are stored ``resolved``; medications
    are ``active`` only for ``PRESCRIBING_DOC_TYPES``; the encounter fee is stored as a float.
    Raises ``CommitError`` unless the document is (still) ``needs_review`` — checked with a conditional UPDATE,
    so it is race-safe. Everything happens in one transaction (rolled back on error). Returns counts per table.
    """
    pid, did = document.patient_id, document.id
    # Claim the document atomically (needs_review -> committed) so two concurrent commits can't both insert rows.
    claimed = db.execute(update(Document).where(Document.id == did, Document.status == "needs_review")
                         .values(status="committed").execution_options(synchronize_session=False)).rowcount
    if claimed != 1:
        db.rollback()
        current = db.scalar(select(Document.status).where(Document.id == did))
        raise CommitError("Document already committed" if current == "committed"
                          else f"Document is {current or 'missing'}; only documents in review can be committed")
    try:
        counts = _write_rows(db, document, result)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return counts


def _supersede(db: Session, document: Document, regimen_date: date, new_meds: list[Medication]) -> int:
    """Apply a prescribing document's medication list as the patient's current regimen. Returns rows deactivated.

    Evidence date of a medication row = its source document's date, falling back to its start_date (a continuing
    drug listed on a 2025 discharge often carries a 2019 start date, but the *evidence* is from 2025).

    * If any other active medication has evidence newer than this document, this document is historical: its
      rows are stored inactive (end_date = the next newer evidence date) and nothing else changes.
    * Otherwise older active meds whose generic is not in the new list are stopped (active=False,
      end_date=regimen_date), and same-generic active rows are de-duplicated keeping the newest evidence.
    Rows with evidence newer than this document are never deactivated.
    """
    pid, did = document.patient_id, document.id
    db.flush()  # sessions use autoflush=False; new rows need ids and must be visible to the query below
    src_dates = dict(db.execute(select(Document.id, Document.document_date).where(Document.patient_id == pid)).all())
    src_dates[did] = regimen_date

    def evidence(m: Medication) -> date | None:
        return src_dates.get(m.source_document_id) or m.start_date

    others = [m for m in db.scalars(select(Medication).where(Medication.patient_id == pid,
                                                              Medication.active.is_(True)))
              if m.source_document_id != did]
    newer = [evidence(m) for m in others if evidence(m) is not None and evidence(m) > regimen_date]
    if newer:
        until = min(newer)
        for m in new_meds:
            m.active = False
            m.end_date = m.end_date or until
        return 0

    superseded = 0
    new_generics = {generic_name(m.drug_name) for m in new_meds}
    for m in others:
        when = evidence(m)
        if generic_name(m.drug_name) not in new_generics and (when is None or when < regimen_date):
            m.active = False
            m.end_date = m.end_date or regimen_date
            superseded += 1

    groups: dict[str, list[Medication]] = {}
    for m in [*others, *new_meds]:
        if m.active:
            groups.setdefault(generic_name(m.drug_name), []).append(m)
    for rows in groups.values():
        if len(rows) < 2:
            continue
        rows.sort(key=lambda m: (evidence(m) or date.min, m.source_document_id == did, m.id or 0))
        keep = rows[-1]
        for m in rows[:-1]:
            m.active = False
            m.end_date = m.end_date or evidence(keep)
            superseded += 1
    return superseded


def _write_rows(db: Session, document: Document, result: ExtractionResult) -> dict[str, int]:
    pid, did = document.patient_id, document.id
    doc_date = parse_date(result.document_date)
    counts = {"conditions": 0, "medications": 0, "lab_results": 0, "vitals": 0, "encounters": 0,
              "superseded": 0}

    existing = {condition_key(n) for n in db.scalars(select(Condition.name).where(Condition.patient_id == pid))}
    for c in result.conditions:
        name = _clean(c.name)
        if not name or condition_key(name) in existing:
            continue
        existing.add(condition_key(name))
        status = (c.status or "active").lower()
        if result.doc_type == "discharge_summary" and _ACUTE_EVENT_RE.search(name):
            status = "resolved"
        db.add(Condition(patient_id=pid, name=name, diagnosed_date=parse_date(c.diagnosed_date) or doc_date,
                         severity=_clean(c.severity), status=status if status in ("active", "resolved") else "active",
                         source_document_id=did, source_page=c.page))
        counts["conditions"] += 1

    prescribing = result.doc_type in PRESCRIBING_DOC_TYPES
    new_meds: list[Medication] = []
    for m in result.medications:
        name = _clean(m.drug_name)
        if not name:
            continue
        med = Medication(patient_id=pid, drug_name=name, dosage=_clean(m.dosage), frequency=_clean(m.frequency),
                         route=_clean(m.route), duration=_clean(m.duration),
                         start_date=parse_date(m.start_date) or doc_date, prescriber=_clean(result.doctor_name),
                         active=prescribing, instructions=_clean(m.instructions), source_document_id=did,
                         source_page=m.page)
        db.add(med)
        new_meds.append(med)
        counts["medications"] += 1
    if prescribing and new_meds:
        regimen_date = doc_date or document.document_date or (document.uploaded_at or utcnow()).date()
        counts["superseded"] = _supersede(db, document, regimen_date, new_meds)

    for lab in result.lab_results:
        name = _clean(lab.test_name)
        if not name:
            continue
        value = _clean(lab.value)
        db.add(LabResult(patient_id=pid, test_name=name, value=value, value_num=_num(value), unit=_clean(lab.unit),
                         ref_range=_clean(lab.ref_range), flag=_flag(lab.flag),
                         date=parse_date(lab.date) or doc_date, lab_name=_clean(result.facility),
                         source_document_id=did, source_page=lab.page))
        counts["lab_results"] += 1

    for v in result.vitals:
        kind = (_clean(v.kind) or "").lower()
        if kind not in VITAL_KINDS or not _clean(v.value):
            continue
        value = _clean(v.value)
        db.add(Vital(patient_id=pid, kind=kind, value=value, value_num=None if kind == "bp" else _num(value),
                     unit=_clean(v.unit), date=parse_date(v.date) or doc_date, source_document_id=did,
                     source_page=v.page))
        counts["vitals"] += 1

    e = result.encounter
    if e is not None:
        etype = (e.type or "").lower()
        if etype not in ENCOUNTER_TYPES:
            etype = _ENCOUNTER_FOR_DOC_TYPE.get(result.doc_type, "opd")
        db.add(Encounter(patient_id=pid, type=etype, date=parse_date(e.date) or doc_date,
                         end_date=parse_date(e.end_date), facility=_clean(result.facility),
                         doctor_name=_clean(result.doctor_name), specialization=_clean(result.specialization),
                         reason=_clean(e.reason), diagnosis=_clean(e.diagnosis), notes=_clean(e.notes),
                         advice=_clean(e.advice), follow_up_date=parse_date(e.follow_up_date),
                         fee=float(e.fee) if e.fee is not None else None, source_document_id=did,
                         source_page=e.page))
        counts["encounters"] += 1

    document.extraction = result.model_dump(mode="json")
    document.doc_type = result.doc_type or "other"
    document.document_date = doc_date or document.document_date
    document.status = "committed"  # already set in the DB by the claim; keep the ORM object consistent
    document.error = None
    return counts
