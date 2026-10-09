"""Background ingestion: extract -> digitise (with offline fallback) -> needs_review (spec section 5).

Jobs run on an in-process ``ThreadPoolExecutor(max_workers=2)``; each job opens its own ``SessionLocal()``.

Sync vs async: ``enqueue()`` runs the job inline (in the caller's thread) when the module flag ``SYNC`` is
True or env ``INGEST_SYNC`` is truthy; otherwise it is submitted to the pool. Tests set
``monkeypatch.setattr(pipeline, "SYNC", True)`` so uploads finish before the HTTP response returns.

A job first *claims* its document with a conditional UPDATE (queued -> processing); if the claim fails
(already claimed, deleted, committed) the job does nothing. After the claim every failure path ends in
``failed`` + ``error``, so documents never stay stuck in ``processing`` (``resume_pending`` re-queues any
left behind by a crash/restart).
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait

from sqlalchemy import select, update

from app.ai.base import get_provider
from app.ai.offline_extract import offline_digitise
from app.audit import log_event
from app.config import settings
from app.db import SessionLocal
from app.ingestion.commit import commit_extraction, parse_date, strip_nul  # noqa: F401 (strip_nul re-exported)
from app.ingestion.extractors import ExtractionError, extract
from app.ingestion.review import sanitise_extraction
from app.models import Document
from app.schemas import ExtractionResult

log = logging.getLogger(__name__)

SYNC: bool = False
MAX_WORKERS = 2
MAX_STORED_TEXT = 500_000  # chars of extracted_text kept on the document

_executor: ThreadPoolExecutor | None = None
_lock = threading.Lock()
_futures: set[Future] = set()


def _sync_mode() -> bool:
    return SYNC or (os.environ.get("INGEST_SYNC", "").strip().lower() in ("1", "true", "yes", "on"))


def _get_executor() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="ingest")
        return _executor


def _forget(fut: Future) -> None:
    with _lock:
        _futures.discard(fut)


def enqueue(document_id: int) -> None:
    """Schedule ``process_document(document_id)`` (inline when in sync mode)."""
    if _sync_mode():
        process_document(document_id)
        return
    fut = _get_executor().submit(_run_safely, document_id)
    with _lock:
        _futures.add(fut)
    fut.add_done_callback(_forget)  # may run immediately in this thread; _lock is not held here


def wait_idle(timeout: float | None = None) -> bool:
    """Block until every submitted job has finished (tests / shutdown). Returns True if all finished."""
    with _lock:
        pending = list(_futures)
    if not pending:
        return True
    _done, not_done = wait(pending, timeout=timeout)
    return not not_done


def _run_safely(document_id: int) -> None:
    try:
        process_document(document_id)
    except Exception:  # noqa: BLE001 - never let a worker die silently
        log.exception("Ingestion job for document %s crashed", document_id)


def _fail(db, document_id: int, message: str) -> str | None:
    try:
        db.rollback()
        doc = db.get(Document, document_id)
        if doc is None:
            return None
        doc.status = "failed"
        doc.error = strip_nul(message)[:2000]
        db.commit()
        return "failed"
    except Exception:  # noqa: BLE001 - DB itself broken; resume_pending will retry after restart
        log.exception("Could not mark document %s as failed", document_id)
        return "failed"


def _claim(db, document_id: int) -> bool:
    res = db.execute(update(Document).where(Document.id == document_id, Document.status == "queued")
                     .values(status="processing", error=None).execution_options(synchronize_session=False))
    db.commit()
    return res.rowcount == 1


def process_document(document_id: int) -> str | None:
    """Run the full pipeline for one document synchronously. Returns the final status (None if missing).

    Only a ``queued`` document is processed; otherwise the current status is returned untouched."""
    with SessionLocal() as db:
        if not _claim(db, document_id):
            return db.scalar(select(Document.status).where(Document.id == document_id))
        try:
            return _process(db, document_id)
        except ExtractionError as exc:
            return _fail(db, document_id, str(exc))
        except Exception as exc:  # noqa: BLE001 - anything after the claim must end in 'failed'
            log.exception("Ingestion failed for document %s", document_id)
            return _fail(db, document_id, f"Processing failed: {type(exc).__name__}: {exc}")


def _process(db, document_id: int) -> str | None:
    doc = db.get(Document, document_id)
    if doc is None:
        return None
    content = extract(doc.kind, doc.content or b"", doc.filename)

    provider = get_provider()
    provider_name = getattr(provider, "name", "offline")
    try:
        result = provider.digitise(content, doc.filename)
    except Exception as exc:  # noqa: BLE001 - API errors, refusals, bugs: fall back offline
        if provider_name == "offline":
            raise
        log.warning("Provider %s failed on document %s (%s); using offline extraction",
                    provider_name, document_id, exc)
        result = offline_digitise(content, doc.filename)
        result.warnings.append(f"AI digitisation failed ({type(exc).__name__}); used offline extraction")
        provider_name = "offline"
    if not isinstance(result, ExtractionResult):
        raise TypeError(f"provider {provider_name!r} returned {type(result).__name__}, not ExtractionResult")
    # Normalise the provider's output so the stored extraction always passes ReviewedExtraction (the patient can
    # commit it unchanged): bad dates/pages/fees cleared, doc_type/confidence coerced, NULs stripped, sizes capped.
    result = sanitise_extraction(result)

    doc = db.get(Document, document_id)
    if doc is None:  # deleted while we were working
        return None
    doc.extracted_text = strip_nul(content.text or "")[:MAX_STORED_TEXT]
    doc.extraction = result.model_dump(mode="json")
    doc.doc_type = result.doc_type or "other"
    doc.document_date = parse_date(result.document_date)
    doc.provider = provider_name
    doc.status = "needs_review"
    db.commit()

    if settings.auto_commit:
        counts = commit_extraction(db, doc, result)
        try:
            log_event(db, None, "COMMIT_EXTRACTION", patient_id=doc.patient_id,
                      detail={"document_id": doc.id, "counts": counts, "auto": True})
        except Exception:  # noqa: BLE001 - rows are committed; an audit hiccup must not mark the doc failed
            log.exception("Audit log failed after auto-commit of document %s", document_id)
            db.rollback()
        return "committed"
    return doc.status


def resume_pending() -> int:
    """Re-queue documents left ``queued``/``processing`` (e.g. after a restart). Returns how many. Never raises."""
    try:
        with SessionLocal() as db:
            docs = db.scalars(select(Document).where(Document.status.in_(("queued", "processing")))).all()
            ids = [d.id for d in docs]
            for d in docs:
                d.status = "queued"
            db.commit()
    except Exception:  # noqa: BLE001 - startup must not crash
        log.exception("resume_pending: could not query pending documents")
        return 0
    for i in ids:
        try:
            enqueue(i)
        except Exception:  # noqa: BLE001
            log.exception("resume_pending: could not enqueue document %s", i)
    return len(ids)
