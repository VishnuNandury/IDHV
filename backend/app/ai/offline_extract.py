"""Offline digitiser: optional OCR (pytesseract) + keyword/regex extraction (spec section 5, OfflineProvider).

Used directly by the ingestion pipeline as the fallback when an AI provider fails, and wrapped by
``app.ai.offline.OfflineProvider.digitise``. Never raises on odd input; confidence is capped at 0.5.
"""
from __future__ import annotations

import io
import logging
import re
from datetime import date

from app.schemas import (
    ExtractedCondition,
    ExtractedContent,
    ExtractedEncounter,
    ExtractedLab,
    ExtractedMedication,
    ExtractedVital,
    ExtractionResult,
)

log = logging.getLogger(__name__)

MAX_CONFIDENCE = 0.5

# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------


def ocr_image(png: bytes) -> str:
    """OCR PNG bytes with pytesseract. Returns "" (never raises) if tesseract/pytesseract is unavailable."""
    try:
        import pytesseract
        from PIL import Image

        with Image.open(io.BytesIO(png)) as img:
            return (pytesseract.image_to_string(img) or "").strip()
    except Exception as exc:  # noqa: BLE001 - TesseractNotFoundError, bad bytes, ImportError ...
        log.debug("OCR unavailable/failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Dictionaries
# ---------------------------------------------------------------------------

# canonical test name -> aliases (matched case-insensitively, longest alias first, word-bounded)
LAB_TESTS: dict[str, tuple[str, ...]] = {
    "HbA1c": ("hba1c", "hb a1c", "glycated haemoglobin", "glycated hemoglobin", "glycosylated haemoglobin",
              "glycosylated hemoglobin", "a1c"),
    "FBS": ("fasting blood sugar", "fasting blood glucose", "fasting plasma glucose", "fasting glucose", "fbs",
            "fbg", "fpg"),
    "PPBS": ("post prandial blood sugar", "post-prandial blood sugar", "postprandial blood sugar",
             "post prandial glucose", "post-prandial glucose", "pp blood sugar", "ppbs", "ppbg", "plbs"),
    "RBS": ("random blood sugar", "random blood glucose", "random glucose", "rbs"),
    "Creatinine": ("serum creatinine", "s. creatinine", "creatinine"),
    "eGFR": ("egfr",),
    "Urea": ("blood urea", "urea", "bun"),
    "Uric Acid": ("uric acid",),
    "Hb": ("haemoglobin", "hemoglobin", "hb"),
    "TSH": ("tsh", "thyroid stimulating hormone"),
    "T3": ("t3",), "T4": ("t4",),
    "LDL": ("ldl cholesterol", "ldl-c", "ldl"),
    "HDL": ("hdl cholesterol", "hdl-c", "hdl"),
    "VLDL": ("vldl",),
    "Total Cholesterol": ("total cholesterol", "serum cholesterol", "cholesterol"),
    "Triglycerides": ("triglycerides", "triglyceride"),
    "Urine ACR": ("urine acr", "urine albumin creatinine ratio", "uacr", "microalbumin"),
    "Troponin I": ("troponin i", "troponin"),
    "Vitamin D": ("vitamin d", "25-oh vitamin d", "vit d"),
    "Vitamin B12": ("vitamin b12", "vit b12"),
    "SGPT": ("sgpt", "alt"), "SGOT": ("sgot", "ast"),
    "Sodium": ("sodium",), "Potassium": ("potassium",),
    "Platelets": ("platelet count", "platelets"),
    "WBC": ("total leucocyte count", "wbc", "tlc"),
    "ESR": ("esr",), "CRP": ("crp", "c-reactive protein"),
}
_ALIAS_TO_TEST = {a: name for name, aliases in LAB_TESTS.items() for a in aliases}
_LAB_RE = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(a) for a in sorted(_ALIAS_TO_TEST, key=len, reverse=True))
                     + r")(?![A-Za-z0-9])", re.I)

