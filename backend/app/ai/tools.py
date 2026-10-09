"""Retrieval tools for doctor Q&A (spec section 7).

Pure functions over ``(db, patient_id, **filters)``. Every row dict carries ``source_document_id``,
``source_page`` and ``source_filename`` so answers can cite their source. All filtering on the patient id
happens here, so the model can never reach another patient's rows (no tool takes a patient id argument).
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Condition,
    Document,
    Encounter,
    LabResult,
    Medication,
    User,
    Vital,
    profile_to_dict,
    rows_with_sources,
)
from app.schemas import Citation

# ---------------------------------------------------------------------------
# Lab name matching
# ---------------------------------------------------------------------------

LAB_ALIASES: dict[str, tuple[str, ...]] = {
    "a1c": ("HbA1c",),
    "hba1c": ("HbA1c",),
    "glycated": ("HbA1c",),
    "sugar": ("FBS", "PPBS", "HbA1c", "RBS"),
    "glucose": ("FBS", "PPBS", "HbA1c", "RBS"),
    "cholesterol": ("Total Cholesterol", "LDL", "HDL", "Triglycerides"),
    "lipid": ("Total Cholesterol", "LDL", "HDL", "Triglycerides"),
    "kidney": ("Creatinine", "Urea", "eGFR"),
    "renal": ("Creatinine", "Urea", "eGFR"),
    "thyroid": ("TSH", "T3", "T4"),
}


def alias_targets(text: str) -> set[str]:
    """Canonical test names implied by alias words appearing anywhere in ``text`` (case-insensitive)."""
    low = (text or "").lower()
    out: set[str] = set()
    for key, names in LAB_ALIASES.items():
        if key in low:
            out.update(names)
    return out


def match_test_names(query: str, available: list[str]) -> list[str]:
    """Resolve a tool ``test_name`` filter to concrete stored names: aliases, else case-insensitive substring."""
    q = (query or "").strip().lower()
    if not q:
        return list(available)
    targets = {t.lower() for t in alias_targets(q)}
    if targets:
        return [a for a in available if a.lower() in targets]
    return [a for a in available if q in a.lower()]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_date(value: Any, end: bool = False) -> date | None:
    """Accept ``YYYY-MM-DD``, ``YYYY-MM`` or ``YYYY`` (or a date). Partial dates expand to the period's
    start (or end when ``end``). Raises ValueError on anything else."""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    try:
        if re.fullmatch(r"\d{4}", s):
            return date(int(s), 12, 31) if end else date(int(s), 1, 1)
        if re.fullmatch(r"\d{4}-\d{2}", s):
            y, m = int(s[:4]), int(s[5:])
            if end:
                nxt = date(y + (m == 12), m % 12 + 1, 1)
                return date.fromordinal(nxt.toordinal() - 1)
            return date(y, m, 1)
        return date.fromisoformat(s[:10])
    except ValueError:
        raise ValueError(f"Invalid date '{value}'; use YYYY-MM-DD") from None


def _date_filter(stmt, column, date_from, date_to):
    lo, hi = _parse_date(date_from), _parse_date(date_to, end=True)
    if lo:
        stmt = stmt.where(column >= lo)
    if hi:
        stmt = stmt.where(column <= hi)
    return stmt


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

_SUMMARY_PRIVATE_KEYS = ("user_id", "email", "insurance", "phone", "address", "emergency_contact",
                         "family_members", "dob", "occupation")


def get_patient_summary(db: Session, patient_id: int) -> dict:
    user = db.get(User, patient_id)
    if user is None or user.role != "patient":
        return {"patient": None, "active_conditions": [], "active_medications": [], "latest_vitals": [],
                "counts": {}}
    patient = profile_to_dict(user)
    # Contact details and identifiers are not needed clinically and are never sent to the model (age is kept).
    for private in _SUMMARY_PRIVATE_KEYS:
        patient.pop(private, None)
    latest: dict[str, Vital] = {}
    for v in db.scalars(select(Vital).where(Vital.patient_id == patient_id)
                        .order_by(Vital.date.desc().nulls_last(), Vital.id.desc())):
        latest.setdefault(v.kind, v)
    counts = {}
    for key, model in (("documents", Document), ("encounters", Encounter), ("lab_results", LabResult),
                       ("medications", Medication), ("conditions", Condition), ("vitals", Vital)):
        counts[key] = db.scalar(select(func.count()).select_from(model).where(model.patient_id == patient_id))
    return {
        "patient": patient,
        "active_conditions": list_conditions(db, patient_id, status="active"),
        "active_medications": list_medications(db, patient_id, active_only=True),
        "latest_vitals": rows_with_sources(db, latest.values()),
        "counts": counts,
    }


def list_conditions(db: Session, patient_id: int, status: str | None = None) -> list[dict]:
    stmt = select(Condition).where(Condition.patient_id == patient_id)
    if status:
        stmt = stmt.where(func.lower(Condition.status) == status.strip().lower())
    stmt = stmt.order_by(Condition.diagnosed_date.asc().nulls_last(), Condition.id)
    return rows_with_sources(db, db.scalars(stmt).all())


def list_medications(db: Session, patient_id: int, active_only: bool = True) -> list[dict]:
    stmt = select(Medication).where(Medication.patient_id == patient_id)
    if active_only:
        stmt = stmt.where(Medication.active.is_(True))
    stmt = stmt.order_by(Medication.active.desc(), Medication.start_date.desc().nulls_last(), Medication.id)
    return rows_with_sources(db, db.scalars(stmt).all())


def get_lab_results(db: Session, patient_id: int, test_name: str | None = None, date_from: str | None = None,
                    date_to: str | None = None, abnormal_only: bool = False) -> list[dict]:
    """Lab rows ascending by date (oldest first). ``abnormal_only`` keeps rows flagged high/low."""
    stmt = select(LabResult).where(LabResult.patient_id == patient_id)
    if abnormal_only:
        stmt = stmt.where(func.lower(LabResult.flag).in_(("high", "low")))
    stmt = _date_filter(stmt, LabResult.date, date_from, date_to)
    if test_name and test_name.strip():
        available = list(db.scalars(select(LabResult.test_name).where(LabResult.patient_id == patient_id)
                                    .distinct()).all())
        names = match_test_names(test_name, available)
        if not names:
            return []
        stmt = stmt.where(LabResult.test_name.in_(names))
    stmt = stmt.order_by(LabResult.date.asc().nulls_last(), LabResult.test_name, LabResult.id)
    return rows_with_sources(db, db.scalars(stmt).all())


def get_vitals(db: Session, patient_id: int, kind: str | None = None, date_from: str | None = None,
               date_to: str | None = None) -> list[dict]:
    stmt = select(Vital).where(Vital.patient_id == patient_id)
    if kind and kind.strip():
        stmt = stmt.where(func.lower(Vital.kind) == kind.strip().lower())
    stmt = _date_filter(stmt, Vital.date, date_from, date_to)
    stmt = stmt.order_by(Vital.date.asc().nulls_last(), Vital.kind, Vital.id)
    return rows_with_sources(db, db.scalars(stmt).all())


def list_encounters(db: Session, patient_id: int, type: str | None = None, date_from: str | None = None,
                    date_to: str | None = None) -> list[dict]:
    stmt = select(Encounter).where(Encounter.patient_id == patient_id)
    if type and type.strip():
        stmt = stmt.where(func.lower(Encounter.type) == type.strip().lower())
    stmt = _date_filter(stmt, Encounter.date, date_from, date_to)
    stmt = stmt.order_by(Encounter.date.asc().nulls_last(), Encounter.id)
    return rows_with_sources(db, db.scalars(stmt).all())


# ---- document search -------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_PAGE_RE = re.compile(r"\[Page\s+(\d+)\]", re.IGNORECASE)
_STOP = {"a", "an", "and", "are", "any", "as", "at", "be", "by", "did", "do", "does", "for", "from", "has",
         "have", "her", "his", "how", "in", "is", "it", "me", "of", "on", "or", "she", "he", "show", "the",
         "there", "this", "to", "was", "were", "what", "when", "which", "who", "with", "about", "tell",
         "patient", "give", "list", "all", "their", "them", "any"}


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 2 and t not in _STOP]


def _split_pages(text: str) -> list[tuple[int | None, str]]:
    """``[(page_no, page_text)]`` from ``[Page n]`` markers; whole text as page None when there are none."""
    marks = list(_PAGE_RE.finditer(text or ""))
    if not marks:
        return [(None, text or "")]
    pages = []
    if marks[0].start() > 0 and (text[:marks[0].start()]).strip():
        pages.append((int(marks[0].group(1)), text[:marks[0].start()]))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        pages.append((int(m.group(1)), text[m.end():end]))
    return pages


def _snippet(page_text: str, terms: set[str], width: int = 240) -> str:
    flat = re.sub(r"\s+", " ", page_text).strip()
    low = flat.lower()
    pos = -1
    for t in sorted(terms, key=len, reverse=True):
        m = re.search(rf"(?<![a-z0-9]){re.escape(t)}", low)
        if m and (pos < 0 or m.start() < pos):
            pos = m.start()
    start = max(0, pos - width // 3) if pos >= 0 else 0
    out = flat[start:start + width].strip()
    return ("…" if start > 0 else "") + out + ("…" if start + width < len(flat) else "")


def search_documents(db: Session, patient_id: int, query: str, limit: int = 5) -> list[dict]:
    """Keyword (token-overlap) search over a patient's document text, summary and filename. Returns one hit per
    document (its best page) with ``snippet`` and ``score``, best first."""
    q_terms = set(_tokens(query))
    if not q_terms:
        return []
    limit = max(1, min(int(limit or 5), 20))
    docs = db.scalars(select(Document).where(Document.patient_id == patient_id,
                                             Document.status != "failed")).all()
    hits = []
    for doc in docs:
        summary = (doc.extraction or {}).get("summary") or ""
        # Title-like fields (filename, doc type) weigh more than the summary; whole-document term frequency
        # adds a small boost so multi-page documents aren't penalised for spreading terms across pages.
        title_tokens = set(_tokens(f"{re.sub(r'[_.-]+', ' ', doc.filename)} {(doc.doc_type or '').replace('_', ' ')}"))
        doc_counts = Counter(_tokens(doc.extracted_text or ""))
        meta_score = (2.0 * len(q_terms & title_tokens) + 1.0 * len(q_terms & set(_tokens(summary)))
                      + 0.5 * sum(math.log1p(doc_counts[t]) for t in q_terms if doc_counts.get(t)))
        best = None
        for page_no, page_text in _split_pages(doc.extracted_text or ""):
            counts = Counter(_tokens(page_text))
            matched = {t for t in q_terms if counts.get(t)}
            if not matched and not meta_score:
                continue
            score = 3.0 * len(matched) + sum(math.log1p(counts[t]) for t in matched) + meta_score
            if best is None or score > best[0]:
                best = (score, page_no, page_text, matched)
        if best is None or best[0] <= 0:
            continue
        score, page_no, page_text, matched = best
        hits.append({
            "document_id": doc.id, "filename": doc.filename, "doc_type": doc.doc_type,
            "document_date": doc.document_date.isoformat() if doc.document_date else None,
            "summary": summary or None, "snippet": _snippet(page_text, matched or q_terms) or summary[:240],
            "score": round(score, 3), "source_document_id": doc.id, "source_page": page_no,
            "source_filename": doc.filename,
        })
    hits.sort(key=lambda h: (-h["score"], h["document_date"] or "", h["document_id"]))
    return hits[:limit]


# ---------------------------------------------------------------------------
# Tool registry shared by both providers
# ---------------------------------------------------------------------------

_DATE_PARAMS = {
    "date_from": {"type": "string", "description": "Inclusive start date, YYYY-MM-DD (YYYY-MM or YYYY ok)."},
    "date_to": {"type": "string", "description": "Inclusive end date, YYYY-MM-DD (YYYY-MM or YYYY ok)."},
}

TOOL_SPECS: list[dict] = [
    {"name": "get_patient_summary", "func": get_patient_summary,
     "description": "Demographics (age, sex, blood group, BMI), allergies, hereditary history, active "
                    "conditions, active medications, latest vitals and record counts for this patient.",
     "params": {}},
    {"name": "list_conditions", "func": list_conditions,
     "description": "Diagnosed conditions with diagnosis date, severity and status.",
     "params": {"status": {"type": "string", "enum": ["active", "resolved"],
                           "description": "Filter by status; omit for all."}}},
    {"name": "list_medications", "func": list_medications,
     "description": "Medications (brand and generic name, dose, frequency, start/end, prescriber).",
     "params": {"active_only": {"type": "boolean", "description": "Only current medications (default true). "
                                "Set false for the full medication history."}}},
    {"name": "get_lab_results", "func": get_lab_results,
     "description": "Lab results ordered oldest to newest. test_name is fuzzy: aliases like 'sugar' (FBS, PPBS, "
                    "HbA1c, RBS), 'a1c', 'cholesterol'/'lipid', 'kidney'/'renal', 'thyroid', otherwise "
                    "case-insensitive substring. Omit test_name for all labs.",
     "params": {"test_name": {"type": "string", "description": "Test name or alias, e.g. 'HbA1c', 'LDL'."},
                **_DATE_PARAMS,
                "abnormal_only": {"type": "boolean", "description": "Only results flagged high or low."}}},
    {"name": "get_vitals", "func": get_vitals,
     "description": "Vital signs ordered oldest to newest.",
     "params": {"kind": {"type": "string", "enum": ["bp", "weight", "glucose", "pulse", "spo2", "temp"],
                         "description": "Vital kind; omit for all."}, **_DATE_PARAMS}},
    {"name": "list_encounters", "func": list_encounters,
     "description": "Clinical encounters (OPD visits, IPD admissions, diagnostics, pharmacy) with facility, "
                    "doctor, reason, diagnosis, notes, advice, follow-up and fee (INR), oldest first.",
     "params": {"type": {"type": "string", "enum": ["opd", "ipd", "diagnostic", "pharmacy"],
                         "description": "Encounter type; omit for all."}, **_DATE_PARAMS}},
    {"name": "search_documents", "func": search_documents,
     "description": "Keyword search over the full text of the patient's uploaded documents (discharge "
                    "summaries, prescriptions, reports, bills). Returns the best-matching page per document "
                    "with a snippet. Use for narrative details not in the structured records.",
     "params": {"query": {"type": "string", "description": "Keywords to search for.", "required": True},
                "limit": {"type": "integer", "description": "Max documents (default 5, max 20)."}}},
]

_SPECS_BY_NAME = {s["name"]: s for s in TOOL_SPECS}


def input_schema(spec: dict) -> dict:
    """JSON schema for a tool spec's params (used for the Claude tool definitions)."""
    props, required = {}, []
    for pname, p in spec["params"].items():
        props[pname] = {k: v for k, v in p.items() if k != "required"}
        if p.get("required"):
            required.append(pname)
    schema: dict = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def run_tool(db: Session, patient_id: int, name: str, args: dict | None) -> list | dict:
    """Dispatch a tool call. Unknown tools or unexpected arguments raise ValueError (e.g. a ``patient_id``
    argument is rejected: the patient is always bound by the server)."""
    spec = _SPECS_BY_NAME.get(name)
    if spec is None:
        raise ValueError(f"Unknown tool '{name}'")
    args = {k: v for k, v in (args or {}).items() if v is not None}
    unknown = set(args) - set(spec["params"])
    if unknown:
        raise ValueError(f"Unexpected argument(s) for {name}: {', '.join(sorted(unknown))}")
    missing = [p for p, d in spec["params"].items() if d.get("required") and p not in args]
    if missing:
        raise ValueError(f"Missing required argument(s) for {name}: {', '.join(missing)}")
    return spec["func"](db, patient_id, **args)


