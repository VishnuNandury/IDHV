"""GroqProvider: Groq Cloud (``gsk_`` keys) via its OpenAI-compatible API (``https://api.groq.com/openai/v1``).

Reuses GrokProvider's OpenAI-SDK implementation. Groq differences:
- two models: a vision model for documents with page images (Groq allows at most 3 images per request) and a
  text model for text-only documents and the tool-calling Q&A loop;
- strict structured outputs only exist on some models, so a 400 on ``json_schema`` falls back to JSON mode
  (handled in GrokProvider.digitise);
- output is capped at 8192 tokens, within every listed model's max completion.
"""
from __future__ import annotations

from app.ai.grok import GrokProvider
from app.config import settings
from app.schemas import ExtractedContent

GROQ_MAX_IMAGES = 3
GROQ_MAX_TOKENS = 8192


class GroqProvider(GrokProvider):
    default_name = "groq"
    label = "Groq"
    max_images = GROQ_MAX_IMAGES
    max_tokens = GROQ_MAX_TOKENS

    def _api_key(self) -> str:
        return settings.groq_api_key

    def _base_url(self) -> str:
        return settings.groq_base_url

    def _text_model(self) -> str:
        return settings.groq_model

    def _digitise_model(self, content: ExtractedContent) -> str:
        return settings.groq_vision_model if content.images else settings.groq_model
