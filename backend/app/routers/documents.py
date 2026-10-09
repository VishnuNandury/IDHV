"""/api/documents: upload, list, view, download, review-commit, reprocess, delete (spec section 8)."""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session, undefer

from app.audit import log_event
from app.auth import get_current_user, require_role
from app.config import settings
from app.consent import CONSENT_REQUIRED, active_grant
from app.db import get_db, isoz
from app.ingestion import pipeline
from app.ingestion.commit import CommitError, commit_extraction
from app.ingestion.detect import ALLOWED_EXTENSIONS, detect_kind, extension, mime_for, sniff_mime
from app.ingestion.review import ReviewedExtraction
from app.models import Document, User
from app.schemas import ExtractionResult

router = APIRouter(prefix="/documents", tags=["documents"])

MAX_FILES_PER_UPLOAD = 7  # the demo samples fit in one upload


def _reviewed(raw) -> Optional[ReviewedExtraction]:
    """Validate an edited extraction strictly. Errors -> 422 with ``loc`` naming the field. Done by hand (not as
    a typed body) so the error payload never echoes the raw input (e.g. ``Infinity``, which can't be JSON)."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise HTTPException(422, detail=[{"type": "model_type", "loc": ["body"], "msg": "Body must be an object"}])
    if raw.get("extraction") is None:
        return None
    try:
        return ReviewedExtraction.model_validate(raw["extraction"])
    except ValidationError as exc:
        errors = [{"type": e["type"], "loc": ["body", "extraction", *e["loc"]], "msg": e["msg"]}
                  for e in exc.errors(include_url=False, include_input=False, include_context=False)]
        raise HTTPException(422, detail=errors) from exc


def document_to_dict(doc: Document, include_text: bool = False) -> dict:
    d = {
        "id": doc.id, "patient_id": doc.patient_id, "filename": doc.filename, "mime": doc.mime, "kind": doc.kind,
        "size": doc.size, "status": doc.status, "doc_type": doc.doc_type, "provider": doc.provider,
        "error": doc.error, "uploaded_at": isoz(doc.uploaded_at),
        "document_date": doc.document_date.isoformat() if doc.document_date else None,
        "extraction": doc.extraction,
    }
    if include_text:
        d["extracted_text"] = doc.extracted_text
    return d


def _ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def _denied() -> HTTPException:
    return HTTPException(status.HTTP_403_FORBIDDEN, detail=CONSENT_REQUIRED)


def _get_doc(db: Session, document_id: int, with_content: bool = False) -> Document:
    q = select(Document).where(Document.id == document_id)
    if with_content:
        q = q.options(undefer(Document.content))
    doc = db.scalar(q)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc


def _readable_doc(db: Session, user: User, document_id: int, request: Request, action: str,
                  with_content: bool = False) -> Document:
    """Owner patient, or a doctor with an active grant for the document's patient (audited as ``action``)."""
    doc = _get_doc(db, document_id, with_content)
    if user.role == "patient":
        if doc.patient_id != user.id:
            raise _denied()
        return doc
    if user.role == "doctor":
        grant = active_grant(db, user.id, doc.patient_id)
        if grant is None:
            raise _denied()
        log_event(db, user, action, patient_id=doc.patient_id,
                  detail={"document_id": doc.id, "filename": doc.filename, "grant_id": grant.id}, ip=_ip(request))
        return doc
    raise _denied()


def _owned_doc(db: Session, user: User, document_id: int) -> Document:
    doc = _get_doc(db, document_id)
    if user.role != "patient" or doc.patient_id != user.id:
        raise _denied()
    return doc


# ---- upload ------------------------------------------------------------------