MAX_MODEL_ROWS = 200
MAX_MODEL_CHARS = 50_000
_MODEL_HIDDEN_KEYS = ("patient_id", "created_at")


def _strip(x):
    if isinstance(x, dict):
        return {k: _strip(v) for k, v in x.items() if k not in _MODEL_HIDDEN_KEYS}
    if isinstance(x, list):
        return [_strip(v) for v in x]
    return x


def _dumps(x) -> str:
    return json.dumps(x, default=str, ensure_ascii=False)


def serialise_for_model(result: list | dict) -> tuple[str, list[dict]]:
    """JSON for a tool result as sent to the model, plus the rows the model actually sees (for citations).

    Drops ``patient_id`` / ``created_at``. A list longer than ``MAX_MODEL_ROWS`` rows, or whose JSON exceeds
    ``MAX_MODEL_CHARS``, becomes ``{"rows": [...], "truncated": true, "total": N, "hint": ...}``."""
    clean = _strip(result)
    if isinstance(clean, list):
        text = _dumps(clean)
        if len(clean) <= MAX_MODEL_ROWS and len(text) <= MAX_MODEL_CHARS:
            return text, flatten_rows(clean)
        total = len(clean)
        keep = min(total, MAX_MODEL_ROWS)
        while True:
            payload = {"rows": clean[:keep], "truncated": True, "total": total,
                       "hint": f"Showing {keep} of {total} rows. Narrow the query with filters "
                               "(test_name, kind, type, date_from/date_to) to see the rest."}
            text = _dumps(payload)
            if len(text) <= MAX_MODEL_CHARS or keep <= 1:
                return text, flatten_rows(clean[:keep])
            keep = max(1, int(keep * MAX_MODEL_CHARS / len(text) * 0.9))
    text = _dumps(clean)
    if len(text) > MAX_MODEL_CHARS:  # summary dicts are small; cap defensively
        text = _dumps({"truncated": True, "partial_json": text[:MAX_MODEL_CHARS - 200]})
        return text, []
    return text, flatten_rows(clean)


