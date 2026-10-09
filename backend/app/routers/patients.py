from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import log_event, request_ip
from app.auth import require_role
from app.consent import patient_access
from app.db import get_db
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
from app.schemas import ProfileUpdate

router = APIRouter(prefix="/patients", tags=["patients"])

KEY_LAB_TESTS = ("HbA1c", "FBS", "LDL", "Creatinine")
ENCOUNTER_TYPES = ("opd", "ipd", "diagnostic", "pharmacy")
TYPE_LABELS = {"opd": "OPD visit", "ipd": "Hospital admission", "diagnostic": "Diagnostics", "pharmacy": "Pharmacy"}
_BP_RE = re.compile(r"(\d{2,3})\s*/\s*(\d{2,3})")

_RECORDS = {
    "conditions": (Condition, lambda: (Condition.status.asc(), Condition.diagnosed_date.desc().nulls_last())),
    "medications": (Medication, lambda: (Medication.active.desc(), Medication.start_date.desc().nulls_last())),
    "labs": (LabResult, lambda: (LabResult.date.desc().nulls_last(), LabResult.test_name.asc())),
    "vitals": (Vital, lambda: (Vital.date.desc().nulls_last(), Vital.kind.asc())),
    "encounters": (Encounter, lambda: (Encounter.date.desc().nulls_last(),)),
}


# ---- own profile -----------------------------------------------------------

@router.get("/me/profile")
def get_my_profile(user: User = Depends(require_role("patient"))):
    return profile_to_dict(user)


@router.put("/me/profile")
def update_my_profile(body: ProfileUpdate, request: Request, user: User = Depends(require_role("patient")),
                      db: Session = Depends(get_db)):
    changes = body.model_dump(exclude_unset=True)
    full_name = changes.pop("full_name", None)
    if full_name:
        user.full_name = full_name.strip()
    profile = user.patient_profile
    for k, v in changes.items():
        setattr(profile, k, v)
    fields = sorted(changes) + (["full_name"] if full_name else [])
    log_event(db, user, "PROFILE_UPDATED", patient_id=user.id, detail={"fields": fields},
              ip=request_ip(request))  # commits
    db.refresh(user)
    return profile_to_dict(user)


# ---- clinical views (consent-guarded) ---------------------------------------

def _records(db: Session, kind: str, patient_id: int) -> list[dict]:
    model, order = _RECORDS[kind]
    rows = db.scalars(select(model).where(model.patient_id == patient_id).order_by(*order())).all()
    return rows_with_sources(db, rows)


@router.get("/{patient_id}/records/{kind}")
def records(patient_id: int, kind: str, access=Depends(patient_access()), db: Session = Depends(get_db)):
    if kind not in _RECORDS:
        raise HTTPException(404, detail=f"Unknown record type '{kind}'")
    return _records(db, kind, patient_id)


def bp_series(vitals) -> list[dict]:
    out = []
    for v in vitals:
        m = _BP_RE.search(v.value or "")
        if v.kind == "bp" and m and v.date:
            out.append({"date": v.date.isoformat(), "systolic": int(m.group(1)), "diastolic": int(m.group(2)),
                        "source_document_id": v.source_document_id})
    return out


