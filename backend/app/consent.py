"""Time-bound, OTP-redeemed consent grants and the patient-access dependency (spec section 6)."""
from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.audit import atomic, log_event, request_ip
from app.auth import get_current_user
from app.db import get_db, isoz, utcnow
from app.models import AuditLog, ConsentGrant, PatientProfile, User

OTP_TTL_MINUTES = 10
ACCESS_MINUTES = (60, 1440, 10080)
SCOPES = ("read", "read_write")
CONSENT_REQUIRED = "CONSENT_REQUIRED"
MAX_FAILED_ATTEMPTS = 5  # per pending grant; reaching it revokes the grant
MAX_DOCTOR_FAILURES = 10  # per doctor within FAILURE_WINDOW_MINUTES -> 429
FAILURE_WINDOW_MINUTES = 10


class ConsentError(ValueError):
    """Invalid grant parameters or a wrong/expired/used OTP (routers map this to 400)."""


class ConsentRateLimited(ConsentError):
    """Doctor exceeded the failed-redeem cap (routers map this to 429)."""


def _consent_denied() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail=CONSENT_REQUIRED)


def grant_status(grant: ConsentGrant, now=None) -> str:
    """``revoked | pending | otp_expired | active | expired``."""
    now = now or utcnow()
    if grant.revoked_at is not None:
        return "revoked"
    if grant.doctor_id is None:
        return "pending" if grant.otp_expires_at > now else "otp_expired"
    if grant.expires_at is not None and grant.expires_at > now:
        return "active"
    return "expired"


def create_grant(db: Session, patient: User, access_minutes: int, scope: str,
                 ip: str | None = None) -> ConsentGrant:
    if patient.role != "patient":
        raise ConsentError("Only patients can create grants")
    if access_minutes not in ACCESS_MINUTES:
        raise ConsentError(f"access_minutes must be one of {ACCESS_MINUTES}")
    if scope not in SCOPES:
        raise ConsentError(f"scope must be one of {SCOPES}")
    now = utcnow()
    grant = ConsentGrant(patient_id=patient.id, otp=f"{secrets.randbelow(1_000_000):06d}", created_at=now,
                         otp_expires_at=now + timedelta(minutes=OTP_TTL_MINUTES),
                         access_minutes=access_minutes, scope=scope)
    with atomic(db):  # grant + audit row commit together
        db.add(grant)
        db.flush()
        log_event(db, patient, "GRANT_CREATED", patient_id=patient.id,
                  detail={"grant_id": grant.id, "access_minutes": access_minutes, "scope": scope}, ip=ip)
    return grant


def _recent_failures(db: Session, doctor_id: int, now) -> int:
    return db.scalar(select(func.count()).select_from(AuditLog).where(
        AuditLog.action == "GRANT_REDEEM_FAILED", AuditLog.actor_id == doctor_id,
        AuditLog.ts >= now - timedelta(minutes=FAILURE_WINDOW_MINUTES))) or 0


def _find_pending(db: Session, patient_id: int, otp: str, now) -> ConsentGrant | None:
    return db.scalar(
        select(ConsentGrant)
        .where(ConsentGrant.patient_id == patient_id, ConsentGrant.otp == otp,
               ConsentGrant.doctor_id.is_(None), ConsentGrant.revoked_at.is_(None),
               ConsentGrant.otp_expires_at > now)
        .order_by(ConsentGrant.id.desc()).limit(1))


def _record_failure(db: Session, doctor: User, dhid: str, profile: PatientProfile | None, now, reason: str,
                    ip: str | None = None) -> None:
    """Count the failure against every pending grant of that patient (revoking at the threshold) and audit it.
    Must be called inside ``atomic(db)``."""
    revoked: list[int] = []
    if profile is not None:
        pending = db.scalars(select(ConsentGrant).where(
            ConsentGrant.patient_id == profile.user_id, ConsentGrant.doctor_id.is_(None),
            ConsentGrant.revoked_at.is_(None), ConsentGrant.otp_expires_at > now)).all()
        for g in pending:
            g.failed_attempts = (g.failed_attempts or 0) + 1
            if g.failed_attempts >= MAX_FAILED_ATTEMPTS:
                g.revoked_at = now
                revoked.append(g.id)
    log_event(db, doctor, "GRANT_REDEEM_FAILED", patient_id=profile.user_id if profile else None,
              detail={"doctor_id": doctor.id, "dhid": dhid, "reason": reason, "revoked_grant_ids": revoked}, ip=ip)


