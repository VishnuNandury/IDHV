from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import verify_chain
from app.auth import require_role
from app.consent import grant_status
from app.db import get_db, utcnow
from app.models import (
    AuditLog,
    ConsentGrant,
    Document,
    Encounter,
    LabResult,
    Medication,
    User,
    row_to_dict,
    user_to_dict,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_role("admin"))])


@router.get("/audit")
def audit(limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0), action: str | None = None,
          db: Session = Depends(get_db)):
    q = select(AuditLog)
    cq = select(func.count()).select_from(AuditLog)
    if action:
        q = q.where(AuditLog.action == action)
        cq = cq.where(AuditLog.action == action)
    rows = db.scalars(q.order_by(AuditLog.id.desc()).offset(offset).limit(limit)).all()
    emails = {}
    actor_ids = {r.actor_id for r in rows if r.actor_id}
    if actor_ids:
        emails = dict(db.execute(select(User.id, User.email).where(User.id.in_(actor_ids))).all())
    items = []
    for r in rows:
        d = row_to_dict(r)
        d["actor_email"] = emails.get(r.actor_id)
        items.append(d)
    return {"total": db.scalar(cq) or 0, "limit": limit, "offset": offset, "items": items}


@router.get("/audit/verify")
def audit_verify(db: Session = Depends(get_db)):
    return verify_chain(db)


@router.get("/users")
def users(db: Session = Depends(get_db)):
    return [user_to_dict(u) for u in db.scalars(select(User).order_by(User.id)).all()]


def _group_count(db: Session, col) -> dict:
    return {k or "unknown": n for k, n in db.execute(select(col, func.count()).group_by(col)).all()}


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    """Anonymised aggregate counts only — no identifiers."""
    now = utcnow()
    grants = db.scalars(select(ConsentGrant)).all()
    grant_counts: dict[str, int] = {}
    for g in grants:
        s = grant_status(g, now)
        grant_counts[s] = grant_counts.get(s, 0) + 1
    users_by_role = {"patient": 0, "doctor": 0, "admin": 0} | _group_count(db, User.role)
    return {
        "users": users_by_role,
        "documents": {"total": db.scalar(select(func.count()).select_from(Document)) or 0,
                      "by_status": _group_count(db, Document.status),
                      "by_doc_type": _group_count(db, Document.doc_type),
                      "by_kind": _group_count(db, Document.kind)},
        "encounters_by_type": _group_count(db, Encounter.type),
        "lab_results": db.scalar(select(func.count()).select_from(LabResult)) or 0,
        "medications": db.scalar(select(func.count()).select_from(Medication)) or 0,
        "grants": grant_counts,
        "audit_events": _group_count(db, AuditLog.action),
    }