def flatten_rows(result: list | dict) -> list[dict]:
    """All row dicts with a ``source_document_id`` key in a tool result (summary dicts are nested)."""
    out: list[dict] = []

    def walk(x):
        if isinstance(x, dict):
            if "source_document_id" in x:
                out.append(x)
            else:
                for v in x.values():
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(result)
    return out


def row_count(result: list | dict) -> int:
    if isinstance(result, list):
        return len(result)
    return len(flatten_rows(result)) + (1 if isinstance(result, dict) and result.get("patient") else 0)


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------

_BRACKET_RE = re.compile(r"\[([^\[\]]*doc:[^\[\]]*)\]", re.IGNORECASE)
_REF_RE = re.compile(r"doc:\s*(\d+)(?:\s*,?\s*(?:p|pg|page)\.?\s*(\d+))?", re.IGNORECASE)


def describe_row(row: dict) -> str:
    """Short human snippet for a cited row."""
    if row.get("snippet"):
        return row["snippet"]
    if "test_name" in row:
        s = f"{row['test_name']} {row.get('value') or ''} {row.get('unit') or ''}".strip()
    elif "drug_name" in row:
        s = " ".join(str(x) for x in (row["drug_name"], row.get("dosage"), row.get("frequency")) if x)
    elif "kind" in row and "value" in row:
        s = f"{row['kind']} {row.get('value') or ''} {row.get('unit') or ''}".strip()
    elif "diagnosis" in row or "reason" in row:
        s = f"{(row.get('type') or '').upper()} {row.get('diagnosis') or row.get('reason') or ''}".strip()
    elif "name" in row:
        s = row["name"]
    else:
        s = ""
    d = row.get("date") or row.get("start_date") or row.get("diagnosed_date")
    return f"{s} ({d})" if d and s else s


