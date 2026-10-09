from sqlalchemy import text

from app.audit import log_event, verify_chain
from app.models import AuditLog


def test_chain_verifies_and_detects_tampering(make_user, db):
    p = make_user("patient")
    log_event(db, p, "LOGIN", detail={"ua": "pytest"}, ip="127.0.0.1")
    e2 = log_event(db, p, "UPLOAD", patient_id=p.id, detail={"filename": "a.pdf", "size": 10})
    log_event(db, None, "SYSTEM_EVENT")
    assert e2.prev_hash and len(e2.hash) == 64

    res = verify_chain(db)
    assert res == {"ok": True, "checked": 3, "broken_id": None}

    db.execute(text("UPDATE audit_log SET detail = :d WHERE id = :i"), {"d": '{"filename": "evil.pdf"}', "i": e2.id})
    db.commit()
    db.expire_all()
    res = verify_chain(db)
    assert res["ok"] is False
    assert res["broken_id"] == e2.id


def test_chain_detects_deleted_row(make_user, db):
    p = make_user("patient")
    for i in range(3):
        log_event(db, p, "LOGIN", detail={"i": i})
    ids = [a.id for a in db.query(AuditLog).order_by(AuditLog.id)]
    db.execute(text("DELETE FROM audit_log WHERE id = :i"), {"i": ids[1]})
    db.commit()
    db.expire_all()
    res = verify_chain(db)
    assert res["ok"] is False and res["broken_id"] == ids[2]


def test_empty_chain_ok(db):
    assert verify_chain(db) == {"ok": True, "checked": 0, "broken_id": None}


def test_admin_audit_endpoints(client, make_user, auth_headers, db):
    admin, p = make_user("admin"), make_user("patient")
    log_event(db, p, "LOGIN")
    r = client.get("/api/admin/audit?limit=10&offset=0", headers=auth_headers(admin))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1 and body["items"][0]["action"]
    assert client.get("/api/admin/audit/verify", headers=auth_headers(admin)).json()["ok"] is True
    assert client.get("/api/admin/audit/verify", headers=auth_headers(p)).status_code == 403
    stats = client.get("/api/admin/stats", headers=auth_headers(admin)).json()
    assert stats["users"]["patient"] >= 1
    assert "email" not in str(stats)


def test_prev_hash_unique_and_retry_on_conflict(make_user, db, monkeypatch):
    """A stale last-hash read (another writer won the race) must be retried, not fork the chain."""
    import app.audit as audit

    p = make_user("patient")
    log_event(db, p, "LOGIN")
    real = audit._last_hash
    calls = {"n": 0}

    def stale_then_real(session):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(session)  # first read is stale (pretends chain is empty)

    monkeypatch.setattr(audit, "_last_hash", stale_then_real)
    e = log_event(db, p, "LOGIN")
    assert calls["n"] == 2 and e.prev_hash != audit.GENESIS_HASH
    assert verify_chain(db) == {"ok": True, "checked": 2, "broken_id": None}
    assert AuditLog.__table__.c.prev_hash.unique