CONDITIONS: dict[str, tuple[str, ...]] = {
    "Type 2 diabetes mellitus": ("type 2 diabetes", "type ii diabetes", "t2dm", "dm type 2", "dm2",
                                 "diabetes mellitus"),
    "Type 1 diabetes mellitus": ("type 1 diabetes", "t1dm"),
    "Hypertension": ("hypertension", "htn"),
    "Hypoglycaemia": ("hypoglycaemia", "hypoglycemia"),
    "Dyslipidaemia": ("dyslipidaemia", "dyslipidemia", "hyperlipidaemia", "hyperlipidemia"),
    "Coronary artery disease": ("coronary artery disease", "cad", "ihd", "ischaemic heart disease"),
    "Stable angina": ("stable angina", "angina"),
    "Hypothyroidism": ("hypothyroidism",),
    "Asthma": ("bronchial asthma", "asthma"),
    "COPD": ("copd",),
    "Chronic kidney disease": ("chronic kidney disease", "ckd"),
}
_CONDITION_RES = [(name, re.compile(r"(?<![A-Za-z])(" + "|".join(re.escape(a) for a in aliases) + r")(?![A-Za-z])", re.I))
                  for name, aliases in CONDITIONS.items()]

DOC_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "discharge_summary": ("discharge summary", "date of admission", "date of discharge", "course in hospital",
                          "admitted on", "discharged on"),
    "lab_report": ("laboratory", "diagnostics", "pathology", "reference range", "biological reference",
                   "sample id", "collected", "reported", "lab report", "investigation report"),
    "pharmacy_bill": ("pharmacy", "chemist", "invoice", "bill no", "mrp", "qty", "gstin", "batch", "pharma"),
    "prescription": ("rx", "℞", "prescription", "advice", "follow up", "follow-up", "review after"),
    "imaging_report": ("x-ray", "xray", "ultrasound", "usg", "mri", "ct scan", "echocardiography", "2d echo",
                       "impression", "radiology"),
    "insurance": ("policy number", "policy no", "insurer", "sum insured", "claim", "tpa"),
    "opd_note": ("opd", "chief complaint", "presenting complaint", "consultation", "o/e", "examination"),
}
_DOC_TYPE_ORDER = list(DOC_TYPE_KEYWORDS)  # tie-break priority
ENCOUNTER_FOR_DOC_TYPE = {"lab_report": "diagnostic", "imaging_report": "diagnostic", "pharmacy_bill": "pharmacy",
                          "discharge_summary": "ipd", "prescription": "opd", "opd_note": "opd"}

_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), start=1)}

# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

_PAGE_RE = re.compile(r"^\s*\[Page (\d+)\]\s*$")
_NUM = r"\d+(?:\.\d+)?"
# Same separator throughout (backreference) so reference ranges like "13.5-17.5" are never dates; 2-digit years
# only with "/" (12/03/25), never with "." or "-".
_DATE_RE = re.compile(
    r"(?<![\d.])(?:(?P<y1>(?:19|20)\d{2})(?P<s1>[-/.])(?P<m1>\d{1,2})(?P=s1)(?P<d1>\d{1,2})"
    r"|(?P<d2>\d{1,2})(?P<s2>[-/.])(?P<m2>\d{1,2})(?P=s2)(?P<y2>(?:19|20)\d{2})"
    r"|(?P<d4>\d{1,2})/(?P<m4>\d{1,2})/(?P<y4>\d{2})"
    r"|(?P<d3>\d{1,2})(?:st|nd|rd|th)?[\s\-/.]*(?P<mon3>jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
    r"[\s\-/.,]*(?P<y3>(?:19|20)\d{2}))(?!\d|\.\d)", re.I)
_DATE_LABEL_RE = re.compile(r"\b(date|dated|reported|collected|visit|issued|bill date|invoice date)\b", re.I)
_BP_RE = re.compile(r"(?:\b(?:BP|B\.P\.?|blood pressure)\b[^0-9\n]{0,15}(\d{2,3})\s*/\s*(\d{2,3})"
                    r"|(?<![\d/])(\d{2,3})\s*/\s*(\d{2,3})\s*mm\s*hg)", re.I)