def build_citations(db: Session, patient_id: int, refs: list[tuple[int, int | None]],
                    rows: list[dict]) -> list[Citation]:
    """Citations for ``(doc_id, page)`` refs, keeping only documents owned by ``patient_id``."""
    ids = {doc_id for doc_id, _ in refs}
    if not ids:
        return []
    docs = {d.id: d for d in db.scalars(select(Document).where(Document.id.in_(ids),
                                                               Document.patient_id == patient_id))}
    by_doc: dict[int, list[dict]] = {}
    for r in rows:
        if r.get("source_document_id"):
            by_doc.setdefault(r["source_document_id"], []).append(r)
    out: list[Citation] = []
    seen: set[tuple[int, int | None]] = set()
    for doc_id, page in refs:
        doc = docs.get(doc_id)
        if doc is None:
            continue
        candidates = by_doc.get(doc_id, [])
        match = next((r for r in candidates if page is not None and r.get("source_page") == page), None)
        match = match or (candidates[0] if candidates else None)
        if page is None and match is not None:
            page = match.get("source_page")
        if (doc_id, page) in seen:
            continue
        seen.add((doc_id, page))
        snippet = describe_row(match) if match else ((doc.extraction or {}).get("summary") or None)
        out.append(Citation(document_id=doc_id, filename=doc.filename, page=page, snippet=snippet or None))
    return out


