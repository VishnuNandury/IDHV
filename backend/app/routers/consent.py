from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import request_ip
from app.auth import require_role
from app.consent import ConsentError, ConsentRateLimited, create_grant, grant_status, redeem_grant, revoke_grant
from app.db import get_db, isoz, utcnow
from app.models import ConsentGrant, User
from app.schemas import GrantCreate, RedeemRequest

router = APIRouter(prefix="/consent", tags=["consent"])


def grant_to_dict(g: ConsentGrant, now=None) -> dict:
    doctor = g.doctor
    return {
        "id": g.id, "patient_id": g.patient_id, "doctor_id": g.doctor_id,
        "doctor_name": doctor.full_name if doctor else None,
        "doctor_specialization": doctor.doctor_profile.specialization if doctor and doctor.doctor_profile else None,
        "access_minutes": g.access_minutes, "scope": g.scope, "status": grant_status(g, now),
        "created_at": isoz(g.created_at), "otp_expires_at": isoz(g.otp_expires_at),
        "expires_at": isoz(g.expires_at), "revoked_at": isoz(g.revoked_at),
    }


@router.post("/grants")
def create(body: GrantCreate, request: Request, user: User = Depends(require_role("patient")), db: Session = Depends(get_db)):
    try:
        g = create_grant(db, user, body.access_minutes, body.scope, ip=request_ip(request))
    except ConsentError as exc:
        raise HTTPException(400, detail=str(exc)) from None
    dhid = user.patient_profile.dhid
    return {"grant_id": g.id, "otp": g.otp, "otp_expires_at": isoz(g.otp_expires_at), "dhid": dhid,
            "access_minutes": g.access_minutes, "scope": g.scope,
            "qr_payload": json.dumps({"dhid": dhid, "otp": g.otp})}


@router.get("/grants")
def list_grants(user: User = Depends(require_role("patient")), db: Session = Depends(get_db)):
    now = utcnow()
    grants = db.scalars(select(ConsentGrant).where(ConsentGrant.patient_id == user.id)
                        .order_by(ConsentGrant.created_at.desc(), ConsentGrant.id.desc())).all()
    return [grant_to_dict(g, now) for g in grants]


@router.delete("/grants/{grant_id}")
def revoke(grant_id: int, request: Request, user: User = Depends(require_role("patient")), db: Session = Depends(get_db)):
    g = revoke_grant(db, user, grant_id, ip=request_ip(request))
    if g is None:
        raise HTTPException(404, detail="Grant not found")
    return grant_to_dict(g)


@router.post("/redeem")
def redeem(body: RedeemRequest, request: Request, user: User = Depends(require_role("doctor")), db: Session = Depends(get_db)):
    try:
        g = redeem_grant(db, user, body.dhid, body.otp, ip=request_ip(request))
    except ConsentRateLimited as exc:
        raise HTTPException(429, detail=str(exc)) from None
    except ConsentError as exc:
        raise HTTPException(400, detail=str(exc)) from None
    patient = g.patient
    return {"grant_id": g.id, "patient_id": g.patient_id, "patient_name": patient.full_name,
            "dhid": patient.patient_profile.dhid if patient.patient_profile else None,
            "scope": g.scope, "access_minutes": g.access_minutes, "expires_at": isoz(g.expires_at)}