_PULSE_RE = re.compile(r"\b(?:pulse|PR|HR|heart rate)\b\s*[:\-]?\s*(\d{2,3})\s*(?:/min|bpm|per min)?", re.I)
_WEIGHT_RE = re.compile(r"\b(?:weight|wt)\b\.?\s*[:\-]?\s*(\d{2,3}(?:\.\d)?)\s*kg", re.I)
_SPO2_RE = re.compile(r"\bspo2\b\s*[:\-]?\s*(\d{2,3})\s*%", re.I)
_TEMP_RE = re.compile(r"\b(?:temp|temperature)\b\.?\s*[:\-]?\s*(\d{2,3}(?:\.\d)?)\s*°?\s*([FC])\b", re.I)
_UNIT_RE = re.compile(r"^\s*(%|[a-zA-Zµμ]+(?:/[a-zA-Z0-9.µμ]+)*(?:\s*/\s*min/1\.73\s*m2)?)")
_RANGE_RE = re.compile(rf"({_NUM}\s*-\s*{_NUM}|[<>]=?\s*{_NUM}|(?:up to|upto)\s*{_NUM})", re.I)
_FLAG_RE = re.compile(r"(?<![A-Za-z])(H|L|High|Low|Normal)(?![A-Za-z])", re.I)
_DRUG_RE = re.compile(r"\b(Tab|Tablet|Cap|Capsule|Syp|Syrup|Syr|Inj|Injection|Oint|Drops?)\b\.?\s+(.+)", re.I)
_DOSE_RE = re.compile(rf"\b({_NUM})\s*(mg|mcg|µg|g|ml|iu|units?)\b", re.I)
_FREQ_RE = re.compile(r"(?<![A-Za-z0-9])(OD|BD|BID|TDS|TID|QID|HS|SOS|STAT|QD|QHS|"
                      r"[01]\s*-\s*[01]\s*-\s*[01](?:\s*-\s*[01])?)(?![A-Za-z0-9])", re.I)
_DURATION_RE = re.compile(r"(?:\b(?:for|x|×)\s*)(\d+\s*(?:days?|weeks?|wks?|months?))\b|\b(ongoing|continue|long[- ]term)\b",
                          re.I)
_MONEY_RE = re.compile(r"(?:₹|\bRs\.?|\bINR)\s*([\d,]+(?:\.\d{1,2})?)|([\d,]+(?:\.\d{1,2})?)\s*(?:INR\b|/-)", re.I)
_DOCTOR_RE = re.compile(r"\bDr\.?[ \t]+((?:[A-Z]\.[ \t]*)*[A-Z][A-Za-z]+(?:[ \t]+[A-Z][A-Za-z]+){0,2})")
_FACILITY_RE = re.compile(r"\b(hospital|clinic|diagnostics?|laborator(?:y|ies)|labs?|pharmacy|chemists?|medical|"
                          r"centre|center|institute|nursing home|healthcare|polyclinic)\b", re.I)


# ---------------------------------------------------------------------------
# small parsers
# ---------------------------------------------------------------------------

def parse_date(m: re.Match) -> date | None:
    try:
        if m.group("y1"):
            return date(int(m.group("y1")), int(m.group("m1")), int(m.group("d1")))
        if m.group("y2"):
            return date(int(m.group("y2")), int(m.group("m2")), int(m.group("d2")))  # Indian dd/mm/yyyy
        if m.group("y4"):
            return date(2000 + int(m.group("y4")), int(m.group("m4")), int(m.group("d4")))  # dd/mm/yy
        if m.group("y3"):
            return date(int(m.group("y3")), _MONTHS[m.group("mon3").lower()[:3]], int(m.group("d3")))
    except ValueError:
        return None
    return None


def _dates_in(s: str) -> list[date]:
    return [d for d in (parse_date(m) for m in _DATE_RE.finditer(s)) if d is not None]