MAX_CITED_DOCUMENTS = 50


def resolve_citations(db: Session, patient_id: int, text: str, rows: list[dict],
                      restrict_to_rows: bool = False, max_documents: int = MAX_CITED_DOCUMENTS) -> list[Citation]:
    """Parse ``[doc:ID]`` / ``[doc:ID p N]`` markers in ``text`` and resolve them to this patient's documents.
    Unknown ids and other patients' documents are always dropped; with ``restrict_to_rows`` only documents that
    back one of ``rows`` (what the model actually retrieved) are kept. At most ``max_documents`` distinct
    documents are cited (first-mentioned first)."""
    allowed = {r.get("source_document_id") for r in rows} if restrict_to_rows else None
    refs: list[tuple[int, int | None]] = []
    doc_ids: set[int] = set()
    for bracket in _BRACKET_RE.finditer(text or ""):
        for m in _REF_RE.finditer(bracket.group(1)):
            doc_id = int(m.group(1))
            if allowed is not None and doc_id not in allowed:
                continue
            if doc_id not in doc_ids:
                if len(doc_ids) >= max_documents:
                    continue
                doc_ids.add(doc_id)
            refs.append((doc_id, int(m.group(2)) if m.group(2) else None))
    return build_citations(db, patient_id, refs, rows)


def citations_from_rows(db: Session, patient_id: int, rows: list[dict], limit: int = 10) -> list[Citation]:
    refs, seen = [], set()
    for r in rows:
        key = (r.get("source_document_id"), r.get("source_page"))
        if key[0] and key not in seen:
            seen.add(key)
            refs.append(key)
    return build_citations(db, patient_id, refs[:limit], rows)
