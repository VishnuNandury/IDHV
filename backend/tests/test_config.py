import pytest

from app.config import normalise_database_url


@pytest.mark.parametrize("raw,expected", [
    ("postgres://u:p@h:5432/db", "postgresql+psycopg://u:p@h:5432/db"),
    ("postgresql://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ("postgresql+psycopg://u:p@h/db", "postgresql+psycopg://u:p@h/db"),
    ("sqlite:///./x.db", "sqlite:///./x.db"),
])
def test_normalise_database_url(raw, expected):
    assert normalise_database_url(raw) == expected


def test_settings_defaults(monkeypatch):
    import app.config as config

    for name in ("DATABASE_URL", "SECRET_KEY", "ANTHROPIC_API_KEY", "AI_MODEL", "SEED_DEMO", "AUTO_COMMIT",
                 "MAX_UPLOAD_MB", "FRONTEND_DIST"):
        monkeypatch.delenv(name, raising=False)
    s = config.Settings()
    assert s.ai_model == "claude-opus-5-5"
    assert s.seed_demo is True and s.auto_commit is False and s.max_upload_mb == 20
    assert s.anthropic_api_key == ""
    assert s.database_url.startswith("sqlite:///")
    assert s.frontend_dist.replace("\\", "/").endswith("frontend/dist")

    monkeypatch.setenv("DATABASE_URL", "postgresql://a:b@host/db")
    monkeypatch.setenv("SEED_DEMO", "false")
    monkeypatch.setenv("AUTO_COMMIT", "true")
    monkeypatch.setenv("MAX_UPLOAD_MB", "5")
    monkeypatch.setenv("AI_MODEL", "claude-x")
    s = config.Settings()
    assert s.database_url == "postgresql+psycopg://a:b@host/db"
    assert (s.seed_demo, s.auto_commit, s.max_upload_mb, s.ai_model) == (False, True, 5, "claude-x")


def test_secret_key_generated_for_non_sqlite(monkeypatch, caplog):
    import app.config as config

    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgres://a:b@h/db")
    with caplog.at_level("WARNING"):
        s1, s2 = config.Settings(), config.Settings()
    assert len(s1.secret_key) >= 32 and s1.secret_key != s2.secret_key
    assert "SECRET_KEY" in caplog.text
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x.db")
    assert config.Settings().secret_key == config.DEV_SECRET_KEY
    monkeypatch.setenv("SECRET_KEY", "explicit-secret-explicit-secret-123")
    monkeypatch.setenv("DATABASE_URL", "postgres://a:b@h/db")
    assert config.Settings().secret_key == "explicit-secret-explicit-secret-123"


def test_isoz():
    from datetime import date, datetime, timedelta, timezone

    from app.db import isoz
    from app.models import Vital, row_to_dict

    assert isoz(None) is None
    assert isoz(datetime(2026, 9, 30, 10, 0, 0, 123456)) == "2026-09-30T10:00:00Z"
    ist = timezone(timedelta(hours=5, minutes=30))
    assert isoz(datetime(2026, 9, 30, 15, 30, tzinfo=ist)) == "2026-09-30T10:00:00Z"
    v = Vital(kind="bp", value="1/1", date=date(2025, 1, 2), patient_id=1)
    assert row_to_dict(v)["date"] == "2025-01-02"  # dates stay YYYY-MM-DD
