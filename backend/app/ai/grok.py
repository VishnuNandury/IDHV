"""GrokProvider: xAI's Grok via its OpenAI-compatible API (``https://api.x.ai/v1``, OpenAI Python SDK).

Digitisation uses structured outputs (``chat.completions.parse`` with ``response_format=ExtractionResult``);
doctor Q&A runs a manual tool-calling loop over the same patient-bound tools as ClaudeProvider. Prompts,
tool specs, result caps and citation rules are shared with Claude (app.ai.prompts / app.ai.tools).
Chat Completions has no native PDF block, so PDFs go as extracted text plus any rendered page images.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import openai
from sqlalchemy.orm import Session

from app.ai import tools
from app.ai.base import DigitiseError
from app.ai.prompts import ANSWER_SYSTEM, DIGITISE_SYSTEM, DIGITISE_USER_TEXT
from app.config import settings
from app.schemas import AskResponse, ExtractedContent, ExtractionResult, ToolCallInfo

log = logging.getLogger(__name__)

MAX_TOKENS = 16000
MAX_ITERATIONS = 8
MAX_IMAGES = 20
MAX_TEXT_CHARS = 150_000
MAX_HISTORY = 20
CLIENT_TIMEOUT = 120
CLIENT_MAX_RETRIES = 2
FINAL_NUDGE = ("Tool budget reached. Do not call more tools: answer the question now from the tool results "
               "above, citing sources as [doc:ID] or [doc:ID p N], and say what could not be checked.")
_DELIM_RE = re.compile(r"</?\s*document_text\s*>", re.IGNORECASE)


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.standard_b64encode(png).decode("utf-8")


def build_digitise_content(content: ExtractedContent, filename: str, max_images: int = MAX_IMAGES) -> list[dict]:
    """User content parts: up to ``max_images`` page images, then the filename + delimited extracted text."""
    parts: list[dict] = [{"type": "image_url", "image_url": {"url": _data_url(png), "detail": "high"}}
                         for png, _page in (content.images or [])[:max_images]]
    pages = (content.meta or {}).get("page_count") or content.pages or 0
    text = _DELIM_RE.sub("[tag removed]", content.text or "")  # the document cannot close our delimiter
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n[... text truncated ...]"
    parts.append({"type": "text", "text": DIGITISE_USER_TEXT.format(
        filename=filename, pages=pages or "unknown",
        text=text.strip() or "(no text layer — read the attached page images)")})
    return parts


def _history_messages(history: list[dict]) -> list[dict]:
    msgs = [{"role": h["role"], "content": h["content"]} for h in (history or [])[-MAX_HISTORY:]
            if isinstance(h, dict) and h.get("role") in ("user", "assistant")
            and isinstance(h.get("content"), str) and h["content"].strip()]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


def _tool_defs() -> list[dict]:
    return [{"type": "function", "function": {"name": s["name"], "description": s["description"],
                                              "parameters": tools.input_schema(s)}}
            for s in tools.TOOL_SPECS]


class GrokProvider:
    """Also the base for other OpenAI-compatible providers (see app.ai.groq): override the hooks below."""

    default_name = "grok"
    label = "Grok"
    max_images = MAX_IMAGES
    max_tokens = MAX_TOKENS

    def __init__(self, name: str | None = None, client: Any = None) -> None:
        self.name = name or self.default_name
        self._client = client

    def _api_key(self) -> str:
        return settings.xai_api_key

    def _base_url(self) -> str:
        return settings.xai_base_url

    def _text_model(self) -> str:
        return settings.grok_model

    def _digitise_model(self, content: ExtractedContent) -> str:
        return self._text_model()

    @property
    def client(self):
        if self._client is None:
            self._client = openai.OpenAI(api_key=self._api_key() or None, base_url=self._base_url(),
                                         timeout=CLIENT_TIMEOUT, max_retries=CLIENT_MAX_RETRIES)
        return self._client

    # -- digitise ---------------------------------------------------------------
    def _parse_json_mode(self, model: str, user: list[dict]) -> ExtractionResult:
        """Fallback for models without strict structured outputs: JSON mode + the schema in the prompt."""
        schema = json.dumps(ExtractionResult.model_json_schema())
        system = (f"{DIGITISE_SYSTEM}\n\nRespond with a single JSON object (no prose, no code fences) that "
                  f"conforms to this JSON Schema:\n{schema}")
        message = self.client.chat.completions.create(
            model=model, max_tokens=self.max_tokens, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        ).choices[0].message
        return ExtractionResult.model_validate_json(message.content or "")

    def digitise(self, content: ExtractedContent, filename: str) -> ExtractionResult:
        model = self._digitise_model(content)
        user = build_digitise_content(content, filename, self.max_images)
        try:
            try:
                response = self.client.chat.completions.parse(
                    model=model,
                    max_tokens=self.max_tokens,
                    messages=[{"role": "system", "content": DIGITISE_SYSTEM}, {"role": "user", "content": user}],
                    response_format=ExtractionResult,
                )
            except openai.BadRequestError as exc:  # e.g. the model rejects json_schema response_format
                log.warning("%s structured output rejected (%s); retrying in JSON mode", self.label, exc)
                return self._parse_json_mode(model, user)
        except openai.APIError as exc:
            _log_api_error(f"{self.label} digitise failed for {filename}", exc)
            raise DigitiseError(f"{self.label} API error ({type(exc).__name__}): {exc}") from exc
        except Exception as exc:  # e.g. a cut-off response failing validation
            log.exception("Unexpected error during %s digitise of %s", self.label, filename)
            raise DigitiseError(f"{self.label} digitise error ({type(exc).__name__}): {exc}") from exc
        message = response.choices[0].message
        if getattr(message, "refusal", None):
            raise DigitiseError(f"{self.label} declined to digitise this document (refusal)")
        parsed = getattr(message, "parsed", None)
        if parsed is None:
            raise DigitiseError(f"{self.label} returned no structured output")
        if not isinstance(parsed, ExtractionResult):
            parsed = ExtractionResult.model_validate(parsed)
        return parsed

    # -- answer -----------------------------------------------------------------
    def _run_tool(self, db: Session, patient_id: int, call: Any, calls: list[ToolCallInfo],
                  rows: list[dict]) -> str:
        """Execute one model tool call, bound to this patient. Errors go back to the model as text."""
        name = call.function.name
        try:
            args = json.loads(call.function.arguments or "{}")
            if not isinstance(args, dict):
                raise ValueError("tool arguments must be a JSON object")
            result = tools.run_tool(db, patient_id, name, args)
        except Exception as exc:
            calls.append(ToolCallInfo(name=name, args={}, row_count=0))
            return f"Error: {exc}"
        calls.append(ToolCallInfo(name=name, args=args, row_count=tools.row_count(result)))
        text, sent_rows = tools.serialise_for_model(result)
        rows.extend(sent_rows)  # only rows the model saw can back a citation
        return text

    def _fallback(self, db, patient_id, question, history, warning: str) -> AskResponse:
        from app.ai.offline import OfflineProvider

        resp = OfflineProvider().answer(db, patient_id, question, history)
        resp.warnings = [warning, *resp.warnings]
        return resp

    def answer(self, db: Session, patient_id: int, question: str, history: list[dict]) -> AskResponse:
        calls: list[ToolCallInfo] = []
        rows: list[dict] = []
        warnings: list[str] = []
        messages: list[dict] = [{"role": "system", "content": ANSWER_SYSTEM}, *_history_messages(history),
                                {"role": "user", "content": question}]
        defs = _tool_defs()
        try:
            message = None
            for _ in range(MAX_ITERATIONS):
                choice = self.client.chat.completions.create(
                    model=self._text_model(), max_tokens=self.max_tokens, messages=messages, tools=defs,
                ).choices[0]
                message = choice.message
                if not message.tool_calls:
                    break
                messages.append({"role": "assistant", "content": message.content or "",
                                 "tool_calls": [{"id": c.id, "type": "function",
                                                 "function": {"name": c.function.name,
                                                              "arguments": c.function.arguments}}
                                                for c in message.tool_calls]})
                for c in message.tool_calls:
                    messages.append({"role": "tool", "tool_call_id": c.id,
                                     "content": self._run_tool(db, patient_id, c, calls, rows)})
            else:  # tool budget used up with results still pending: one last call that may not use tools
                warnings.append(f"AI used its {MAX_ITERATIONS}-round tool budget; answer may be incomplete")
                message = self.client.chat.completions.create(
                    model=self._text_model(), max_tokens=self.max_tokens, tools=defs, tool_choice="none",
                    messages=[*messages, {"role": "user", "content": FINAL_NUDGE}],
                ).choices[0].message
        except openai.APIError as exc:
            _log_api_error(f"{self.label} answer failed", exc)
            return self._fallback(db, patient_id, question, history,
                                  f"AI unavailable ({type(exc).__name__}); offline answer shown")
        except Exception as exc:  # never fail the doctor's request, but keep the traceback
            log.exception("Unexpected error during %s answer", self.label)
            return self._fallback(db, patient_id, question, history,
                                  f"AI unavailable ({type(exc).__name__}); offline answer shown")
        if getattr(message, "refusal", None):
            return self._fallback(db, patient_id, question, history,
                                  "AI declined to answer (refusal); offline answer shown")
        text = (getattr(message, "content", None) or "").strip()
        if not text:
            return self._fallback(db, patient_id, question, history,
                                  "AI returned no answer; offline answer shown")
        return AskResponse(answer=text, citations=tools.resolve_citations(db, patient_id, text, rows,
                                                                          restrict_to_rows=True),
                           tool_calls=calls, mode=self.name, warnings=warnings)


def _log_api_error(what: str, exc: Exception) -> None:
    # A 400 means our request is wrong (a bug worth noticing); other API errors are operational.
    level = logging.ERROR if isinstance(exc, openai.BadRequestError) else logging.WARNING
    log.log(level, "%s: %s: %s", what, type(exc).__name__, exc)
