"""OfflineProvider: no-API fallback. Digitise via regex/OCR (Task 2's offline_extract); answer via a keyword
intent router over the retrieval tools, rendered as a markdown table with citations (spec sections 5 and 7)."""
from __future__ import annotations

import datetime as dt
import logging
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import tools
from app.db import utcnow
from app.models import LabResult
from app.schemas import AskResponse, ExtractedContent, ExtractionResult, ToolCallInfo

log = logging.getLogger(__name__)

OFFLINE_BANNER = "_Offline mode — set ANTHROPIC_API_KEY for AI answers._"
MAX_TABLE_ROWS = 25

_MED_WORDS = ("medication", "medicine", "meds", "drug", "prescri", "tablet", "pills", "taking", " rx ")
_MED_HISTORY_RE = re.compile(r"\b(?:past|previous|history|all|stopped|ever|earlier|old|former|discontinued)\b")
_LAB_RE = re.compile(r"\b(?:labs?|trends?|levels?|test results?|investigations?|report values?)\b")
_ABNORMAL_RE = re.compile(r"\b(?:abnormal\w*|high|low|out[ -]of[ -]range|deranged|elevated|raised)\b")
_DIABETES_RE = re.compile(r"\b(?:diabet\w*|glycaemic|glycemic|glyc(?!omet|ated)\w*)")
_DIABETES_TESTS = ("FBS", "PPBS", "HbA1c")
_VITAL_KINDS = {"bp": ("bp", "blood pressure", "hypertens"), "weight": ("weight", "bmi"),
                "pulse": ("pulse", "heart rate"), "spo2": ("spo2", "oxygen", "saturation"),
                "temp": ("temperature", "fever")}
_ENCOUNTER_WORDS = {"ipd": ("admission", "admitted", "discharge", "hospitali", "ipd", "inpatient"),
                    "opd": ("visit", "opd", "consult", "appointment"),
                    "pharmacy": ("pharmacy", "bill", "purchase"),
                    "diagnostic": ("diagnostic",)}
_CONDITION_WORDS = ("condition", "diagnos", "problem", "disease", "comorbid")
_SUMMARY_WORDS = ("allerg", "summary", "summaris", "summariz", "overview", "profile", "demograph", "blood group", "about the patient")


def _has(q: str, words) -> bool:
    return any(w in q for w in words)


def _cell(value) -> str:
    if value is None or value == "":
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _src(row: dict) -> str:
    doc = row.get("source_document_id")
    if not doc:
        return "—"
    page = row.get("source_page")
    return f"[doc:{doc} p {page}]" if page else f"[doc:{doc}]"


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _newest_first(rows: list[dict], key: str = "date") -> list[dict]:
    return sorted(rows, key=lambda r: r.get(key) or "", reverse=True)


def _fmt_value(row: dict) -> str:
    return " ".join(str(x) for x in (row.get("value"), row.get("unit")) if x)


def _trend_line(rows: list[dict]) -> str:
    """One line per test: first -> last value with direction (rows are any order)."""
    by_test: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("value_num") is not None:
            by_test.setdefault(r["test_name"], []).append(r)
    parts = []
    for name, rs in by_test.items():
        rs = sorted(rs, key=lambda r: r.get("date") or "")
        first, last = rs[0], rs[-1]
        if len(rs) == 1:
            parts.append(f"{name} {_fmt_value(last)} ({last.get('date') or 'undated'})")
            continue
        a, b = first["value_num"], last["value_num"]
        direction = "stable" if abs(b - a) <= 0.02 * max(abs(a), 1e-9) else ("rising" if b > a else "falling")
        parts.append(f"{name} {_fmt_value(first)} ({first.get('date')}) → {_fmt_value(last)} "
                     f"({last.get('date')}), {direction} over {len(rs)} results")
    return "; ".join(parts)


# ---- renderers ---------------------------------------------------------------

def _render_meds(rows, active_only):
    title = "Current medications" if active_only else "Medication history"
    rows = rows[:MAX_TABLE_ROWS]
    table = _table(["Drug", "Dose", "Frequency", "Since", "Status", "Prescriber", "Source"],
                   [[r["drug_name"], r.get("dosage"), r.get("frequency"), r.get("start_date"),
                     "active" if r.get("active") else f"stopped {r.get('end_date') or ''}".strip(),
                     r.get("prescriber"), _src(r)] for r in rows])
    n_active = sum(1 for r in rows if r.get("active"))
    return title, table, f"{len(rows)} medication(s) listed, {n_active} active."


def _render_labs(rows, _):
    shown = _newest_first(rows)[:MAX_TABLE_ROWS]
    table = _table(["Date", "Test", "Value", "Ref range", "Flag", "Source"],
                   [[r.get("date"), r["test_name"], _fmt_value(r), r.get("ref_range"), r.get("flag"), _src(r)]
                    for r in shown])
    names = sorted({r["test_name"] for r in rows})
    return "Lab results — " + ", ".join(names), table, _trend_line(rows)


