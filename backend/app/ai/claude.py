"""ClaudeProvider: structured digitisation via ``messages.parse`` and doctor Q&A via the beta tool runner.

SDK usage follows the bundled claude-api skill docs (python/README.md: vision base64 blocks, error handling,
stop reasons; python/tool-use.md: tool runner + ``@beta_tool``, structured outputs with ``messages.parse``).
Model ``settings.ai_model`` (default claude-opus-5-5): no ``thinking`` param (adaptive is the default and
always on), ``output_config={"effort": "medium"}``, no forced ``tool_choice`` (400 on this model).
"""
from __future__ import annotations

import base64
import logging
import re
from typing import Any

import anthropic
from sqlalchemy.orm import Session

from app.ai import tools
from app.ai.base import DigitiseError
from app.ai.prompts import ANSWER_SYSTEM, DIGITISE_SYSTEM, DIGITISE_USER_TEXT
from app.config import settings
from app.schemas import AskResponse, ExtractedContent, ExtractionResult, ToolCallInfo

log = logging.getLogger(__name__)

MAX_TOKENS = 16000
EFFORT = {"effort": "medium"}
MAX_ITERATIONS = 8
MAX_IMAGES = 20
MAX_TEXT_CHARS = 150_000
MAX_PDF_PAGES = 100
MAX_PDF_BYTES = 32 * 1024 * 1024
MAX_HISTORY = 20
CLIENT_TIMEOUT = 120
CLIENT_MAX_RETRIES = 2
FINAL_NUDGE = ("Tool budget reached. Do not call more tools: answer the question now from the tool results "
               "above, citing sources as [doc:ID] or [doc:ID p N], and say what could not be checked.")
_DELIM_RE = re.compile(r"</?\s*document_text\s*>", re.IGNORECASE)


def _b64(data: bytes) -> str:
    return base64.standard_b64encode(data).decode("utf-8")


def build_digitise_blocks(content: ExtractedContent, filename: str) -> list[dict]:
    """Content blocks for one document: the original PDF as a ``document`` block when available and small
    enough, otherwise up to 20 page images; always followed by a text block with filename + extracted text."""
    blocks: list[dict] = []
    pdf = content.meta.get("pdf_bytes") if content.meta else None
    pages = content.meta.get("page_count") or content.pages or 0
    if pdf and pages <= MAX_PDF_PAGES and len(pdf) <= MAX_PDF_BYTES:
        blocks.append({"type": "document",
                       "source": {"type": "base64", "media_type": "application/pdf", "data": _b64(pdf)}})
    else:
        for png, _page in (content.images or [])[:MAX_IMAGES]:
            blocks.append({"type": "image",
                           "source": {"type": "base64", "media_type": "image/png", "data": _b64(png)}})
    text = _DELIM_RE.sub("[tag removed]", content.text or "")  # the document cannot close our delimiter
    if len(text) > MAX_TEXT_CHARS:
        text = text[:MAX_TEXT_CHARS] + "\n[... text truncated ...]"
    blocks.append({"type": "text", "text": DIGITISE_USER_TEXT.format(
        filename=filename, pages=pages or "unknown", text=text.strip() or "(no text layer — read the attached "
                                                                          "page images/document)")})
    return blocks


def _history_messages(history: list[dict]) -> list[dict]:
    """Keep only plain-text user/assistant turns; the conversation must start with a user turn."""
    msgs = [{"role": h["role"], "content": h["content"]} for h in (history or [])[-MAX_HISTORY:]
            if isinstance(h, dict) and h.get("role") in ("user", "assistant")
            and isinstance(h.get("content"), str) and h["content"].strip()]
    while msgs and msgs[0]["role"] != "user":
        msgs.pop(0)
    return msgs


def _final_text(message: Any) -> str:
    if message is None:
        return ""
    return "\n".join(b.text for b in (message.content or []) if getattr(b, "type", None) == "text").strip()


