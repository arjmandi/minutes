# minutes

Self-hosted meeting note-taker: it joins your Teams / Google Meet calls (via a local capture client
+ an invited bot account), transcribes them **live** with [Soniox](https://soniox.com) (English,
German, Persian), translates each line with an **LLM**, archives the audio to **S3-compatible**
object storage, and streams a live, searchable, translated transcript to a web app — all on
infrastructure you run.

No third-party meeting-bot provider: the only external processors are the speech-to-text and
translation APIs. Capture runs on a client device (a local Chrome + extension), not a server-side
browser farm.

## Features

- **Live transcription** streamed during the meeting (en / de / fa, detected per phrase).
- **Instant translation** of each finalized line into the languages you choose (Persian RTL-correct).
- **Custom vocabulary** recognized in speech and carried into translations.
- **Teams *and* Google Meet**, including meetings hosted by other organizations.
- **Runtime control plane** — change translation targets / vocabulary mid-meeting.
- **Consent gate, configurable retention, and a full erasure path** (GDPR-minded).
- **Capacity-managed, self-healing sessions** — shared admission control + reconnect without losing audio.

## Architecture

A FastAPI backend runs the pipeline: authenticated capture ingest (WebSocket) → Soniox STT →
Postgres (durable transcripts/translations) → S3-compatible audio archive → live fan-out to the web
viewer over Redis Streams. Redis also backs a shared admission cap and the control plane. The
capture client is an MV3 Chrome extension that captures tab audio and streams 16 kHz PCM to the
backend; an optional Playwright driver automates the bot join.

| Path | Role |
|---|---|
| `app/` | FastAPI backend — config, db, session pipeline, admission, auth, api, jobs |
| `capture/` | Browser capture extension + bot-join driver |
| `deploy/single-box/` | One-VPS Docker Compose stack (Caddy + app + Postgres + Valkey + MinIO) |
| `scripts/` | Dev tools — e.g. `ingest_wav.py`, stream a WAV through the pipeline for an end-to-end test |
| `migrations/` | Alembic (async) |

## Dev quickstart

```bash
uv sync                                   # Python 3.12 venv + deps
cp .env.example .env
docker compose up -d postgres redis minio # local datastores
uv run alembic upgrade head               # apply schema
uv run uvicorn app.main:app --reload      # serve on :8000
uv run pytest                             # tests (need postgres + redis up)
```

Probes: `GET /healthz` (liveness), `GET /readyz` (db + redis). Marketing landing at `/`, the
transcript viewer at `/app`, capture ingest at `wss://…/ingest`.

### End-to-end test (no browser needed)

```bash
# 16 kHz mono WAV in, transcript out — exercises the whole deployed pipeline:
uv run python scripts/ingest_wav.py wss://<host>/ingest sample16k.wav --meeting demo-001
```

Mint a capability token with `python -m app.mint_token --meetings '*' --ttl 3600` (run inside the
backend container so it uses the deployed secret).

## Deploy

Single-box (everything on one VPS) — see **[`deploy/single-box/`](deploy/single-box/)**. For
production / HA, move Postgres, Redis, and object storage to managed or replicated services across
multiple nodes; the application code is unchanged.

## Capture client & releases

The Chrome extension and bot-join driver live in **[`capture/`](capture/)**. Packaged extension
builds are published under this repo's GitHub Releases.

## License

[MIT](LICENSE).
