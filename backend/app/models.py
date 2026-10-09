"""ORM models (spec section 4) plus small row-serialisation helpers shared by routers and AI tools."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from app.db import Base, isoz, utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), index=True)  # patient|doctor|admin
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    patient_profile: Mapped[PatientProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan")
    doctor_profile: Mapped[DoctorProfile | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan")


class PatientProfile(Base):
    __tablename__ = "patient_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    dhid: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    dob: Mapped[date | None] = mapped_column(Date, nullable=True)
    sex: Mapped[str | None] = mapped_column(String(16), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    occupation: Mapped[str | None] = mapped_column(String(128), nullable=True)
    blood_group: Mapped[str | None] = mapped_column(String(8), nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    allergies: Mapped[list] = mapped_column(JSON, default=list)
    hereditary_history: Mapped[list] = mapped_column(JSON, default=list)  # [{relation, condition}]
    insurance: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {provider, policy_number, coverage}
    family_members: Mapped[list] = mapped_column(JSON, default=list)  # [{name, relation}]
    emergency_contact: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped[User] = relationship(back_populates="patient_profile")

    @property
    def bmi(self) -> float | None:
        if not self.height_cm or not self.weight_kg:
            return None
        m = self.height_cm / 100.0
        return round(self.weight_kg / (m * m), 1)


class DoctorProfile(Base):
    __tablename__ = "doctor_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    license_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    specialization: Mapped[str | None] = mapped_column(String(128), nullable=True)
    qualification: Mapped[str | None] = mapped_column(String(128), nullable=True)
    facility: Mapped[str | None] = mapped_column(String(255), nullable=True)

    user: Mapped[User] = relationship(back_populates="doctor_profile")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    filename: Mapped[str] = mapped_column(Text)
    mime: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="other")  # pdf|docx|xlsx|xls|doc|image|video|text|other
    size: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    doc_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extraction: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    document_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class ClinicalMixin:
    """Shared columns for clinical rows: owner + citation source."""

    patient_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id"), nullable=True, index=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Condition(ClinicalMixin, Base):
    __tablename__ = "conditions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    diagnosed_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    severity: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(Text, default="active")  # active|resolved


class Medication(ClinicalMixin, Base):
    __tablename__ = "medications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    drug_name: Mapped[str] = mapped_column(Text)
    dosage: Mapped[str | None] = mapped_column(Text, nullable=True)
    frequency: Mapped[str | None] = mapped_column(Text, nullable=True)
    route: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration: Mapped[str | None] = mapped_column(Text, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    prescriber: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    instructions: Mapped[str | None] = mapped_column(Text, nullable=True)


class Encounter(ClinicalMixin, Base):
    __tablename__ = "encounters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[str] = mapped_column(Text, default="opd")  # opd|ipd|diagnostic|pharmacy
    date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    facility: Mapped[str | None] = mapped_column(Text, nullable=True)
    doctor_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    specialization: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnosis: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    advice: Mapped[str | None] = mapped_column(Text, nullable=True)
    follow_up_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fee: Mapped[float | None] = mapped_column(Float, nullable=True)


class LabResult(ClinicalMixin, Base):
    __tablename__ = "lab_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    test_name: Mapped[str] = mapped_column(Text, index=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)
    ref_range: Mapped[str | None] = mapped_column(Text, nullable=True)
    flag: Mapped[str | None] = mapped_column(Text, nullable=True)  # normal|high|low|None
    date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    lab_name: Mapped[str | None] = mapped_column(Text, nullable=True)


class Vital(ClinicalMixin, Base):
    __tablename__ = "vitals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    kind: Mapped[str] = mapped_column(Text)  # bp|weight|glucose|pulse|spo2|temp
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(Text, nullable=True)


class ConsentGrant(Base):
    __tablename__ = "consent_grants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    doctor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    otp: Mapped[str] = mapped_column(String(6))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    otp_expires_at: Mapped[datetime] = mapped_column(DateTime)
    access_minutes: Mapped[int] = mapped_column(Integer)  # 60|1440|10080
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    scope: Mapped[str] = mapped_column(String(16), default="read")  # read|read_write
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    patient: Mapped[User] = relationship(foreign_keys=[patient_id])
    doctor: Mapped[User | None] = relationship(foreign_keys=[doctor_id])


class AuditLog(Base):
    """Append-only, hash-chained. Only app.audit.log_event writes here; nothing updates/deletes."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor_role: Mapped[str | None] = mapped_column(String(16), nullable=True)
    patient_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64), unique=True)  # one successor per row -> no forks
    hash: Mapped[str] = mapped_column(String(64))


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _jsonable(value):
    if isinstance(value, datetime):
        return isoz(value)  # ISO-8601 UTC with 'Z'
    if isinstance(value, date):
        return value.isoformat()  # YYYY-MM-DD
    return value


def row_to_dict(row, exclude: tuple[str, ...] = ("content",)) -> dict:
    """Plain dict of a model's column values (dates as ISO strings). Never includes document bytes."""
    return {c.key: _jsonable(getattr(row, c.key)) for c in row.__table__.columns if c.key not in exclude}


def rows_with_sources(db: Session, rows) -> list[dict]:
    """Serialise clinical rows and add ``source_filename`` (one query for all source documents)."""
    rows = list(rows)
    ids = {r.source_document_id for r in rows if getattr(r, "source_document_id", None)}
    names: dict[int, str] = {}
    if ids:
        names = dict(db.execute(select(Document.id, Document.filename).where(Document.id.in_(ids))).all())
    out = []
    for r in rows:
        d = row_to_dict(r)
        d["source_filename"] = names.get(d.get("source_document_id"))
        out.append(d)
    return out


def _age(dob: date | None) -> int | None:
    if dob is None:
        return None
    today = date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def user_to_dict(user: User) -> dict:
    """Public user representation (no password hash) used by auth/admin responses."""
    d = {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role,
         "created_at": _jsonable(user.created_at), "dhid": None}
    if user.patient_profile is not None:
        d["dhid"] = user.patient_profile.dhid
    if user.doctor_profile is not None:
        d["doctor_profile"] = row_to_dict(user.doctor_profile, exclude=("user_id",))
    return d


def profile_to_dict(user: User) -> dict:
    """Patient demographics card: profile columns + identity + computed ``age`` / ``bmi``."""
    p = user.patient_profile
    d = row_to_dict(p) if p is not None else {"user_id": user.id}
    d.update({"full_name": user.full_name, "email": user.email,
              "bmi": p.bmi if p is not None else None, "age": _age(p.dob) if p is not None else None})
    return d