def _render_vitals(rows, _):
    shown = _newest_first(rows)[:MAX_TABLE_ROWS]
    table = _table(["Date", "Vital", "Value", "Source"],
                   [[r.get("date"), r["kind"], _fmt_value(r), _src(r)] for r in shown])
    latest = shown[0]
    return "Vitals", table, f"{len(rows)} reading(s); latest {latest['kind']} {_fmt_value(latest)} " \
                            f"on {latest.get('date')}."


def _render_encounters(rows, _):
    shown = _newest_first(rows)[:MAX_TABLE_ROWS]
    table = _table(["Date", "Type", "Facility", "Doctor", "Diagnosis / reason", "Source"],
                   [[r.get("date") + (f" → {r['end_date']}" if r.get("end_date") else "") if r.get("date") else None,
                     (r.get("type") or "").upper(), r.get("facility"), r.get("doctor_name"),
                     r.get("diagnosis") or r.get("reason"), _src(r)] for r in shown])
    return "Encounters", table, f"{len(rows)} encounter(s); most recent on {shown[0].get('date')}."


def _render_conditions(rows, _):
    table = _table(["Condition", "Diagnosed", "Severity", "Status", "Source"],
                   [[r["name"], r.get("diagnosed_date"), r.get("severity"), r.get("status"), _src(r)]
                    for r in rows[:MAX_TABLE_ROWS]])
    n_active = sum(1 for r in rows if r.get("status") == "active")
    return "Conditions", table, f"{len(rows)} condition(s), {n_active} active."


def _render_search(rows, query):
    table = _table(["Document", "Date", "Page", "Snippet", "Source"],
                   [[r["filename"], r.get("document_date"), r.get("source_page"), r.get("snippet"), _src(r)]
                    for r in rows])
    return f"Documents matching “{query}”", table, f"{len(rows)} matching document(s), best match first."


def _render_summary(s: dict, _):
    p = s.get("patient") or {}
    hered = ", ".join(f"{h.get('relation')}: {h.get('condition')}" for h in (p.get("hereditary_history") or []))
    lines = [
        f"- **Patient:** {p.get('full_name')} ({p.get('dhid')}), {p.get('age') or '?'} y, {p.get('sex') or '?'}, "
        f"blood group {p.get('blood_group') or '?'}, BMI {p.get('bmi') or '?'}",
        f"- **Allergies:** {', '.join(p.get('allergies') or []) or 'none recorded'}",
        f"- **Hereditary history:** {hered or 'none recorded'}",
        "- **Active conditions:** " + ("; ".join(f"{c['name']} {_src(c)}" for c in s["active_conditions"])
                                       or "none recorded"),
        "- **Active medications:** " + ("; ".join(
            f"{m['drug_name']} {m.get('dosage') or ''} {m.get('frequency') or ''}".strip() + f" {_src(m)}"
            for m in s["active_medications"]) or "none recorded"),
        "- **Latest vitals:** " + ("; ".join(f"{v['kind']} {_fmt_value(v)} ({v.get('date')}) {_src(v)}"
                                            for v in s["latest_vitals"]) or "none recorded"),
    ]
    c = s.get("counts") or {}
    summary = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in c.items()) + " on file."
    return "Patient summary", "\n".join(lines), summary


_RENDERERS = {"list_medications": _render_meds, "get_lab_results": _render_labs, "get_vitals": _render_vitals,
              "list_encounters": _render_encounters, "list_conditions": _render_conditions,
              "search_documents": _render_search, "get_patient_summary": _render_summary}


# ---- provider ------------------------------------------------------------------