class ClaudeProvider:
    def __init__(self, name: str = "claude", client: Any = None) -> None:
        self.name = name
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key or None,
                                               timeout=CLIENT_TIMEOUT, max_retries=CLIENT_MAX_RETRIES)
        return self._client

    # -- digitise ---------------------------------------------------------------
    def digitise(self, content: ExtractedContent, filename: str) -> ExtractionResult:
        try:
            response = self.client.messages.parse(
                model=settings.ai_model,
                max_tokens=MAX_TOKENS,
                system=DIGITISE_SYSTEM,
                messages=[{"role": "user", "content": build_digitise_blocks(content, filename)}],
                output_format=ExtractionResult,
                output_config=dict(EFFORT),
            )
        except anthropic.APIError as exc:
            _log_api_error(f"Claude digitise failed for {filename}", exc)
            raise DigitiseError(f"Claude API error ({type(exc).__name__}): {exc}") from exc
        except Exception as exc:  # e.g. JSON validation of a cut-off response, or an SDK bug
            log.exception("Unexpected error during Claude digitise of %s", filename)
            raise DigitiseError(f"Claude digitise error ({type(exc).__name__}): {exc}") from exc
        if response.stop_reason == "refusal":
            raise DigitiseError("Claude declined to digitise this document (refusal)")
        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise DigitiseError(f"Claude returned no structured output (stop_reason={response.stop_reason})")
        if not isinstance(parsed, ExtractionResult):
            parsed = ExtractionResult.model_validate(parsed)
        return parsed

    # -- answer -----------------------------------------------------------------
    def _make_tools(self, db: Session, patient_id: int, calls: list[ToolCallInfo], rows: list[dict]) -> list:
        """``@beta_tool`` closures bound to (db, patient_id). The model cannot pass a patient id."""
        from anthropic import beta_tool

        def make(spec: dict):
            def fn(**kwargs) -> str:
                try:
                    result = tools.run_tool(db, patient_id, spec["name"], kwargs)
                except Exception:
                    calls.append(ToolCallInfo(name=spec["name"], args=kwargs, row_count=0))
                    raise  # the tool runner returns it to the model as an is_error tool_result
                calls.append(ToolCallInfo(name=spec["name"], args=kwargs, row_count=tools.row_count(result)))
                text, sent_rows = tools.serialise_for_model(result)
                rows.extend(sent_rows)  # only rows the model saw can back a citation
                return text

            fn.__name__ = spec["name"]
            return beta_tool(name=spec["name"], description=spec["description"],
                             input_schema=tools.input_schema(spec))(fn)

        return [make(s) for s in tools.TOOL_SPECS]

    def _fallback(self, db, patient_id, question, history, warning: str) -> AskResponse:
        from app.ai.offline import OfflineProvider

        resp = OfflineProvider().answer(db, patient_id, question, history)
        resp.warnings = [warning, *resp.warnings]
        return resp

    def answer(self, db: Session, patient_id: int, question: str, history: list[dict]) -> AskResponse:
        calls: list[ToolCallInfo] = []
        rows: list[dict] = []
        messages = _history_messages(history) + [{"role": "user", "content": question}]
        warnings: list[str] = []
        try:
            tool_objs = self._make_tools(db, patient_id, calls, rows)
            runner = self.client.beta.messages.tool_runner(
                model=settings.ai_model,
                max_tokens=MAX_TOKENS,
                system=ANSWER_SYSTEM,
                tools=tool_objs,
                messages=messages,
                output_config=dict(EFFORT),
                max_iterations=MAX_ITERATIONS,
            )
            last = None
            for message in runner:
                last = message
            if getattr(last, "stop_reason", None) == "tool_use":
                last = self._final_answer(runner, tool_objs)
                warnings.append(f"AI used its {MAX_ITERATIONS}-round tool budget; answer may be incomplete")
        except anthropic.APIError as exc:
            _log_api_error("Claude answer failed", exc)
            return self._fallback(db, patient_id, question, history,
                                  f"AI unavailable ({type(exc).__name__}); offline answer shown")
        except Exception as exc:  # never fail the doctor's request, but keep the traceback
            log.exception("Unexpected error during Claude answer")
            return self._fallback(db, patient_id, question, history,
                                  f"AI unavailable ({type(exc).__name__}); offline answer shown")
        stop = getattr(last, "stop_reason", None)
        if stop == "refusal":
            return self._fallback(db, patient_id, question, history,
                                  "AI declined to answer (refusal); offline answer shown")
        text = _final_text(last)
        if not text:
            return self._fallback(db, patient_id, question, history,
                                  f"AI returned no answer (stop_reason={stop}); offline answer shown")
        if stop == "max_tokens":
            warnings.append("AI answer was cut off (max_tokens)")
        return AskResponse(answer=text, citations=tools.resolve_citations(db, patient_id, text, rows,
                                                                          restrict_to_rows=True),
                           tool_calls=calls, mode="claude", warnings=warnings)

    def _final_answer(self, runner: Any, tool_objs: list):
        """The runner stopped at the iteration cap with tool results pending an answer: make one last call that
        may not use tools. Tool definitions are still sent because the history contains tool_use blocks."""
        params = getattr(runner, "_params", None) or {}
        history = list(params.get("messages") or [])
        if not history:
            raise RuntimeError("tool runner history unavailable for final answer")
        return self.client.beta.messages.create(
            model=settings.ai_model,
            max_tokens=MAX_TOKENS,
            system=ANSWER_SYSTEM,
            tools=[t.to_dict() for t in tool_objs],
            tool_choice={"type": "none"},
            messages=[*history, {"role": "user", "content": FINAL_NUDGE}],
            output_config=dict(EFFORT),
        )


def _log_api_error(what: str, exc: Exception) -> None:
    # A 400 means our request is wrong (a bug worth noticing); other API errors are operational.
    level = logging.ERROR if isinstance(exc, anthropic.BadRequestError) else logging.WARNING
    log.log(level, "%s: %s: %s", what, type(exc).__name__, exc)
