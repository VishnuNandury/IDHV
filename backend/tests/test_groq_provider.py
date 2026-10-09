"""GroqProvider (Groq Cloud, OpenAI-compatible API, ``gsk_`` keys) with a fake client — no network."""
from __future__ import annotations

import httpx
import openai
import pytest

from app.ai import base
from app.ai.base import DigitiseError, get_provider
from app.ai.groq import GroqProvider
from app.config import settings
from app.schemas import ExtractedContent, ExtractionResult
from app.seed import seed_demo
from tests.fakes import default_extraction
from tests.test_grok_provider import FakeClient, _choice, _msg, _tool_call

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _bad_request():
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return openai.BadRequestError("json_schema not supported", response=httpx.Response(400, request=req), body=None)


class ParseRejectingClient(FakeClient):
    """parse() fails with 400 (model lacks strict structured outputs); create() returns JSON text."""

    def __init__(self, json_text):
        super().__init__(responses=[_choice(_msg(content=json_text))])
        completions = self.chat.completions

        def parse(**kwargs):
            self.calls.append(("parse", kwargs))
            raise _bad_request()

        completions.parse = parse


@pytest.fixture
def seeded(db):
    return seed_demo(db)


def _select(monkeypatch, anthropic="", xai="", groq="gsk_test"):
    monkeypatch.setattr(base.settings, "anthropic_api_key", anthropic)
    monkeypatch.setattr(base.settings, "xai_api_key", xai)
    monkeypatch.setattr(base.settings, "groq_api_key", groq)
    monkeypatch.setattr(base, "_cache", {})


def test_groq_selected_when_only_groq_key(monkeypatch):
    _select(monkeypatch)
    assert get_provider().name == "groq"


def test_claude_and_grok_take_precedence(monkeypatch):
    _select(monkeypatch, anthropic="sk-test")
    assert get_provider().name == "claude"
    _select(monkeypatch, xai="xai-test")
    assert get_provider().name == "grok"


def test_system_info_reports_groq(client, monkeypatch):
    _select(monkeypatch)
    info = client.get("/api/system/info").json()
    assert info["ai_mode"] == "groq" and info["model"] == settings.groq_model


def test_digitise_images_use_vision_model_capped_at_three():
    fake = FakeClient(parsed=default_extraction())
    content = ExtractedContent(text="Rx", images=[(PNG, i) for i in range(1, 6)], pages=5)
    GroqProvider(client=fake).digitise(content, "scan.pdf")
    kw = fake.calls[0][1]
    assert kw["model"] == settings.groq_vision_model
    parts = kw["messages"][-1]["content"]
    assert sum(p["type"] == "image_url" for p in parts) == 3
    assert kw["max_tokens"] <= 16384


def test_digitise_text_only_uses_text_model():
    fake = FakeClient(parsed=default_extraction())
    GroqProvider(client=fake).digitise(ExtractedContent(text="HbA1c 7.2 %"), "lab.docx")
    assert fake.calls[0][1]["model"] == settings.groq_model


def test_digitise_falls_back_to_json_mode_when_schema_rejected():
    fake = ParseRejectingClient(default_extraction().model_dump_json())
    result = GroqProvider(client=fake).digitise(ExtractedContent(text="HbA1c 7.2 %"), "lab.docx")
    assert isinstance(result, ExtractionResult) and result.doc_type == "lab_report"
    kind, kw = fake.calls[1]
    assert kind == "create" and kw["response_format"] == {"type": "json_object"}
    assert "JSON" in kw["messages"][0]["content"]  # schema instructions added to the system prompt


def test_digitise_json_mode_garbage_raises():
    with pytest.raises(DigitiseError):
        GroqProvider(client=ParseRejectingClient("not json")).digitise(ExtractedContent(text="x"), "a.txt")


def test_answer_uses_text_model_and_reports_groq(db, seeded):
    fake = FakeClient(responses=[
        _choice(_msg(tool_calls=[_tool_call("list_medications", {})]), "tool_calls"),
        _choice(_msg(content="She takes metformin.")),
    ])
    resp = GroqProvider(client=fake).answer(db, seeded["ananya"], "meds?", [])
    assert resp.mode == "groq" and resp.answer == "She takes metformin."
    assert all(kw["model"] == settings.groq_model for _, kw in fake.calls)
