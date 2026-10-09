"""Shared pytest fixtures. Environment is configured BEFORE any ``app`` import so that
``app.config.settings`` / ``app.db.engine`` point at a throwaway SQLite file."""
from __future__ import annotations

import itertools
import os
import tempfile

_TMP_DIR = tempfile.mkdtemp(prefix="idhv-tests-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_TMP_DIR, "test.db").replace("\\", "/")
os.environ["SEED_DEMO"] = "false"
os.environ["AUTO_COMMIT"] = "false"
os.environ["SECRET_KEY"] = "test-secret-key-that-is-long-enough-for-hs256"
os.environ["ANTHROPIC_API_KEY"] = ""  # tests never call the real API
os.environ["BCRYPT_ROUNDS"] = "4"  # fast hashing in tests
os.environ["FRONTEND_DIST"] = os.path.join(_TMP_DIR, "no-frontend")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402,F401  (register tables)
from app.ai.base import set_provider_override  # noqa: E402
from app.auth import create_token, hash_password  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.models import DoctorProfile, PatientProfile, User  # noqa: E402
from tests.fakes import FakeProvider, default_extraction  # noqa: E402,F401  (re-exported)

__all__ = ["FakeProvider", "default_extraction"]

_counter = itertools.count(1)


@pytest.fixture(autouse=True)
def _fresh_db():
    """Empty every table before each test (much faster than drop/create on Windows)."""
    Base.metadata.create_all(engine)  # no-op once tables exist; picks up any later-registered models
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())
    yield
    set_provider_override(None)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_user(db):
    """Factory: ``make_user("patient"|"doctor"|"admin", email=None, password="demo1234", full_name=None, **profile)``."""

    def _make(role: str = "patient", email: str | None = None, password: str = "demo1234",
              full_name: str | None = None, **profile) -> User:
        n = next(_counter)
        user = User(email=email or f"{role}{n}@test.in", password_hash=hash_password(password),
                    full_name=full_name or f"Test {role.title()} {n}", role=role)
        db.add(user)
        db.flush()
        if role == "patient":
            dhid = profile.pop("dhid", f"DH-{1000 + n:04d}-{2000 + n:04d}-{3000 + n:04d}")
            db.add(PatientProfile(user_id=user.id, dhid=dhid, **profile))
        elif role == "doctor":
            profile.setdefault("license_no", f"LIC-{n}")
            profile.setdefault("specialization", "General Medicine")
            db.add(DoctorProfile(user_id=user.id, **profile))
        db.commit()
        db.refresh(user)
        return user

    return _make


@pytest.fixture
def auth_headers():
    def _headers(user: User) -> dict:
        return {"Authorization": f"Bearer {create_token(user.id)}"}

    return _headers


@pytest.fixture
def fake_provider():
    provider = FakeProvider()
    set_provider_override(provider)
    yield provider
    set_provider_override(None)
