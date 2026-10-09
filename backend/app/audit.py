"""Append-only, hash-chained audit log.

hash = sha256(prev_hash + canonical_json(row)) where the canonical row covers every column except
``id``/``hash``/``prev_hash``. Rows are only ever inserted; there is no update/delete code path.
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import AuditLog, User

GENESIS_HASH = "0" * 64
_lock = threading.RLock()  # keeps the chain linear within one process (single Render instance)


def _normalise(detail: dict | None) -> dict | None:
    if detail is None:
        return None
    return json.loads(json.dumps(detail, default=str))


def canonical_row(entry: AuditLog) -> str:
    body = {
        "ts": entry.ts.isoformat() if entry.ts else None,
        "actor_id": entry.actor_id,
        "actor_role": entry.actor_role,
        "patient_id": entry.patient_id,
        "action": entry.action,
        "detail": entry.detail,
        "ip": entry.ip,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def compute_hash(prev_hash: str, entry: AuditLog) -> str:
    return hashlib.sha256((prev_hash + canonical_row(entry)).encode("utf-8")).hexdigest()


def _last_hash(db: Session) -> str | None:
    return db.scalar(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1))


_MAX_RETRIES = 5
_ATOMIC_KEY = "audit_atomic"


@contextmanager
def atomic(db: Session) -> Iterator[Session]:
    """State change + its audit row(s) in ONE transaction.

    Commits any pending work first (outside the lock), then takes the audit lock BEFORE the block's first
    write and holds it until the single commit on exit (rollback on any exception). Inside the block,
    ``log_event`` defaults to ``commit=False``: the row is hashed under the lock and only flushed.

    Deadlock-free: no thread ever waits for ``_lock`` while holding the SQLite write lock, because every
    entry point (``atomic`` and default ``log_event``) commits pending work before acquiring it."""
    if db.info.get(_ATOMIC_KEY):
        yield db  # nested: the outer block owns lock + commit
        return
    db.commit()
    with _lock:
        db.info[_ATOMIC_KEY] = True
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.info.pop(_ATOMIC_KEY, None)


def _new_entry(db: Session, actor_id, actor_role, patient_id, action, detail, ip) -> AuditLog:
    prev = _last_hash(db) or GENESIS_HASH
    entry = AuditLog(ts=utcnow(), actor_id=actor_id, actor_role=actor_role, patient_id=patient_id,
                     action=action, detail=detail, ip=ip, prev_hash=prev)
    entry.hash = compute_hash(prev, entry)
    return entry


def log_event(db: Session, actor: User | None, action: str, patient_id: int | None = None,
              detail: dict | None = None, ip: str | None = None, commit: bool | None = None) -> AuditLog:
    """Append an audit row.

    * Default (outside ``atomic``): COMMITS the caller's pending work first, then — holding the lock across
      read-last-hash + insert + commit — writes the audit row in its own transaction.
    * Inside ``with atomic(db):`` (or ``commit=False``, which requires ``atomic``): only adds + flushes, so
      the row commits or rolls back together with the caller's change.

    ``prev_hash`` is UNIQUE; a conflicting insert (another process won the race) is retried from a fresh
    read of the last hash — via a SAVEPOINT in atomic mode so the caller's work is kept."""
    in_atomic = bool(db.info.get(_ATOMIC_KEY))
    if commit is None:
        commit = not in_atomic
    if not commit and not in_atomic:
        raise RuntimeError("log_event(commit=False) must be called inside 'with audit.atomic(db):'")
    actor_id = actor.id if actor is not None else None
    actor_role = actor.role if actor is not None else None
    normalised = _normalise(detail)

    if in_atomic:  # lock already held by atomic(); never commit here
        for attempt in range(_MAX_RETRIES):
            try:
                with db.begin_nested():
                    entry = _new_entry(db, actor_id, actor_role, patient_id, action, normalised, ip)
                    db.add(entry)
                    db.flush()
                return entry
            except IntegrityError:
                if attempt == _MAX_RETRIES - 1:
                    raise
        raise RuntimeError("unreachable")  # pragma: no cover

    db.commit()
    with _lock:
        for attempt in range(_MAX_RETRIES):
            entry = _new_entry(db, actor_id, actor_role, patient_id, action, normalised, ip)
            db.add(entry)
            try:
                db.commit()
                return entry
            except IntegrityError:
                db.rollback()
                if attempt == _MAX_RETRIES - 1:
                    raise
    raise RuntimeError("unreachable")  # pragma: no cover


def request_ip(request) -> str | None:
    """Client IP of a FastAPI/Starlette request (honours X-Forwarded-For when uvicorn runs with
    ``--proxy-headers``)."""
    client = getattr(request, "client", None) if request is not None else None
    return client.host if client else None


def verify_chain(db: Session) -> dict:
    prev = GENESIS_HASH
    checked = 0
    for entry in db.scalars(select(AuditLog).order_by(AuditLog.id)).yield_per(500):
        if entry.prev_hash != prev or compute_hash(prev, entry) != entry.hash:
            return {"ok": False, "checked": checked, "broken_id": entry.id}
        prev = entry.hash
        checked += 1
    return {"ok": True, "checked": checked, "broken_id": None}
