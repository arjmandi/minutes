# minutes

Meeting note-taker backend — captures Teams/Google Meet audio (via a local capture client +
invited bot account), transcribes with **Soniox** (en/de/fa), translates downstream with an
**LLM**, archives audio to S3-compatible storage (**S3-compatible object storage**), and streams transcripts live
to a web app. Backend runs on **a cloud host**.

**Architecture:** [`docs/meeting-notetaker-architecture-spec-v3.md`](docs/meeting-notetaker-architecture-spec-v3.md)
(definitive). Decision trail: `docs/architecture-review-v1.md`, `docs/architecture-validation-v2.md`,
`docs/architecture-external-capture.md`.

## Dev quickstart

```bash
uv sync                                   # provision Python 3.12 venv + deps
cp .env.example .env
docker compose up -d postgres redis       # + minio (local S3 stand-in) if needed
uv run alembic upgrade head               # apply schema
uv run uvicorn app.main:app --reload      # serve on :8000
uv run pytest                             # tests (admission registry needs redis up)
```

Probes: `GET /healthz` (liveness), `GET /readyz` (db + redis). Capture ingest: `ws://…/ingest`.

## Layout

| Path | Role |
|---|---|
| `app/config.py` | Settings (`MINUTES_*` env / `.env`) |
| `app/db/` | Async engine, session, data model (spec §10) |
| `app/session/` | `MeetingSession` seam + `ClientCaptureAdapter` |
| `app/admission/` | Redis-backed admission cap (Lua slots + fencing, spec §13) |
| `app/api/` | Health probes + capture ingest WebSocket |
| `app/main.py` | App factory + lifespan |
| `migrations/` | Alembic (async) |

## Build status

Build-order step 1 (core skeleton) in progress. See spec §20 for the sequence.
