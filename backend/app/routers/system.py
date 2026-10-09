from __future__ import annotations

import shutil

from fastapi import APIRouter

from app.ai.base import get_provider
from app.config import settings

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/info")
def info():
    name = getattr(get_provider(), "name", "offline")
    ai_mode = "claude" if name == "claude" else "offline"
    return {
        "ai_mode": ai_mode,
        "provider": name,
        "model": settings.ai_model if ai_mode == "claude" else None,
        "ocr_available": shutil.which("tesseract") is not None,
        "ffmpeg_available": shutil.which("ffmpeg") is not None,
        "max_upload_mb": settings.max_upload_mb,
    }
