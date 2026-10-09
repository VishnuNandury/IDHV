"""FastAPI application: API routers under /api, SPA static hosting, startup (create tables, seed, resume jobs)."""
from __future__ import annotations

import importlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal, init_db
from app.models import User

log = logging.getLogger("app")


def _optional_import(module: str):
    """Import ``module`` if it exists; a *missing* module returns None, but errors inside it still raise."""
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if exc.name and (module == exc.name or module.startswith(exc.name + ".")):
            return None
        raise


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    if settings.seed_demo:
        from app.seed import refresh_seeded_grant, seed_demo

        with SessionLocal() as db:
            if (db.scalar(select(func.count()).select_from(User)) or 0) == 0:
                log.info("Seeding demo data")
                seed_demo(db)
            elif refresh_seeded_grant(db) is not None:
                log.info("Refreshed expired demo grant Ravi -> Dr. Meera")
    pipeline = _optional_import("app.ingestion.pipeline")  # Task 2
    if pipeline is not None and hasattr(pipeline, "resume_pending"):
        pipeline.resume_pending()
    yield


app = FastAPI(title="IDH Vault (demo)", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
def health():
    return {"status": "ok"}


for _mod in ("auth", "patients", "consent", "admin", "system", "documents", "doctor"):
    _router_mod = _optional_import(f"app.routers.{_mod}")  # documents (Task 2) / doctor (Task 3) may be absent
    if _router_mod is not None:
        app.include_router(_router_mod.router, prefix="/api")


# ---- SPA hosting (registered last so /api routes win) ------------------------
_DIST = Path(settings.frontend_dist)

if (_DIST / "index.html").is_file():
    _DIST_RESOLVED = _DIST.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(404, detail="Not Found")
        if full_path:
            candidate = (_DIST_RESOLVED / full_path).resolve()
            if candidate.is_file() and _DIST_RESOLVED in candidate.parents:
                return FileResponse(candidate)
        return FileResponse(_DIST_RESOLVED / "index.html")
