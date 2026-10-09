"""GrokProvider (xAI, OpenAI-compatible API) with a fake client — no network."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from app.ai import base
from app.ai.base import DigitiseError, get_provider
from app.ai.grok import GrokProvider
from app.config import settings
from app.models import Document
from app.schemas import ExtractedContent, ExtractionResult
from app.seed import seed_demo
from tests.fakes import default_extraction

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _msg(content=None, tool_calls=None, refusal=None, parsed=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls, refusal=refusal, parsed=parsed, role="assistant")


def _choice(message, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason=finish)])


def _tool_call(name, args, i=0):
    return SimpleNamespace(id=f"call_{i}", type="function",
                           function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class FakeCompletions:
    def __init__(self, owner):
        self.owner = owner

    def parse(self, **kwargs):
        self.owner.calls.append(("parse", kwargs))
        if self.owner.raise_exc:
            raise self.owner.raise_exc
        return _choice(_msg(parsed=self.owner.parsed, refusal=self.owner.refusal))

    def create(self, **kwargs):
        self.owner.calls.append(("create", kwargs))
        if self.owner.raise_exc:
            raise self.owner.raise_exc
        return self.owner.responses.pop(0)


class FakeClient:
    def __init__(self, parsed=None, refusal=None, raise_exc=None, responses=()):
        self.parsed, self.refusal, self.raise_exc = parsed, refusal, raise_exc
        self.responses = list(responses)
        self.calls = []
        self.chat = SimpleNamespace(completions=FakeCompletions(self))


def _api_error():
    req = httpx.Request("POST", "https://api.x.ai/v1/chat/completions")
    return openai.APIStatusError("boom", response=httpx.Response(500, request=req), body=None)


@pytest.fixture
def seeded(db):
    return seed_demo(db)


# -- provider selection ---------------------------------------------------------

def test_grok_selected_when_only_xai_key(monkeypatch):
    monkeypatch.setattr(base.settings, "anthropic_api_key", "")
    monkeypatch.setattr(base.settings, "xai_api_key", "xai-test")
    monkeypatch.setattr(base, "_cache", {})
    assert get_provider().name == "grok"


def test_claude_wins_when_both_keys(monkeypatch):
    monkeypatch.setattr(base.settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(base.settings, "xai_api_key", "xai-test")
    monkeypatch.setattr(base, "_cache", {})
    assert get_provider().name == "claude"


def test_system_info_reports_grok(client, monkeypatch):
    monkeypatch.setattr(base.settings, "anthropic_api_key", "")
    monkeypatch.setattr(base.settings, "xai_api_key", "xai-test")
    monkeypatch.setattr(base, "_cache", {})
    info = client.get("/api/system/info").json()
    assert info["ai_mode"] == "grok" and info["model"] == settings.grok_model


# -- digitise ---------------------------------------------------------------------

def test_digitise_request_shape():
    fake = FakeClient(parsed=default_extraction())
    content = ExtractedContent(text="HbA1c 7.2 %", images=[(PNG, 1)], pages=1)
    result = GrokProvider(client=fake).digitise(content, "lab.jpg")
    assert isinstance(result, ExtractionResult)
    kind, kw = fake.calls[0]
    assert kind == "parse" and kw["model"] == settings.grok_model and kw["response_format"] is ExtractionResult
    user = kw["messages"][-1]["content"]
    assert any(b["type"] == "image_url" and b["image_url"]["url"].startswith("data:image/png;base64,") for b in user)
    text = [b["text"] for b in user if b["type"] == "text"][0]
    assert "<document_text>" in text and "HbA1c 7.2" in text
    assert kw["messages"][0]["role"] == "system"


def test_digitise_refusal_and_errors_raise():
    with pytest.raises(DigitiseError):
        GrokProvider(client=FakeClient(refusal="no")).digitise(ExtractedContent(text="x"), "a.txt")
    with pytest.raises(DigitiseError):
        GrokProvider(client=FakeClient(raise_exc=_api_error())).digitise(ExtractedContent(text="x"), "a.txt")
    with pytest.raises(DigitiseError):
        GrokProvider(client=FakeClient(parsed=None)).digitise(ExtractedContent(text="x"), "a.txt")


# -- answer -----------------------------------------------------------------------

def test_answer_runs_tools_bound_to_patient_and_cites(db, seeded):
    pid = seeded["ananya"]
    doc = db.query(Document).filter(Document.patient_id == pid).first()
    other = db.query(Document).filter(Document.patient_id == seeded["ravi"]).first()
    fake = FakeClient(responses=[
        _choice(_msg(tool_calls=[_tool_call("get_lab_results", {"test_name": "HbA1c", "patient_id": seeded["ravi"]}),
                                 _tool_call("get_lab_results", {"test_name": "HbA1c"}, 1)]), "tool_calls"),
        _choice(_msg(content=f"HbA1c improved [doc:{doc.id}] [doc:{other.id}]")),
    ])
    resp = GrokProvider(client=fake).answer(db, pid, "HbA1c trend?", [])
    assert resp.mode == "grok" and "HbA1c improved" in resp.answer
    second = fake.calls[1][1]["messages"]
    tool_msgs = [m for m in second if m["role"] == "tool"]
    assert len(tool_msgs) == 2
    assert "Error" in tool_msgs[0]["content"]  # patient_id is not an allowed tool argument
    assert [t.name for t in resp.tool_calls] == ["get_lab_results", "get_lab_results"]
    assert all(c.document_id != other.id for c in resp.citations)  # never another patient's document


def test_answer_iteration_cap_forces_final_answer(db, seeded):
    loop = [_choice(_msg(tool_calls=[_tool_call("list_medications", {}, i)]), "tool_calls") for i in range(8)]
    fake = FakeClient(responses=[*loop, _choice(_msg(content="Final answer"))])
    resp = GrokProvider(client=fake).answer(db, seeded["ananya"], "meds?", [])
    assert resp.answer == "Final answer" and resp.warnings
    assert fake.calls[-1][1]["tool_choice"] == "none"


def test_answer_api_error_falls_back_offline(db, seeded):
    resp = GrokProvider(client=FakeClient(raise_exc=_api_error())).answer(db, seeded["ananya"], "current meds", [])
    assert resp.mode == "offline" and any("AI unavailable" in w for w in resp.warnings)


def test_no_arg_constructor_is_lazy():
    p = GrokProvider()
    assert p.name == "grok" and p._client is None