@router.post("")
def upload(request: Request, files: list[UploadFile] = File(...), user: User = Depends(require_role("patient")),
           db: Session = Depends(get_db)):
    """Validate each file independently; valid ones are stored ``queued`` and enqueued for processing."""
    limit = settings.max_upload_mb * 1024 * 1024
    accepted: list[Document] = []
    rejected: list[dict] = []
    for index, f in enumerate(files):
        name = (f.filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip() or "upload"
        if index >= MAX_FILES_PER_UPLOAD:
            rejected.append({"filename": name,
                             "reason": f"Too many files in one upload (max {MAX_FILES_PER_UPLOAD})"})
            continue
        ext = extension(name)
        if ext not in ALLOWED_EXTENSIONS:
            rejected.append({"filename": name, "reason": f"Unsupported file type '.{ext}'" if ext
                             else "File has no extension"})
            continue
        data = f.file.read(limit + 1)
        if len(data) > limit:
            rejected.append({"filename": name, "reason": f"File exceeds {settings.max_upload_mb} MB limit"})
            continue
        if not data:
            rejected.append({"filename": name, "reason": "File is empty"})
            continue
        kind = detect_kind(name, data[:64])
        if kind == "other":
            rejected.append({"filename": name, "reason": "File content does not match a supported type"})
            continue
        mime = sniff_mime(data[:64]) or mime_for(name)  # magic bytes decide when they can
        doc = Document(patient_id=user.id, filename=name, mime=mime, kind=kind, size=len(data),
                       content=data, status="queued")
        db.add(doc)
        accepted.append(doc)
    db.commit()
    if accepted or rejected:
        log_event(db, user, "UPLOAD", patient_id=user.id, ip=_ip(request),
                  detail={"accepted": [{"id": d.id, "filename": d.filename, "kind": d.kind, "size": d.size}
                                       for d in accepted],
                          "rejected": rejected})
    for doc in accepted:
        pipeline.enqueue(doc.id)
    out = []
    for doc in accepted:
        db.refresh(doc)  # pick up status changes made by the (possibly inline) pipeline
        out.append(document_to_dict(doc))
    return {"accepted": out, "rejected": rejected}


# ---- read --------------------------------------------------------------------

@router.get("")
def list_documents(request: Request, patient_id: Optional[int] = Query(default=None),
                   status_filter: Optional[str] = Query(default=None, alias="status"),
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Patients: their own documents. Doctors: ``?patient_id=`` with an active grant (audited VIEW_RECORD)."""
    if user.role == "patient":
        if patient_id is not None and patient_id != user.id:
            raise _denied()
        target = user.id
    elif user.role == "doctor":
        if patient_id is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="patient_id is required")
        grant = active_grant(db, user.id, patient_id)
        if grant is None:
            raise _denied()
        log_event(db, user, "VIEW_RECORD", patient_id=patient_id, ip=_ip(request),
                  detail={"method": "GET", "path": request.url.path, "grant_id": grant.id})
        target = patient_id
    else:
        raise _denied()
    q = select(Document).where(Document.patient_id == target)
    if status_filter:
        q = q.where(Document.status == status_filter)
    docs = db.scalars(q.order_by(Document.uploaded_at.desc(), Document.id.desc())).all()
    return [document_to_dict(d) for d in docs]


@router.get("/{document_id}")
def get_document(document_id: int, request: Request, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    return document_to_dict(_readable_doc(db, user, document_id, request, "VIEW_RECORD"), include_text=True)


@router.get("/{document_id}/file")
def get_document_file(document_id: int, request: Request, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    doc = _readable_doc(db, user, document_id, request, "DOWNLOAD_REPORT", with_content=True)
    disposition = f"inline; filename*=UTF-8''{quote(doc.filename)}"
    return Response(content=doc.content or b"", media_type=doc.mime or "application/octet-stream",
                    headers={"Content-Disposition": disposition, "X-Content-Type-Options": "nosniff",
                             "Cache-Control": "private, no-store"})


# ---- mutate (owner patient only) ---------------------------------------------

@router.post("/{document_id}/commit")
def commit_document(document_id: int, request: Request, body: Optional[dict] = Body(default=None),
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Commit the (optionally edited) extraction. Body ``{"extraction": ReviewedExtraction}`` (optional; the
    stored extraction is used when omitted). 422 on invalid edits; 409 if already committed or not in review."""
    doc = _owned_doc(db, user, document_id)
    if doc.status == "committed":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Document already committed")
    if doc.status != "needs_review":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Document is {doc.status}; nothing to commit")
    result = _reviewed(body)
    edited = result is not None
    if result is None:
        if not doc.extraction:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="No extraction to commit")
        result = ExtractionResult.model_validate(doc.extraction)
    try:
        counts = commit_extraction(db, doc, result)
    except CommitError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    log_event(db, user, "COMMIT_EXTRACTION", patient_id=doc.patient_id, ip=_ip(request),
              detail={"document_id": doc.id, "counts": counts, "edited": edited})
    db.refresh(doc)
    return {"document": document_to_dict(doc), "counts": counts}


@router.post("/{document_id}/reprocess")
def reprocess_document(document_id: int, request: Request, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """Clear the previous extraction and run the pipeline again. Only ``needs_review``/``failed`` documents;
    409 when committed, queued or processing (conditional UPDATE, so concurrent requests can't double-queue)."""
    doc = _owned_doc(db, user, document_id)
    if doc.status == "committed":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Committed documents cannot be reprocessed")
    res = db.execute(update(Document)
                     .where(Document.id == doc.id, Document.status.in_(("needs_review", "failed")))
                     .values(status="queued", extraction=None, extracted_text=None, doc_type=None,
                             document_date=None, provider=None, error=None)
                     .execution_options(synchronize_session=False))
    db.commit()
    if res.rowcount != 1:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Document is already queued or being processed")
    log_event(db, user, "REPROCESS_DOCUMENT", patient_id=doc.patient_id, ip=_ip(request),
              detail={"document_id": doc.id})
    pipeline.enqueue(doc.id)
    db.refresh(doc)
    return document_to_dict(doc)


@router.delete("/{document_id}")
def delete_document(document_id: int, request: Request, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Delete an uncommitted document (409 if committed or mid-processing)."""
    doc = _owned_doc(db, user, document_id)
    if doc.status in ("committed", "processing"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Cannot delete a {doc.status} document")
    info = {"document_id": doc.id, "filename": doc.filename}
    db.delete(doc)
    db.commit()
    log_event(db, user, "DELETE_DOCUMENT", patient_id=user.id, ip=_ip(request), detail=info)
    return {"ok": True, "id": document_id}
