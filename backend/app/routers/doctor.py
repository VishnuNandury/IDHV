"""Doctor endpoints: consented patient list, record Q&A, and writing OPD notes (spec sections 6-8)."""
from __future__ import annotations

import datetime as dt
import hashlib
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ai.base import get_provider
from app.audit import log_event, request_ip
from app.auth import require_role
from app.consent import CONSENT_REQUIRED, active_grants_for_doctor, patient_access
from app.db import get_db, isoz, utcnow
from app.models import Encounter, Medication, User, profile_to_dict, row_to_dict
from app.schemas import AskRequest, AskResponse

router = APIRouter(prefix="/doctor", tags=["doctor"])


class NoteMedication(BaseModel):
    drug_name: str = Field(min_length=1, max_length=255)
    dosage: Optional[str] = Field(default=None, max_length=64)
    frequency: Optional[str] = Field(default=None, max_length=64)
    route: Optional[str] = Field(default="oral", max_length=32)
    duration: Optional[str] = Field(default=None, max_length=64)
    instructions: Optional[str] = None


class NoteCreate(BaseModel):
    date: Optional[dt.date] = None
    reason: Optional[str] = None
    diagnosis: Optional[str] = None
    notes: Optional[str] = None
    advice: Optional[str] = None
    follow_up_date: Optional[dt.date] = None
    fee: Optional[float] = Field(default=None, ge=0)
    facility: Optional[str] = Field(default=None, max_length=255)
    medications: list[NoteMedication] = Field(default_factory=list)


def _doctor_only(access: tuple[User, User]) -> tuple[User, User]:
    user, patient = access
    if user.role != "doctor":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=CONSENT_REQUIRED)
    return user, patient


@router.get("/patients")
def my_patients(user: User = Depends(require_role("doctor")), db: Session = Depends(get_db)):
    out = []
    for g in active_grants_for_doctor(db, user.id):
        p = profile_to_dict(g.patient)
        out.append({"patient_id": g.patient_id, "full_name": p.get("full_name"), "dhid": p.get("dhid"),
                    "age": p.get("age"), "sex": p.get("sex"), "blood_group": p.get("blood_group"),
                    "grant_id": g.id, "scope": g.scope, "access_minutes": g.access_minutes,
                    "expires_at": isoz(g.expires_at)})
    return out


@router.post("/patients/{patient_id}/ask", response_model=AskResponse)
def ask(patient_id: int, body: AskRequest, request: Request, access=Depends(patient_access()),
        db: Session = Depends(get_db)):
    user, _patient = _doctor_only(access)
    # Privacy: the audit log (visible to admins) records only the question's length and hash, never its text.
    log_event(db, user, "ASK_QUERY", patient_id=patient_id, ip=request_ip(request),
              detail={"question_len": len(body.question),
                      "question_sha256": hashlib.sha256(body.question.encode("utf-8")).hexdigest()})
    provider = get_provider()
    return provider.answer(db, patient_id, body.question, body.trimmed_history())


@router.post("/patients/{patient_id}/notes", status_code=status.HTTP_201_CREATED)
def write_note(patient_id: int, body: NoteCreate, request: Request, access=Depends(patient_access(write=True)),
               db: Session = Depends(get_db)):
    doctor, _patient = _doctor_only(access)
    prof = doctor.doctor_profile
    visit_date = body.date or utcnow().date()
    enc = Encounter(patient_id=patient_id, type="opd", date=visit_date, facility=body.facility or
                    (prof.facility if prof else None), doctor_name=doctor.full_name,
                    specialization=prof.specialization if prof else None, reason=body.reason,
                    diagnosis=body.diagnosis, notes=body.notes, advice=body.advice,
                    follow_up_date=body.follow_up_date, fee=body.fee)
    db.add(enc)
    meds = [Medication(patient_id=patient_id, drug_name=m.drug_name.strip(), dosage=m.dosage,
                       frequency=m.frequency, route=m.route, duration=m.duration, instructions=m.instructions,
                       start_date=visit_date, prescriber=doctor.full_name, active=True)
            for m in body.medications]
    db.add_all(meds)
    db.flush()
    log_event(db, doctor, "WRITE_NOTE", patient_id=patient_id, ip=request_ip(request),
              detail={"encounter_id": enc.id, "medications": len(meds),
                      "medication_ids": [m.id for m in meds]})  # commits encounter + meds too
    return {"encounter": row_to_dict(enc), "medications": [row_to_dict(m) for m in meds]}
