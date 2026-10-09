"""Strict validation for patient-edited extractions submitted to ``POST /documents/{id}/commit``.

``ReviewedExtraction`` subclasses ``ExtractionResult`` (so ``commit_extraction`` accepts it unchanged) but adds
limits. The plain ``ExtractionResult`` stays permissive because it is also Claude's structured-output schema.
Rules: NUL characters stripped; doc_type from a fixed vocabulary; every date must parse (YYYY-MM-DD); 1 <= page <= 10000;
fee >= 0 and finite; lists <= 500 items; strings <= 5000 chars.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional

from pydantic import AfterValidator, ConfigDict, Field, model_validator

from app.ingestion.commit import parse_date, strip_nul
from app.schemas import (
    ExtractedCondition,
    ExtractedEncounter,
    ExtractedLab,
    ExtractedMedication,
    ExtractedVital,
    ExtractionResult,
)

DOC_TYPES = ("prescription", "lab_report", "discharge_summary", "opd_note", "pharmacy_bill", "imaging_report",
             "insurance", "other")
MAX_ITEMS = 500
MAX_STR = 5000
MAX_PAGE = 10_000

_LIMITS = ConfigDict(str_max_length=MAX_STR, allow_inf_nan=False)


def _valid_date(value: Optional[str]) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    d = parse_date(value)
    if d is None:
        raise ValueError("must be a valid date (YYYY-MM-DD or DD/MM/YYYY)")
    return d.isoformat()


ReviewDate = Annotated[Optional[str], AfterValidator(_valid_date)]
Page = Optional[Annotated[int, Field(ge=1, le=MAX_PAGE)]]


def _items(model):
    return Field(default_factory=list, max_length=MAX_ITEMS)


class ReviewedCondition(ExtractedCondition):
    model_config = _LIMITS
    diagnosed_date: ReviewDate = None
    page: Page = None


class ReviewedMedication(ExtractedMedication):
    model_config = _LIMITS
    start_date: ReviewDate = None
    page: Page = None


class ReviewedLab(ExtractedLab):
    model_config = _LIMITS
    date: ReviewDate = None
    page: Page = None


class ReviewedVital(ExtractedVital):
    model_config = _LIMITS
    date: ReviewDate = None
    page: Page = None


class ReviewedEncounter(ExtractedEncounter):
    model_config = _LIMITS
    date: ReviewDate = None
    end_date: ReviewDate = None
    follow_up_date: ReviewDate = None
    fee: Optional[Annotated[float, Field(ge=0)]] = None
    page: Page = None


class ReviewedExtraction(ExtractionResult):
    model_config = _LIMITS

    @model_validator(mode="before")
    @classmethod
    def _strip_nul(cls, data):
        return strip_nul(data)  # Postgres TEXT/JSON rejects NUL; strip from every string, nested included
    doc_type: Literal[DOC_TYPES] = "other"  # type: ignore[valid-type]
    document_date: ReviewDate = None
    conditions: list[ReviewedCondition] = _items(ReviewedCondition)
    medications: list[ReviewedMedication] = _items(ReviewedMedication)
    lab_results: list[ReviewedLab] = _items(ReviewedLab)
    vitals: list[ReviewedVital] = _items(ReviewedVital)
    encounter: Optional[ReviewedEncounter] = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    warnings: list[str] = _items(str)


# ---------------------------------------------------------------------------
# Sanitising provider output before it is stored (so "commit unchanged" always passes ReviewedExtraction)
# ---------------------------------------------------------------------------

_DATE_FIELDS = {
    None: ("document_date",),
    "conditions": ("diagnosed_date",),
    "medications": ("start_date",),
    "lab_results": ("date",),
    "vitals": ("date",),
    "encounter": ("date", "end_date", "follow_up_date"),
}
_LIST_FIELDS = ("conditions", "medications", "lab_results", "vitals")


def _clip(value):
    """Truncate strings to ``MAX_STR`` and lists to ``MAX_ITEMS``, recursively."""
    if isinstance(value, str):
        return value[:MAX_STR]
    if isinstance(value, list):
        return [_clip(v) for v in value[:MAX_ITEMS]]
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    return value


def _fix_page(item: dict) -> None:
    page = item.get("page")
    if page is None:
        return
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= MAX_PAGE:
        item["page"] = None


def _fix_dates(item: dict, fields: tuple[str, ...], where: str, warnings: list[str]) -> None:
    for f in fields:
        raw = item.get(f)
        if raw is None or str(raw).strip() == "":
            item[f] = None
            continue
        d = parse_date(raw)
        if d is None:
            warnings.append(f"Unparseable date in {where}{f}: '{str(raw)[:100]}' (cleared; please check)")
            item[f] = None
        else:
            item[f] = d.isoformat()


def sanitise_extraction(result: ExtractionResult) -> ExtractionResult:
    """Normalise a provider's (permissive) ``ExtractionResult`` so it satisfies every ``ReviewedExtraction`` rule:
    unparseable dates -> None (with a warning naming the field and value), unknown ``doc_type`` -> ``other``,
    page outside 1..10000 -> None, confidence clamped to [0, 1], negative / non-finite fee -> None, strings and
    lists truncated to the review limits. NUL characters are stripped too."""
    import math

    data = strip_nul(result.model_dump())
    warnings: list[str] = [str(w) for w in (data.get("warnings") or []) if w is not None]

    doc_type = str(data.get("doc_type") or "other").strip().lower()
    if doc_type not in DOC_TYPES:
        warnings.append(f"Unknown document type '{str(data.get('doc_type'))[:100]}' (set to 'other')")
        doc_type = "other"
    data["doc_type"] = doc_type

    _fix_dates(data, _DATE_FIELDS[None], "", warnings)
    for key in _LIST_FIELDS:
        items = [i for i in (data.get(key) or []) if isinstance(i, dict)][:MAX_ITEMS]
        for idx, item in enumerate(items):
            _fix_page(item)
            _fix_dates(item, _DATE_FIELDS[key], f"{key}[{idx}].", warnings)
        data[key] = items
    enc = data.get("encounter")
    if isinstance(enc, dict):
        _fix_page(enc)
        _fix_dates(enc, _DATE_FIELDS["encounter"], "encounter.", warnings)
        fee = enc.get("fee")
        if fee is not None and (not isinstance(fee, (int, float)) or not math.isfinite(fee) or fee < 0):
            enc["fee"] = None

    conf = data.get("confidence")
    if not isinstance(conf, (int, float)) or not math.isfinite(conf):
        conf = 0.5
    data["confidence"] = min(1.0, max(0.0, float(conf)))

    data["warnings"] = warnings
    data = _clip(data)
    return ExtractionResult.model_validate(data)