def _row_date(line: str, ref_range: str | None = None) -> date | None:
    """Per-row date for a lab line: 4-digit years only, and never text inside the reference range."""
    scan = line.replace(ref_range, " ") if ref_range and ref_range in line else line
    for m in _DATE_RE.finditer(scan):
        if m.group("y4"):
            continue
        d = parse_date(m)
        if d is not None:
            return d
    return None


def _num(s: str | None) -> float | None:
    if not s:
        return None
    m = re.search(_NUM, s.replace(",", ""))
    return float(m.group()) if m else None


def _flag_from_range(value: float, ref: str | None) -> str | None:
    if not ref:
        return None
    r = ref.replace(" ", "").lower()
    m = re.fullmatch(rf"({_NUM})-({_NUM})", r)
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return "low" if value < lo else "high" if value > hi else "normal"
    m = re.fullmatch(rf"(<=?|upto)({_NUM})", r)
    if m:
        lim = float(m.group(2))
        return "high" if (value > lim if m.group(1) != "<" else value >= lim) else "normal"
    m = re.fullmatch(rf"(>=?)({_NUM})", r)
    if m:
        lim = float(m.group(2))
        return "low" if (value < lim if m.group(1) == ">=" else value <= lim) else "normal"
    return None


def _fmt_num(s: str) -> str:
    return s.replace(",", "")


# ---------------------------------------------------------------------------
# line model
# ---------------------------------------------------------------------------

def _lines_with_pages(text: str) -> list[tuple[str, int | None]]:
    out: list[tuple[str, int | None]] = []
    page: int | None = None
    for raw in text.splitlines():
        m = _PAGE_RE.match(raw)
        if m:
            page = int(m.group(1))
            continue
        line = raw.strip()
        if line:
            out.append((line, page))
    return out


def _parse_lab_tail(tail: str) -> dict | None:
    """Parse '<value> <unit> <range> <flag>' from the text after a test name."""
    tail = re.sub(r"^\s*\([^)]*\)", "", tail)  # "(Glycated Hb)"
    tail = re.sub(r"^\s*[:\-–=]\s*", "", tail).strip()
    m = re.match(rf"({_NUM})", tail)
    if not m:
        return None
    value = m.group(1)
    rest = tail[m.end():]
    unit = None
    um = _UNIT_RE.match(rest)
    if um and not _FLAG_RE.fullmatch(um.group(1).strip()):
        unit = um.group(1).strip()
        rest = rest[um.end():]
    ref = None
    rm = _RANGE_RE.search(rest)
    if rm:
        ref = re.sub(r"\s+", " ", rm.group(1)).strip()
        after = rest[rm.end():]
    else:
        after = rest
    flag = None
    fm = _FLAG_RE.search(after[:20]) if after.strip() else None
    if fm:
        flag = {"h": "high", "high": "high", "l": "low", "low": "low", "normal": "normal"}[fm.group(1).lower()]
    if flag is None:
        flag = _flag_from_range(float(value), ref)
    return {"value": value, "unit": unit, "ref_range": ref, "flag": flag}