@router.get("/{patient_id}/overview")
def overview(patient_id: int, access=Depends(patient_access()), db: Session = Depends(get_db)):
    _, patient = access
    conds = db.scalars(select(Condition).where(Condition.patient_id == patient_id, Condition.status == "active")
                       .order_by(Condition.diagnosed_date.asc().nulls_last())).all()
    meds = db.scalars(select(Medication).where(Medication.patient_id == patient_id, Medication.active.is_(True))
                      .order_by(Medication.start_date.asc().nulls_last())).all()
    vitals = db.scalars(select(Vital).where(Vital.patient_id == patient_id)
                        .order_by(Vital.date.asc().nulls_first(), Vital.id.asc())).all()
    latest: dict[str, dict] = {}
    for v in vitals:  # ascending, so later rows overwrite
        latest[v.kind] = {"date": v.date.isoformat() if v.date else None, "value": v.value,
                          "value_num": v.value_num, "unit": v.unit, "source_document_id": v.source_document_id}

    labs = db.scalars(select(LabResult).where(LabResult.patient_id == patient_id,
                                              LabResult.test_name.in_(KEY_LAB_TESTS))
                      .order_by(LabResult.date.asc().nulls_first(), LabResult.id.asc())).all()
    trends: dict[str, list[dict]] = {t: [] for t in KEY_LAB_TESTS}
    for r in labs:
        if r.date is not None and r.value_num is not None:
            trends[r.test_name].append({"date": r.date.isoformat(), "value_num": r.value_num, "unit": r.unit,
                                        "flag": r.flag, "source_document_id": r.source_document_id})

    def _count(model) -> int:
        return db.scalar(select(func.count()).select_from(model).where(model.patient_id == patient_id)) or 0

    recent = db.scalars(select(Encounter).where(Encounter.patient_id == patient_id)
                        .order_by(Encounter.date.desc().nulls_last()).limit(5)).all()
    return {
        "patient": profile_to_dict(patient),
        "active_conditions": rows_with_sources(db, conds),
        "active_medications": rows_with_sources(db, meds),
        "latest_vitals": latest,
        "lab_trends": trends,
        "bp_series": bp_series(vitals),
        "recent_encounters": rows_with_sources(db, recent),
        "counts": {"documents": _count(Document), "encounters": _count(Encounter), "lab_results": _count(LabResult),
                   "medications": _count(Medication), "conditions": _count(Condition), "vitals": _count(Vital)},
    }


@router.get("/{patient_id}/timeline")
def timeline(patient_id: int, access=Depends(patient_access()), db: Session = Depends(get_db)):
    items: list[dict] = []
    for e in db.scalars(select(Encounter).where(Encounter.patient_id == patient_id)):
        label = TYPE_LABELS.get(e.type, e.type)
        items.append({
            "date": e.date.isoformat() if e.date else None, "kind": "encounter", "type": e.type,
            "title": e.reason or e.diagnosis or label,
            "subtitle": " · ".join(x for x in (label, e.specialization, e.doctor_name, e.facility) if x),
            "id": e.id, "source_document_id": e.source_document_id,
            "end_date": e.end_date.isoformat() if e.end_date else None, "diagnosis": e.diagnosis, "fee": e.fee,
        })
    docs = db.execute(select(Document.id, Document.filename, Document.doc_type, Document.kind, Document.status,
                             Document.document_date, Document.uploaded_at, Document.extraction)
                      .where(Document.patient_id == patient_id)).all()
    for d in docs:
        when = d.document_date or (d.uploaded_at.date() if d.uploaded_at else None)
        summary = (d.extraction or {}).get("summary") if isinstance(d.extraction, dict) else None
        items.append({
            "date": when.isoformat() if when else None, "kind": "document", "type": d.doc_type or d.kind,
            "title": d.filename, "subtitle": summary or f"Document · {d.status}",
            "id": d.id, "source_document_id": d.id, "status": d.status,
        })
    items.sort(key=lambda i: (i["date"] or "", i["kind"] == "encounter"), reverse=True)
    return items


@router.get("/{patient_id}/expenses")
def expenses(patient_id: int, access=Depends(patient_access()), db: Session = Depends(get_db)):
    by_type = {t: 0.0 for t in ENCOUNTER_TYPES}
    by_month: dict[str, float] = defaultdict(float)
    total = 0.0
    rows = db.execute(select(Encounter.type, Encounter.date, Encounter.fee)
                      .where(Encounter.patient_id == patient_id, Encounter.fee.is_not(None))).all()
    for etype, when, fee in rows:
        fee = float(fee or 0)
        total += fee
        by_type[etype] = by_type.get(etype, 0.0) + fee
        if isinstance(when, date):
            by_month[when.strftime("%Y-%m")] += fee
    return {
        "total": round(total, 2),
        "by_type": {k: round(v, 2) for k, v in by_type.items()},
        "by_month": [{"month": m, "amount": round(a, 2)} for m, a in sorted(by_month.items())],
    }
