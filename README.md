# Integrated Digital Health Vault (IDH Vault) - mock demo

A working mock of a patient-owned health vault. Patients upload messy records (handwritten prescriptions, lab PDFs, discharge summaries, spreadsheets, bills, videos); the app digitises them (OCR + Claude structured extraction, with an offline fallback), the patient reviews and commits them to a structured timeline, grants time-boxed access to a doctor via OTP, and the doctor asks natural-language questions answered with citations back to source documents. Every access is written to a hash-chained audit log.

> **DISCLAIMER: all data is synthetic. Do not upload real personal health information (PHI).** Consent, audit and security features are illustrative demos of the concepts, not a certified or compliant (HIPAA/DPDP/ABDM) implementation.

## Architecture

```
 Browser (React + Vite + TS, Tailwind, recharts)
        |  JSON over /api  (JWT bearer)
        v
 FastAPI  (backend/app)
   |-- auth / consent (OTP grants) / audit chain
   |-- ingestion: extractors (pdf, docx, xlsx, image OCR, video frames)
   |        -> Claude structured extraction (or offline regex fallback)
   |        -> patient review -> commit to records
   |-- doctor Q&A: Claude tool-runner over record tools (or offline router)
   v
 SQLAlchemy -> SQLite (local) / PostgreSQL (Render)
 Uploaded files stored in the database (BYTEA); Tesseract + ffmpeg installed in the image
```

In production the FastAPI app also serves the built frontend (`FRONTEND_DIST`).

## Local development

Backend (Python 3.12):

```bash
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt    # Linux/macOS: .venv/bin/pip; runtime only: requirements.txt
.venv/Scripts/uvicorn app.main:app --reload          # http://localhost:8000/api/health
.venv/Scripts/python -m pytest -q                    # backend tests (pytest + httpx come from requirements-dev.txt)
```

Frontend (Node 20; if several Node versions are installed, put Node 20 first on `PATH` rather than switching globally, e.g. `export PATH="/c/Users/<you>/AppData/Roaming/nvm/v20.19.5:$PATH"`):

```bash
cd frontend
npm install
npm run dev                                          # http://localhost:5173
```

Optional: install Tesseract and ffmpeg for image OCR and video samples. If missing, the app reports it in `/api/system/info` and keeps running. Without an AI key (`ANTHROPIC_API_KEY`, `XAI_API_KEY` or `GROQ_API_KEY`) the app runs in offline mode (regex extraction and rule-based answers).

Regenerate samples: `pip install pillow reportlab python-docx openpyxl` then `python samples/make_samples.py`.

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | SQLAlchemy URL; both `postgres://` and `postgresql://` are normalised to `postgresql+psycopg://` | local SQLite file |
| `SECRET_KEY` | JWT signing key (Render generates one) | dev value, change in prod |
| `ANTHROPIC_API_KEY` | Enables Claude extraction and Q&A; unset = offline mode | unset |
| `AI_MODEL` | Claude model id | `claude-opus-5-5` |
| `XAI_API_KEY` | Enables Grok (xAI) extraction and Q&A when `ANTHROPIC_API_KEY` is unset (`GROK_API_KEY` also accepted) | unset |
| `GROK_MODEL` | Grok model id | `grok-4.7` |
| `XAI_BASE_URL` | xAI OpenAI-compatible endpoint | `https://api.x.ai/v1` |
| `GROQ_API_KEY` | Enables Groq Cloud (`gsk_...` keys) when neither Claude nor Grok key is set | unset |
| `GROQ_MODEL` | Groq text model for text documents and doctor Q&A (tool calling) | `openai/gpt-oss-120b` |
| `GROQ_VISION_MODEL` | Groq vision model for scans/photos/video frames (max 3 images per document) | `qwen/qwen3.8-27b` |
| `GROQ_BASE_URL` | Groq OpenAI-compatible endpoint | `https://api.groq.com/openai/v1` |
| `SEED_DEMO` | Seed demo users and history when the DB is empty | `true` |
| `AUTO_COMMIT` | Skip patient review and commit extractions automatically | `false` |
| `MAX_UPLOAD_MB` | Per-file upload limit (the frontend reads it from `/api/system/info`; `render.yaml` sets `5`) | `20` |
| `INGEST_SYNC` | Process uploads inline in the request instead of the background pool (debugging/tests) | `false` |
| `BCRYPT_ROUNDS` | bcrypt cost factor for password hashes | `12` |
| `TOKEN_TTL_MINUTES` | Lifetime of a login token (JWT) | `720` (12 hours) |
| `FRONTEND_DIST` | Path of the built frontend served by FastAPI | `../frontend/dist` (relative to `backend/`); `/app/frontend/dist` in Docker |

## Deploy to Render

Building the Docker image locally (Docker Desktop) is optional; Render builds it for you.

1. Push this repository to GitHub.
2. In Render: **New > Blueprint**, select the repo. `render.yaml` creates the `idh-vault` web service (Docker, free plan, Singapore) and the `idhv-db` Postgres database.
3. When prompted, set **one** AI key: `ANTHROPIC_API_KEY` (Claude), `XAI_API_KEY` (Grok, xAI) or `GROQ_API_KEY` (Groq, `gsk_...`). If several are set, the first in that order wins; leave all blank for offline mode.
4. Wait for the build; the health check is `/api/health`. Open the service URL.

Free-plan notes: the service sleeps when idle (first request is slow). Uploaded files are stored in Postgres (BYTEA), so they survive restarts and redeploys, but the free 1 GB Postgres database expires 30 days after creation (Render deletes it unless upgraded). Each upload request takes at most 7 files.

### Before sharing the URL

The demo is public: anyone with the link can register and spend your AI credits on extraction and Q&A. Before sharing it, set a monthly **spend limit** on the account that owns the key (Anthropic Console > Settings > Limits, or the xAI / Groq console's billing limits), or leave the keys unset to run in offline mode. `render.yaml` already caps uploads at 5 MB per file and blocks external database connections (`ipAllowList: []`).

## Demo script (about 5 minutes)

Demo accounts (password `demo1234`): `ananya@demo.in` (patient), `ravi@demo.in`, `meera@demo.in` (doctor), `admin@demo.in`.

1. **Upload**: log in as Ananya; upload the files in `samples/` (prescription image, scanned PDF, lab report, discharge summary, xlsx, pharmacy bill, video). Then drag `samples/README.md` onto the drop zone: the unsupported file is rejected without blocking anything else.
2. **Review**: open the digitised documents, check extracted labs, medicines, visits and fees against the source, fix anything, commit. The timeline and HbA1c trend update.
3. **Grant**: In Access Control, choose a duration and permission, then generate a code. Copy the one-time code (OTP).
4. **Redeem**: log out, log in as `meera@demo.in`, enter Ananya's DHID `DH-8923-4102-9981` and the OTP.
5. **Ask**: ask "What is her HbA1c trend?", "Which diabetes drugs has she been on and why was one switched?", "Any hypoglycaemia?". Answers cite source documents/pages. Then, as Ananya, revoke access and watch the doctor get "Access expired" on the next question; check the audit log.

## Limitations

- Synthetic demo only; no real PHI. Not a medical device and gives no medical advice.
- OCR and handwriting extraction are best-effort; always review before committing.
- Uploaded files live in the database (BYTEA); the free Render Postgres instance (1 GB) expires after 30 days, taking all data with it.
- Security, consent and audit features illustrate concepts and have not been independently audited.