def _extract_labs(lines: list[tuple[str, int | None]]) -> list[ExtractedLab]:
    labs: list[ExtractedLab] = []
    seen: set[tuple] = set()

    def add(test, parsed, page, when):
        key = (test, parsed["value"], page, when)
        if key in seen:
            return
        seen.add(key)
        labs.append(ExtractedLab(test_name=test, value=parsed["value"], unit=parsed["unit"],
                                 ref_range=parsed["ref_range"], flag=parsed["flag"],
                                 date=when.isoformat() if when else None, page=page))

    i = 0
    while i < len(lines):
        line, page = lines[i]
        cells = line.split("\t")
        header_tests = [_ALIAS_TO_TEST.get(c.strip().lower()) for c in cells]
        other_cells = [c.strip() for c, t in zip(cells, header_tests) if not t and c.strip()]
        is_header = (sum(1 for t in header_tests if t) >= 2
                     and not any(re.fullmatch(_NUM, c) or _dates_in(c) for c in other_cells))
        if is_header:
            # column-header table: Date \t Test A \t Test B ... \t Unit
            unit_col = next((j for j, c in enumerate(cells) if c.strip().lower() in ("unit", "units")), None)
            j = i + 1
            while j < len(lines) and "\t" in lines[j][0] and not lines[j][0].startswith("[Sheet"):
                row = lines[j][0].split("\t")
                when = _row_date(lines[j][0])
                unit = row[unit_col].strip() if unit_col is not None and unit_col < len(row) else None
                for col, test in enumerate(header_tests):
                    if test and col < len(row) and re.fullmatch(_NUM, row[col].strip()):
                        add(test, {"value": row[col].strip(), "unit": unit or None, "ref_range": None, "flag": None},
                            lines[j][1], when)
                j += 1
            i = j
            continue
        m = _LAB_RE.search(line)
        if m:
            test = _ALIAS_TO_TEST[m.group(1).lower()]
            tail = line[m.end():]
            parsed = _parse_lab_tail(tail)
            if parsed is None and len(re.sub(r"\([^)]*\)", "", tail).strip()) <= 3:
                # vertical layout (pdf table cells one per line): name / value / unit / range / flag
                buf, k = [], i + 1
                while k < len(lines) and k <= i + 5 and lines[k][1] == page and not _LAB_RE.search(lines[k][0]):
                    buf.append(lines[k][0])
                    k += 1
                parsed = _parse_lab_tail(" ".join(buf)) if buf else None
                if parsed is not None and not re.match(rf"^{_NUM}$", buf[0]):
                    parsed = None
            if parsed is not None:
                add(test, parsed, page, _row_date(line, parsed["ref_range"]))
        i += 1
    return labs


def _extract_vitals(lines) -> list[ExtractedVital]:
    vitals: list[ExtractedVital] = []
    seen = set()

    def add(kind, value, unit, page):
        if (kind, value, page) not in seen:
            seen.add((kind, value, page))
            vitals.append(ExtractedVital(kind=kind, value=value, unit=unit, page=page))

    for line, page in lines:
        for m in _BP_RE.finditer(line):
            sys_, dia = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
            if 60 <= int(sys_) <= 260 and 30 <= int(dia) <= 160:
                add("bp", f"{sys_}/{dia}", "mmHg", page)
        for m in _PULSE_RE.finditer(line):
            add("pulse", m.group(1), "/min", page)
        for m in _WEIGHT_RE.finditer(line):
            add("weight", m.group(1), "kg", page)
        for m in _SPO2_RE.finditer(line):
            add("spo2", m.group(1), "%", page)
        for m in _TEMP_RE.finditer(line):
            add("temp", m.group(1), "°" + m.group(2).upper(), page)
    return vitals


_ROUTES = {"inj": "injection", "injection": "injection", "oint": "topical", "drop": "drops", "drops": "drops"}


def _extract_medications(lines) -> list[ExtractedMedication]:
    meds: list[ExtractedMedication] = []
    for line, page in lines:
        m = _DRUG_RE.search(line)
        if not m:
            continue
        rest = m.group(2)
        dose = _DOSE_RE.search(rest)
        freq = _FREQ_RE.search(rest)
        if not dose and not freq:
            continue
        cut = min(x.start() for x in (dose, freq) if x)
        name = re.sub(r"[\s\-:,;\t]+$", "", rest[:cut]).strip()
        name = re.split(r"\t", name)[0].strip()
        if not name or len(name) > 60 or not re.match(r"[A-Za-z]", name):
            continue
        dur = _DURATION_RE.search(rest)
        duration = (dur.group(1) or dur.group(2)).lower() if dur else None
        form = m.group(1).lower()
        freq_s = re.sub(r"\s+", "", freq.group(1)).upper() if freq else None
        instr = None
        im = re.search(r"\b(before|after|with)\s+(food|meals?|breakfast|lunch|dinner)\b", rest, re.I)
        if im:
            instr = im.group(0)
        meds.append(ExtractedMedication(
            drug_name=name, dosage=f"{_fmt_num(dose.group(1))} {dose.group(2).lower()}" if dose else None,
            frequency=freq_s, route=_ROUTES.get(form, "oral"), duration=duration, instructions=instr, page=page))
    return meds