def redeem_grant(db: Session, doctor: User, dhid: str, otp: str, ip: str | None = None) -> ConsentGrant:
    """Bind a pending grant to ``doctor``. Raises ``ConsentRateLimited`` when the doctor has too many recent
    failures, ``ConsentError`` for a wrong/expired/used OTP (each failure is counted and audited)."""
    if doctor.role != "doctor":
        raise ConsentError("Only doctors can redeem grants")
    dhid = (dhid or "").strip().upper()
    otp = (otp or "").strip()
    now = utcnow()
    if _recent_failures(db, doctor.id, now) >= MAX_DOCTOR_FAILURES:
        raise ConsentRateLimited("Too many failed attempts; try again later")
    profile = db.scalar(select(PatientProfile).where(PatientProfile.dhid == dhid))
    grant = _find_pending(db, profile.user_id, otp, now) if profile is not None and otp else None
    if grant is None:
        with atomic(db):  # failure counters + GRANT_REDEEM_FAILED commit together
            _record_failure(db, doctor, dhid, profile, now, "wrong_or_expired_otp", ip=ip)
        raise ConsentError("Invalid or expired OTP")
    expires_at = now + timedelta(minutes=grant.access_minutes)
    with atomic(db):  # claim + GRANT_REDEEMED commit together; any exception rolls both back
        # Conditional claim: only one concurrent redeemer can bind the grant.
        res = db.execute(update(ConsentGrant)
                         .where(ConsentGrant.id == grant.id, ConsentGrant.doctor_id.is_(None),
                                ConsentGrant.revoked_at.is_(None))
                         .values(doctor_id=doctor.id, expires_at=expires_at)
                         .execution_options(synchronize_session=False))
        if res.rowcount != 1:
            raise ConsentError("Invalid or expired OTP")
        db.refresh(grant)
        log_event(db, doctor, "GRANT_REDEEMED", patient_id=grant.patient_id,
                  detail={"grant_id": grant.id, "scope": grant.scope, "expires_at": isoz(grant.expires_at)}, ip=ip)
    return grant


def revoke_grant(db: Session, patient: User, grant_id: int, ip: str | None = None) -> ConsentGrant | None:
    """Revoke the patient's own grant. Returns None if it doesn't exist / isn't theirs."""
    grant = db.get(ConsentGrant, grant_id)
    if grant is None or grant.patient_id != patient.id:
        return None
    if grant.revoked_at is None:
        try:
            with atomic(db):  # revocation + audit row commit together
                grant.revoked_at = utcnow()
                db.flush()
                log_event(db, patient, "GRANT_REVOKED", patient_id=patient.id, detail={"grant_id": grant.id},
                          ip=ip)
        except BaseException:
            db.expire(grant)  # drop the rolled-back in-memory revoked_at
            raise
    return grant


def active_grant(db: Session, doctor_id: int, patient_id: int) -> ConsentGrant | None:
    """Best currently-valid grant (prefers read_write, then latest expiry), or None."""
    now = utcnow()
    grants = db.scalars(
        select(ConsentGrant).where(ConsentGrant.doctor_id == doctor_id, ConsentGrant.patient_id == patient_id,
                                   ConsentGrant.revoked_at.is_(None), ConsentGrant.expires_at.is_not(None),
                                   ConsentGrant.expires_at > now)).all()
    if not grants:
        return None
    return max(grants, key=lambda g: (g.scope == "read_write", g.expires_at))


def active_grants_for_doctor(db: Session, doctor_id: int) -> list[ConsentGrant]:
    """All valid grants a doctor holds (one per patient, best grant kept) — for GET /doctor/patients."""
    now = utcnow()
    grants = db.scalars(
        select(ConsentGrant).where(ConsentGrant.doctor_id == doctor_id, ConsentGrant.revoked_at.is_(None),
                                   ConsentGrant.expires_at.is_not(None), ConsentGrant.expires_at > now)).all()
    best: dict[int, ConsentGrant] = {}
    for g in grants:
        cur = best.get(g.patient_id)
        if cur is None or (g.scope == "read_write", g.expires_at) > (cur.scope == "read_write", cur.expires_at):
            best[g.patient_id] = g
    return sorted(best.values(), key=lambda g: g.expires_at)


def patient_access(write: bool = False):
    """Dependency factory. Reads path param ``patient_id``; allows the patient themself or a doctor with an
    active grant (``read_write`` scope when ``write``). Admins get no clinical access. Doctor accesses are
    audited as ``VIEW_RECORD``. Returns ``(user, patient_user)``; otherwise 403 ``CONSENT_REQUIRED``."""

    def _dependency(patient_id: int, request: Request, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)) -> tuple[User, User]:
        if user.role == "patient":
            if user.id != patient_id:
                raise _consent_denied()
            return user, user
        if user.role == "doctor":
            grant = active_grant(db, user.id, patient_id)
            if grant is None or (write and grant.scope != "read_write"):
                raise _consent_denied()
            patient = db.get(User, patient_id)
            if patient is None or patient.role != "patient":
                raise _consent_denied()
            log_event(db, user, "VIEW_RECORD", patient_id=patient_id,
                      detail={"method": request.method, "path": request.url.path, "grant_id": grant.id},
                      ip=request_ip(request))
            return user, patient
        raise _consent_denied()

    return _dependency