def test_log_event_no_deadlock_with_pending_writer(make_user, db, monkeypatch):
    """Thread A holds the audit lock and is about to insert; thread B has flushed (holds the SQLite write lock)
    and then calls log_event. B must release its write lock before waiting on the audit lock."""
    import threading
    import time

    import app.audit as audit
    from app.db import SessionLocal
    from app.models import Condition

    pid = make_user("patient").id
    a_in_lock, b_flushed = threading.Event(), threading.Event()
    real = audit._last_hash
    errors = []

    def slow_last_hash(session):
        if not a_in_lock.is_set():
            a_in_lock.set()
            b_flushed.wait(5)
            time.sleep(0.3)  # let B reach log_event
        return real(session)

    monkeypatch.setattr(audit, "_last_hash", slow_last_hash)

    def run_a():
        s = SessionLocal()
        try:
            log_event(s, None, "A", patient_id=pid)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            s.close()

    def run_b():
        s = SessionLocal()
        try:
            a_in_lock.wait(5)
            s.add(Condition(patient_id=pid, name="pending"))
            s.flush()  # takes the SQLite write lock
            b_flushed.set()
            log_event(s, None, "B", patient_id=pid)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)
        finally:
            s.close()

    t0 = time.monotonic()
    threads = [threading.Thread(target=run_a), threading.Thread(target=run_b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors and not any(t.is_alive() for t in threads)
    assert time.monotonic() - t0 < 10
    assert verify_chain(db) == {"ok": True, "checked": 2, "broken_id": None}
    assert db.query(Condition).count() == 1


# ---- fix round 2: atomic audit ----------------------------------------------------


def _boom(*a, **k):
    raise RuntimeError("audit insert failed")


def test_atomic_rolls_back_state_when_audit_fails(make_user, db, monkeypatch):
    import app.audit as audit
    from app.audit import atomic
    from app.models import Condition

    p = make_user("patient")
    monkeypatch.setattr(audit, "compute_hash", _boom)
    try:
        with atomic(db):
            db.add(Condition(patient_id=p.id, name="should vanish"))
            db.flush()
            log_event(db, p, "X", commit=False)
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected failure")
    db.expire_all()
    assert db.query(Condition).count() == 0 and db.query(AuditLog).count() == 0


def test_atomic_commits_state_and_audit_together(make_user, db):
    from app.audit import atomic
    from app.models import Condition

    p = make_user("patient")
    with atomic(db):
        db.add(Condition(patient_id=p.id, name="kept"))
        db.flush()
        log_event(db, p, "A1", commit=False)
        log_event(db, p, "A2")  # default inside atomic -> no intermediate commit
    db.expire_all()
    assert db.query(Condition).count() == 1
    assert [a.action for a in db.query(AuditLog).order_by(AuditLog.id)] == ["A1", "A2"]
    assert verify_chain(db) == {"ok": True, "checked": 2, "broken_id": None}


def test_commit_false_outside_atomic_rejected(make_user, db):
    import pytest

    p = make_user("patient")
    with pytest.raises(RuntimeError):
        log_event(db, p, "X", commit=False)


def test_atomic_retries_audit_insert_on_conflict(make_user, db, monkeypatch):
    import app.audit as audit
    from app.audit import atomic
    from app.models import Condition

    p = make_user("patient")
    log_event(db, p, "FIRST")
    real = audit._last_hash
    calls = {"n": 0}

    def stale_then_real(session):
        calls["n"] += 1
        return None if calls["n"] == 1 else real(session)

    monkeypatch.setattr(audit, "_last_hash", stale_then_real)
    with atomic(db):
        db.add(Condition(patient_id=p.id, name="kept"))
        db.flush()
        log_event(db, p, "SECOND", commit=False)
    db.expire_all()
    assert calls["n"] == 2
    assert db.query(Condition).count() == 1
    assert verify_chain(db) == {"ok": True, "checked": 2, "broken_id": None}


def test_grant_lifecycle_atomic_with_audit(make_user, db, monkeypatch):
    import pytest

    import app.audit as audit
    from app.consent import create_grant, redeem_grant, revoke_grant
    from app.models import ConsentGrant

    p, d = make_user("patient"), make_user("doctor")
    dhid = p.patient_profile.dhid
    real = audit.compute_hash

    monkeypatch.setattr(audit, "compute_hash", _boom)
    with pytest.raises(RuntimeError):
        create_grant(db, p, 60, "read")
    db.expire_all()
    assert db.query(ConsentGrant).count() == 0

    monkeypatch.setattr(audit, "compute_hash", real)
    g = create_grant(db, p, 60, "read")
    gid, otp = g.id, g.otp

    monkeypatch.setattr(audit, "compute_hash", _boom)
    with pytest.raises(RuntimeError):
        redeem_grant(db, d, dhid, otp)
    db.expire_all()
    assert db.get(ConsentGrant, gid).doctor_id is None

    monkeypatch.setattr(audit, "compute_hash", real)
    redeem_grant(db, d, dhid, otp)

    monkeypatch.setattr(audit, "compute_hash", _boom)
    with pytest.raises(RuntimeError):
        revoke_grant(db, p, gid)
    db.expire_all()
    assert db.get(ConsentGrant, gid).revoked_at is None

    monkeypatch.setattr(audit, "compute_hash", real)
    assert revoke_grant(db, p, gid).revoked_at is not None
    actions = [a.action for a in db.query(AuditLog).order_by(AuditLog.id)]
    assert actions == ["GRANT_CREATED", "GRANT_REDEEMED", "GRANT_REVOKED"]
    assert verify_chain(db)["ok"] is True


def test_failed_redeem_counter_atomic_with_audit(make_user, db, monkeypatch):
    import pytest

    import app.audit as audit
    from app.consent import ConsentError, create_grant, redeem_grant
    from app.models import ConsentGrant

    p, d = make_user("patient"), make_user("doctor")
    g = create_grant(db, p, 60, "read")
    monkeypatch.setattr(audit, "compute_hash", _boom)
    with pytest.raises(RuntimeError):
        redeem_grant(db, d, p.patient_profile.dhid, "000000" if g.otp != "000000" else "111111")
    db.expire_all()
    assert db.get(ConsentGrant, g.id).failed_attempts == 0
    assert not isinstance(RuntimeError(), ConsentError)


def test_seed_is_all_or_nothing(db, monkeypatch):
    import pytest

    import app.audit as audit
    from app.models import ConsentGrant, Document, User
    from app.seed import seed_demo

    monkeypatch.setattr(audit, "compute_hash", _boom)
    with pytest.raises(RuntimeError):
        seed_demo(db)
    db.expire_all()
    assert db.query(User).count() == 0 and db.query(Document).count() == 0
    assert db.query(ConsentGrant).count() == 0
