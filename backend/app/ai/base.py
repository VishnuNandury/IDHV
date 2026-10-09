"""Pluggable AI provider interface and selection."""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from app.config import settings
from app.schemas import AskResponse, ExtractedContent, ExtractionResult

log = logging.getLogger(__name__)


@runtime_checkable
class AIProvider(Protocol):
    name: str

    def digitise(self, content: ExtractedContent, filename: str) -> ExtractionResult: ...

    def answer(self, db: Session, patient_id: int, question: str, history: list[dict]) -> AskResponse: ...


class DigitiseError(RuntimeError):
    """Raised by a provider when it cannot digitise (API error, refusal); pipeline falls back to offline."""


class _NullProvider:
    """Placeholder used only when neither app.ai.claude nor app.ai.offline is importable."""

    name = "offline"

    def digitise(self, content: ExtractedContent, filename: str) -> ExtractionResult:
        return ExtractionResult(warnings=["provider unavailable"])

    def answer(self, db: Session, patient_id: int, question: str, history: list[dict]) -> AskResponse:
        return AskResponse(answer="AI provider not installed on this server.", mode="offline",
                           warnings=["provider unavailable"])


_override: AIProvider | None = None
_cache: dict[tuple, AIProvider] = {}


def set_provider_override(provider: AIProvider | None) -> None:
    """Force ``get_provider()`` to return ``provider`` (tests). ``None`` restores normal selection."""
    global _override
    _override = provider


def _offline() -> AIProvider:
    try:
        from app.ai.offline import OfflineProvider
    except ImportError as exc:
        log.warning("OfflineProvider unavailable (%s); using null provider", exc)
        return _NullProvider()
    return OfflineProvider()


def get_provider() -> AIProvider:
    if _override is not None:
        return _override
    # First configured wins: ANTHROPIC_API_KEY (Claude), XAI_API_KEY (Grok), GROQ_API_KEY (Groq); else offline.
    key = (bool(settings.anthropic_api_key), settings.ai_model, bool(settings.xai_api_key), settings.grok_model,
           bool(settings.groq_api_key), settings.groq_model, settings.groq_vision_model)
    if key not in _cache:
        provider: AIProvider | None = None
        if settings.anthropic_api_key:
            try:
                from app.ai.claude import ClaudeProvider
            except ImportError as exc:
                log.warning("ClaudeProvider unavailable (%s); falling back", exc)
            else:
                provider = ClaudeProvider()
        if provider is None and settings.xai_api_key:
            try:
                from app.ai.grok import GrokProvider
            except ImportError as exc:
                log.warning("GrokProvider unavailable (%s); falling back to offline", exc)
            else:
                provider = GrokProvider()
        if provider is None and settings.groq_api_key:
            try:
                from app.ai.groq import GroqProvider
            except ImportError as exc:
                log.warning("GroqProvider unavailable (%s); falling back to offline", exc)
            else:
                provider = GroqProvider()
        _cache[key] = provider or _offline()
    return _cache[key]