def _extract_fee(lines) -> float | None:
    totals, fees, others = [], [], []
    for line, _ in lines:
        for m in _MONEY_RE.finditer(line):
            raw = m.group(1) or m.group(2)
            try:
                amount = float(raw.replace(",", ""))
            except ValueError:
                continue
            if amount <= 0:
                continue
            low = line.lower()
            if re.search(r"\b(total|net|grand|payable|amount paid|bill amount|hospital bill)\b", low):
                totals.append(amount)
            elif re.search(r"\b(fee|fees|consult|charges?)\b", low):
                fees.append(amount)
            else:
                others.append(amount)
    if totals:
        return totals[-1]
    if fees:
        return fees[-1]
    return max(others) if others else None


def _classify(text: str, n_labs: int, n_meds: int) -> str:
    low = text.lower()
    scores = {}
    for doc_type, kws in DOC_TYPE_KEYWORDS.items():
        score = 0
        for kw in kws:
            if re.search(r"(?<![a-z])" + re.escape(kw) + r"(?![a-z])", low):
                score += 1
        scores[doc_type] = score
    scores["discharge_summary"] *= 3
    scores["lab_report"] += min(n_labs, 6) * (1 if n_labs >= 2 else 0)
    scores["prescription"] += min(n_meds, 4) if n_labs < 2 else min(n_meds, 1)
    if "discharge summary" in low:
        return "discharge_summary"
    best = max(_DOC_TYPE_ORDER, key=lambda t: (scores[t], -_DOC_TYPE_ORDER.index(t)))
    return best if scores[best] > 0 else "other"


def _labelled_date(lines, *labels: str) -> date | None:
    pat = re.compile(r"\b(" + "|".join(labels) + r")\b", re.I)
    for idx, (line, _) in enumerate(lines):
        if pat.search(line):
            ds = _dates_in(line[pat.search(line).end():]) or (_dates_in(lines[idx + 1][0]) if idx + 1 < len(lines) else [])
            if ds:
                return ds[0]
    return None


def _document_date(lines) -> date | None:
    for idx, (line, _) in enumerate(lines):
        lm = _DATE_LABEL_RE.search(line)
        if lm and not re.search(r"\b(follow|review|next|due|birth|dob)\b", line, re.I):
            ds = _dates_in(line[lm.end():])
            if not ds and idx + 1 < len(lines) and len(line) < 30:
                ds = _dates_in(lines[idx + 1][0])
            if ds:
                return ds[0]
    for line, _ in lines:
        if re.search(r"\b(follow|review|next|birth|dob)\b", line, re.I):
            continue
        ds = _dates_in(line)
        if ds:
            return ds[0]
    return None


def _facility(lines) -> str | None:
    for line, _ in lines[:8]:
        if _FACILITY_RE.search(line) and len(line) <= 80 and not line.lower().startswith(("dr", "referred")):
            return line.split("|")[0].strip().rstrip(",")
    return None


def _labelled_value(lines, label: str) -> str | None:
    pat = re.compile(rf"^\s*{label}\b\s*[:\t\-]?\s*(.*)$", re.I)
    for idx, (line, _) in enumerate(lines):
        m = pat.match(line)
        if m:
            val = m.group(1).strip(" :\t-")
            if not val and idx + 1 < len(lines):
                val = lines[idx + 1][0]
            if val:
                return val[:500]
    return None


