from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import log_event
from app.auth import create_token, get_current_user, hash_password, verify_password
from app.db import get_db
from app.models import DoctorProfile, PatientProfile, User, user_to_dict
from app.schemas import LoginRequest, RegisterRequest

router = APIRouter(prefix="/auth", tags=["auth"])


def new_dhid(db: Session) -> str:
    """Random unique ``DH-XXXX-XXXX-XXXX``."""
    while True:
        dhid = "DH-" + "-".join(f"{secrets.randbelow(10_000):04d}" for _ in range(3))
        if db.scalar(select(PatientProfile.user_id).where(PatientProfile.dhid == dhid)) is None:
            return dhid


def _token_response(user: User) -> dict:
    return {"access_token": create_token(user.id), "token_type": "bearer", "user": user_to_dict(user)}


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _opt(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


@router.post("/register")
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, detail="Invalid email")
    if db.scalar(select(User.id).where(User.email == email)) is not None:
        raise HTTPException(400, detail="Email already registered")
    user = User(email=email, password_hash=hash_password(body.password), full_name=body.full_name.strip(),
                role=body.role)
    db.add(user)
    db.flush()
    if body.role == "patient":
        db.add(PatientProfile(user_id=user.id, dhid=new_dhid(db), dob=body.dob, sex=_opt(body.sex),
                              phone=_opt(body.phone), allergies=[], hereditary_history=[], family_members=[]))
    else:
        db.add(DoctorProfile(user_id=user.id, license_no=body.license_no, specialization=body.specialization,
                             qualification=body.qualification, facility=body.facility))
    db.flush()
    log_event(db, user, "REGISTER", patient_id=user.id if body.role == "patient" else None, ip=_ip(request))
    db.refresh(user)
    return _token_response(user)


@router.post("/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(401, detail="Invalid email or password")
    log_event(db, user, "LOGIN", ip=_ip(request))
    return _token_response(user)


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user_to_dict(user)
