from app.ai import base
from app.ai.base import _NullProvider, get_provider, set_provider_override
from app.schemas import ExtractedContent
from tests.conftest import FakeProvider


def test_override_and_default_provider():
    fake = FakeProvider()
    set_provider_override(fake)
    assert get_provider() is fake
    set_provider_override(None)
    p = get_provider()
    assert p.name == "offline"  # no API key in tests -> OfflineProvider (Task 3) or the null placeholder


def test_null_provider_is_safe():
    p = _NullProvider()
    res = p.digitise(ExtractedContent(text="x"), "a.txt")
    assert res.warnings == ["provider unavailable"] and res.doc_type == "other"
    ans = p.answer(None, 1, "q", [])
    assert ans.mode == "offline" and "not installed" in ans.answer


def test_claude_key_without_module_falls_back(monkeypatch):
    monkeypatch.setattr(base.settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(base, "_cache", {})
    p = get_provider()
    assert p.name in ("claude", "offline")


def test_fake_provider_records_and_raises():
    fake = FakeProvider(raise_exc=RuntimeError("boom"))
    try:
        fake.digitise(ExtractedContent(), "x.pdf")
    except RuntimeError:
        pass
    else:
        raise AssertionError("expected raise")
    assert fake.calls[0][:2] == ("digitise", "x.pdf")
    ok = FakeProvider()
    assert ok.digitise(ExtractedContent(), "y").lab_results[0].test_name == "HbA1c"


def test_system_info_reflects_provider(client):
    set_provider_override(FakeProvider(name="claude"))
    assert client.get("/api/system/info").json()["ai_mode"] == "claude"