def _extract_conditions(lines) -> list[ExtractedCondition]:
    scope = []
    for idx, (line, page) in enumerate(lines):
        if re.search(r"\b(diagnosis|diagnoses|impression|k/c/o|known case|c/o|history of|dx|final diagnosis)\b",
                     line, re.I):
            scope.append((line, page))
            if idx + 1 < len(lines) and len(line) < 25:
                scope.append(lines[idx + 1])
    out, seen = [], set()
    for line, page in scope:
        for name, rx in _CONDITION_RES:
            if name not in seen and rx.search(line):
                seen.add(name)
                out.append(ExtractedCondition(name=name, status="active", page=page))
    if "Type 2 diabetes mellitus" in seen:
        out = [c for c in out if c.name != "Type 1 diabetes mellitus"]
    return out


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def offline_digitise(content: ExtractedContent, filename: str) -> ExtractionResult:
    """Deterministic, best-effort extraction from text (+ OCR of images when tesseract is present)."""
    warnings: list[str] = ["Offline extraction (rules/OCR, no AI) — please review every field carefully."]
    text = content.text or ""
    images = list(content.images or [])
    if images:
        ocr_parts = []
        for png, page in images:
            t = ocr_image(png)
            if t:
                ocr_parts.append(f"[Page {page}]\n{t}" if page is not None else t)
        if ocr_parts:
            text = (text + "\n\n" + "\n\n".join(ocr_parts)).strip()
        else:
            warnings.append(f"OCR unavailable or found no text in {len(images)} image(s); "
                            "enter values from the original manually.")
    lines = _lines_with_pages(text)
    if not lines:
        warnings.append("No readable text found in this file.")
        return ExtractionResult(doc_type="other", summary=f"No text could be read from {filename}.",
                                confidence=0.1, warnings=warnings)

    labs = _extract_labs(lines)
    vitals = _extract_vitals(lines)
    meds = _extract_medications(lines)
    conditions = _extract_conditions(lines)
    doc_type = _classify(text, len(labs), len(meds))
    fee = _extract_fee(lines)

    doc_date = _document_date(lines)
    admit = discharge = None
    if doc_type == "discharge_summary":
        admit = _labelled_date(lines, "date of admission", "admitted on", "doa")
        discharge = _labelled_date(lines, "date of discharge", "discharged on", "dod")
        doc_date = admit or doc_date
    iso = doc_date.isoformat() if doc_date else None
    for lab in labs:
        lab.date = lab.date or iso
    for v in vitals:
        v.date = iso
    for med in meds:
        med.start_date = iso

    doctor = None
    dm = _DOCTOR_RE.search(text)
    if dm:
        doctor = "Dr. " + re.sub(r"\s+", " ", dm.group(1)).strip()

    encounter = None
    etype = ENCOUNTER_FOR_DOC_TYPE.get(doc_type) or ("opd" if fee is not None else None)
    if etype:
        follow = _labelled_date(lines, "follow[- ]?up", "review on", "review after", "next visit", "revisit")
        encounter = ExtractedEncounter(
            type=etype, date=iso, end_date=discharge.isoformat() if discharge else None,
            reason=_labelled_value(lines, r"(?:chief complaints?|presenting complaints?|reason|c/o)"),
            diagnosis=_labelled_value(lines, r"(?:final diagnosis|diagnosis|impression)"),
            advice=_labelled_value(lines, r"(?:advice|advised)"),
            follow_up_date=follow.isoformat() if follow and follow != doc_date else None, fee=fee)

    parts = [f"Offline extraction of {filename} ({doc_type.replace('_', ' ')})"]
    found = [f"{len(x)} {label}" for x, label in ((labs, "lab results"), (meds, "medications"),
                                                   (vitals, "vitals"), (conditions, "conditions")) if x]
    if found:
        parts.append("found " + ", ".join(found))
    summary = "; ".join(parts) + "."
    if fee is not None:
        summary += f" Amount ₹{fee:,.0f}."

    filled = sum(bool(x) for x in (labs, meds, vitals, conditions, doc_date, encounter))
    confidence = round(min(MAX_CONFIDENCE, 0.15 + 0.06 * filled), 2)
    return ExtractionResult(doc_type=doc_type, document_date=iso, facility=_facility(lines), doctor_name=doctor,
                            summary=summary, conditions=conditions, medications=meds, lab_results=labs,
                            vitals=vitals, encounter=encounter, confidence=confidence, warnings=warnings)
