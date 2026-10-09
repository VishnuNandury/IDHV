"""File-kind detection from extension + magic bytes (spec section 5, step 1)."""
from __future__ import annotations

import mimetypes
from pathlib import PurePath

# Extension -> kind. Only these extensions are accepted for upload.
EXTENSION_KINDS: dict[str, str] = {
    "pdf": "pdf",
    "doc": "doc", "docx": "docx",
    "xls": "xls", "xlsx": "xlsx",
    "csv": "text", "txt": "text",
    "jpg": "image", "jpeg": "image", "png": "image", "webp": "image",
    "mp4": "video", "mov": "video", "webm": "video", "avi": "video", "mkv": "video",
}
ALLOWED_EXTENSIONS: frozenset[str] = frozenset(EXTENSION_KINDS)

_MIME_OVERRIDES = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "doc": "application/msword", "xls": "application/vnd.ms-excel",
    "webp": "image/webp", "mkv": "video/x-matroska", "webm": "video/webm", "mov": "video/quicktime",
    "avi": "video/x-msvideo", "mp4": "video/mp4", "csv": "text/csv", "txt": "text/plain",
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "pdf": "application/pdf",
}

_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def extension(filename: str) -> str:
    return PurePath(filename or "").suffix.lower().lstrip(".")


def mime_for(filename: str) -> str:
    ext = extension(filename)
    return _MIME_OVERRIDES.get(ext) or mimetypes.guess_type(filename)[0] or "application/octet-stream"


def _magic_kind(head: bytes) -> str | None:
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video"
    if head[4:8] == b"ftyp" or head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video"
    if head.startswith(b"MZ") or head.startswith(b"\x7fELF"):
        return "other"  # executables are never accepted, whatever the extension says
    return None


def sniff_mime(head: bytes) -> str | None:
    """Exact MIME type when the magic bytes are decisive, else None (caller falls back to the extension)."""
    head = head or b""
    if head.startswith(b"%PDF"):
        return "application/pdf"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "video/x-msvideo"
    if head[4:8] == b"ftyp":
        return "video/quicktime" if head[8:12] == b"qt  " else "video/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm" if b"webm" in head[:64] else "video/x-matroska"
    return None


def detect_kind(filename: str, head: bytes) -> str:
    """Return ``pdf|docx|xlsx|xls|doc|image|video|text|other``.

    Image/video extensions must be backed by matching magic bytes, otherwise ``other`` (so e.g. a playlist
    named .mp4 never reaches ffmpeg). Otherwise unambiguous magic bytes (PDF, JPEG/PNG/WebP, MP4/MOV/MKV/WebM/AVI,
    executables) win over the extension.
    Container formats (ZIP for docx/xlsx, OLE for doc/xls) are disambiguated by extension. Anything whose
    extension is not in ``ALLOWED_EXTENSIONS`` and has no recognised magic is ``other``.
    """
    head = head or b""
    ext = extension(filename)
    by_ext = EXTENSION_KINDS.get(ext)
    magic = _magic_kind(head)
    if by_ext in ("image", "video"):
        # media decoders (Pillow, ffmpeg) are attack surface: the content must really be that media type
        return by_ext if magic == by_ext else "other"
    if magic is not None:
        return magic
    if head.startswith(b"PK\x03\x04"):
        return by_ext if by_ext in ("docx", "xlsx") else "other"
    if head.startswith(_OLE):
        return by_ext if by_ext in ("doc", "xls") else "other"
    return by_ext or "other"