class OfflineProvider:
    """Deterministic provider used when no ANTHROPIC_API_KEY is configured (and as the Claude fallback)."""

    def __init__(self, name: str = "offline") -> None:
        self.name = name

    # -- digitise ---------------------------------------------------------------
    def digitise(self, content: ExtractedContent, filename: str) -> ExtractionResult:
        try:
            from app.ai.offline_extract import offline_digitise
        except ImportError as exc:
            log.warning("offline extractor unavailable: %s", exc)
            return ExtractionResult(warnings=["offline extractor unavailable"])
        return offline_digitise(content, filename)

    # -- answer -----------------------------------------------------------------
    def _plan(self, db: Session, patient_id: int, question: str):
        """``(calls, window)``: the tool calls to make and the parsed relative window (or None)."""
        q = " " + question.lower() + " "
        window = _parse_window(q)
        since = {"date_from": _minus(utcnow().date(), *window).isoformat()} if window else {}
        if _has(q, _MED_WORDS):
            return [("list_medications", {"active_only": not _MED_HISTORY_RE.search(q)})], None
        abnormal = {"abnormal_only": True} if _ABNORMAL_RE.search(q) else {}
        available = list(db.scalars(select(LabResult.test_name).where(LabResult.patient_id == patient_id)
                                    .distinct()).all())
        aliases = tools.alias_targets(q)
        if _DIABETES_RE.search(q):
            aliases |= set(_DIABETES_TESTS)
        names = {n for n in aliases if n in available}
        names |= {n for n in available if re.search(rf"(?<![a-z0-9]){re.escape(n.lower())}(?![a-z0-9])", q)}
        if names:
            return [("get_lab_results", {"test_name": n, **since, **abnormal}) for n in sorted(names)], window
        if aliases:  # a lab was named but this patient has none of it: report that, don't list other labs
            return [("get_lab_results", {"test_name": sorted(aliases)[0], **since, **abnormal})], window
        for kind, words in _VITAL_KINDS.items():
            if _has(q, words):
                return [("get_vitals", {"kind": kind, **since})], window
        if _has(q, ("vital",)):
            return [("get_vitals", {**since})], window
        for etype, words in _ENCOUNTER_WORDS.items():
            if _has(q, words):
                return [("list_encounters", {"type": etype, **since})], window
        if _has(q, ("encounter",)):
            return [("list_encounters", {**since})], window
        if _LAB_RE.search(q) or abnormal:
            return [("get_lab_results", {**since, **abnormal})], window
        if _has(q, _CONDITION_WORDS):
            return [("list_conditions", {})], None
        if _has(q, _SUMMARY_WORDS):
            return [("get_patient_summary", {})], None
        return [("search_documents", {"query": question})], None

    def _run(self, db, patient_id, plan, calls) -> list | dict:
        results = []
        for name, args in plan:
            res = tools.run_tool(db, patient_id, name, args)
            calls.append(ToolCallInfo(name=name, args=args, row_count=tools.row_count(res)))
            results.append(res)
        if plan[0][0] == "get_lab_results":
            return [r for res in results for r in res]
        return results[0]

    def answer(self, db: Session, patient_id: int, question: str, history: list[dict]) -> AskResponse:
        plan, window = self._plan(db, patient_id, question)
        calls: list[ToolCallInfo] = []
        name = plan[0][0]
        result = self._run(db, patient_id, plan, calls)
        notes: list[str] = []
        if not result and window:
            # Nothing in the window ending today (demo records are historical): anchor the same window on the
            # patient's latest matching record instead, and say so.
            unbounded = [(n, {k: v for k, v in a.items() if k != "date_from"}) for n, a in plan]
            every = self._run(db, patient_id, unbounded, calls)
            dates = [r.get("date") for r in every if r.get("date")]
            if dates:
                latest = max(dates)
                start = _minus(dt.date.fromisoformat(latest), *window).isoformat()
                result = [r for r in every if (r.get("date") or "") >= start]
                n, unit = window
                notes.append(f"No results in the last {n} {unit}(s) up to today ({plan[0][1]['date_from']} "
                             f"onwards); showing the {n} {unit}(s) up to the latest record, {latest} "
                             f"(from {start}).")
        if not result and name != "search_documents":
            # nothing structured matched: try the documents' full text before giving up
            name = "search_documents"
            result = tools.run_tool(db, patient_id, name, {"query": question})
            calls.append(ToolCallInfo(name=name, args={"query": question}, row_count=len(result)))

        if not result:
            body = (f"### No matching records\n\nNo matching records were found in this patient's vault for "
                    f"“{question}”.")
            return AskResponse(answer=f"{body}\n\n{OFFLINE_BANNER}", citations=[], tool_calls=calls,
                               mode="offline")

        arg = plan[0][1].get("active_only", True) if name == "list_medications" else question
        title, body, summary = _RENDERERS[name](result, arg)
        if name == "get_lab_results" and plan[0][1].get("abnormal_only"):
            title = "Abnormal " + title[0].lower() + title[1:]
        if name == "list_medications" and "interact" in question.lower():
            notes.append("Drug-interaction checking needs AI mode; review the active list above with a "
                         "clinical interaction checker.")
        rows = tools.flatten_rows(result)
        extra = "".join(f"\n\n_{n}_" for n in notes)
        answer = f"### {title}\n\n{body}\n\n**Summary:** {summary}{extra}\n\n{OFFLINE_BANNER}"
        # Cite every document referenced by a marker in the answer (not just the first rows retrieved).
        citations = (tools.resolve_citations(db, patient_id, answer, rows)
                     or tools.citations_from_rows(db, patient_id, rows))
        return AskResponse(answer=answer, citations=citations, tool_calls=calls, mode="offline")


_NUM_WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
              "eight": 8, "nine": 9, "ten": 10, "twelve": 12, "eighteen": 18, "twenty": 20}
_WINDOW_RE = re.compile(
    r"\b(?:last|past|previous|over|within|in)(?:\s+the)?(?:\s+(?:last|past))?\s+"
    r"(\d+|" + "|".join(_NUM_WORDS) + r")\s+(day|week|month|year)s?\b")


def _parse_window(q: str) -> tuple[int, str] | None:
    m = _WINDOW_RE.search(q)
    if not m:
        return None
    raw = m.group(1)
    n = int(raw) if raw.isdigit() else _NUM_WORDS[raw]
    return (n, m.group(2)) if n > 0 else None


def _minus(d: dt.date, n: int, unit: str) -> dt.date:
    if unit == "day":
        return d - dt.timedelta(days=n)
    if unit == "week":
        return d - dt.timedelta(weeks=n)
    months = n * (12 if unit == "year" else 1)
    y, m = divmod(d.year * 12 + (d.month - 1) - months, 12)
    m += 1
    last_day = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)).day
    return dt.date(y, m, min(d.day, last_day))
