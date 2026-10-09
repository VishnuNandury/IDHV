"""Application settings, read once from environment variables."""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_DEFAULT_DIST = _BACKEND_DIR.parent / "frontend" / "dist"
DEV_SECRET_KEY = "dev-insecure-secret-key-change-me-in-production"
log = logging.getLogger("app.config")


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def normalise_database_url(url: str) -> str:
    """Render hands out ``postgres://``; SQLAlchemy + psycopg3 wants ``postgresql+psycopg://``."""
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class Settings:
    def __init__(self) -> None:
        self.database_url: str = normalise_database_url(
            os.environ.get("DATABASE_URL") or f"sqlite:///{(_BACKEND_DIR / 'idhv.db').as_posix()}")
        self.secret_key: str = self._secret_key()
        self.anthropic_api_key: str = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        self.ai_model: str = os.environ.get("AI_MODEL") or "claude-opus-5-5"
        self.xai_api_key: str = (os.environ.get("XAI_API_KEY") or os.environ.get("GROK_API_KEY") or "").strip()
        self.grok_model: str = os.environ.get("GROK_MODEL") or "grok-4.7"
        self.xai_base_url: str = os.environ.get("XAI_BASE_URL") or "https://api.x.ai/v1"
        self.groq_api_key: str = (os.environ.get("GROQ_API_KEY") or "").strip()
        self.groq_model: str = os.environ.get("GROQ_MODEL") or "openai/gpt-oss-120b"
        self.groq_vision_model: str = os.environ.get("GROQ_VISION_MODEL") or "qwen/qwen3.8-27b"
        self.groq_base_url: str = os.environ.get("GROQ_BASE_URL") or "https://api.groq.com/openai/v1"
        self.seed_demo: bool = _bool("SEED_DEMO", True)
        self.auto_commit: bool = _bool("AUTO_COMMIT", False)
        self.max_upload_mb: int = int(os.environ.get("MAX_UPLOAD_MB") or 20)
        self.frontend_dist: str = os.environ.get("FRONTEND_DIST") or str(_DEFAULT_DIST)
        self.bcrypt_rounds: int = int(os.environ.get("BCRYPT_ROUNDS") or 12)
        self.token_ttl_minutes: int = int(os.environ.get("TOKEN_TTL_MINUTES") or 720)


    def _secret_key(self) -> str:
        key = os.environ.get("SECRET_KEY")
        if key:
            return key
        if self.database_url.startswith("sqlite"):
            return DEV_SECRET_KEY  # local dev / tests
        log.warning("SECRET_KEY is not set; generated a random one for this process "
                    "(tokens will not survive a restart). Set SECRET_KEY in production.")
        return secrets.token_urlsafe(32)


settings = Settings()
