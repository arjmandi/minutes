# minutes

**Self-hosted live transcription + translation for any browser audio.** Open a Google Meet or
Microsoft Teams call — or a webinar, a recorded video, a podcast — and minutes turns the audio into
a written transcript as it plays, optionally translating every line into another language in real
time, and streams it into a clean web app you can read, search, rename, share, and export. It can
also capture **your own microphone** as a separate track, so your side of a call is transcribed too
— no second bot account, no extra participant.

Everything runs on infrastructure **you** operate. The only external processors are the
speech-to-text and translation APIs; capture happens in *your* browser, not on a server-side browser
farm. Multi-user, EU-friendly, GDPR-minded.

> Live at **[gettheminutes.com](https://gettheminutes.com)** · full docs at
> **[gettheminutes.com/docs](https://gettheminutes.com/docs)** (the same docs that live in
> [`docs-site/`](docs-site/) in this repo).

## Features

- **Live transcription** of any tab's audio, streamed as it happens (English, German, Persian —
  detected per phrase; Persian rendered RTL-correct).
- **Dual-source capture** — capture the **tab's audio** ("Online stream") and **your microphone**
  ("Host mic") as **two separate tracks**, each with its own transcript, translation, and
  timestamps. Mic is **off by default**; toggle it on per capture.
- **Mic-only capture** — on a blank/non-audio tab, capture just your microphone (dictation, an
  in-person talk, voice notes).
- **Instant translation** of each finalized line into one output language per meeting (via an LLM),
  plus on-demand "translate this line".
- **Silence-suspend** — an optional cost saver that pauses mic streaming during sustained silence so
  you aren't billed for it (timestamps still track real time).
- **Audio upload** — transcribe an existing recording instead of a live call.
- **Public share links** — read-only, via an opaque token you can rotate or disable; two-column view
  when a meeting has both sources.
- **Export** — `txt` / `md` / `json`; transcript, translation, or both; by source or combined.
- **Mobile-ready web app** (capture itself runs on desktop Chrome).
- **Per-user keys + EU/US data residency** — each user brings their own Soniox + Anthropic keys and
  picks their Soniox region; nothing is centrally billed.
- **GDPR controls** — optional consent gate, configurable retention purge, and a full erasure path.

## How it works

```text
Any browser tab audio  +  (optionally) your microphone
        │   Chrome capture extension streams 16 kHz PCM over a secure WebSocket
        ▼
   minutes backend  ──►  Soniox            (speech-to-text: en / de / fa)
        │            ──►  Anthropic/Claude  (optional translation)
        ▼
   Live transcript in the web app  (searchable · translatable · exportable · shareable)
```

Only **Soniox** (audio) and **Anthropic** (text) ever see your data — and only with each user's own
key. Postgres (durable transcripts), Redis/Valkey (live fan-out + admission control + control
plane), and S3-compatible object storage (archived audio) all stay on the box you run. Host in an EU
region and select Soniox's **EU** region for end-to-end EU residency.

## Repository layout

| Path | Role |
|---|---|
| `app/` | FastAPI backend — config, db, session pipeline, admission, auth, api, jobs |
| `app/web/` | The web app (single-page client served by the backend) |
| `capture/extension/` | The MV3 Chrome capture extension (tab + mic) |
| `capture/driver/` | Legacy/optional Playwright bot-join driver (not needed for normal capture) |
| `docs-site/` | The documentation (Docusaurus) — published at `/docs` |
| `deploy/single-box/` | One-VPS Docker Compose stack (Caddy + app + Postgres + Valkey + MinIO) |
| `scripts/` | Dev tools — e.g. `ingest_wav.py` streams a WAV through the pipeline end-to-end |
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

Probes: `GET /healthz` (liveness), `GET /readyz` (db + redis). Web app at `/` (and `/app`); REST +
live-WebSocket API under `/api/*`; capture ingest is the `wss://…/ingest` WebSocket.

## Deploy (single box)

Everything runs on one VPS via Docker Compose. A real **domain** is required — the extension opens a
secure `wss://` WebSocket, which needs TLS (Caddy auto-provisions Let's Encrypt), which needs a
public domain with an `A` record at the VPS. Ports **80 and 443 must be public**.

```bash
git clone <this-repo> && cd minutes/deploy/single-box
cp .env.example .env
# fill in .env: DOMAIN, secrets, datastore passwords, and (optional) a fallback MINUTES_SONIOX_API_KEY
docker compose up -d --build              # a one-shot `migrate` runs alembic upgrade head first
```

Update later with `git pull && docker compose up -d --build`. Full walkthrough (sizing, boot order,
verifying, backups): **[`deploy/single-box/`](deploy/single-box/)** and
**[docs → Deploy](https://gettheminutes.com/docs/admin/deploy)**.

### Create users

There is **no public signup** — the admin creates each account from inside the backend container.
The **first user created is automatically an admin**:

```bash
docker compose exec -T backend python -m app.admin create-user --email you@org.com --admin
docker compose exec -T backend python -m app.admin set-password  --email you@org.com
docker compose exec -T backend python -m app.admin list-users
docker compose exec -T backend python -m app.admin delete-user   --email old@org.com
```

Each user then signs in to the web app and adds **their own** Soniox + Anthropic keys (and Soniox
region) in Settings — these unlock transcription and translation for that user.

## Install the capture extension

The extension is unpacked (not yet on the Chrome Web Store):

1. `chrome://extensions` → enable **Developer mode** → **Load unpacked** →
   select [`capture/extension/`](capture/extension/).
2. Click the minutes toolbar icon → **gear (Settings)** → set your **Server URL**. **This is your
   deployed domain** — e.g. `https://minutes.your-company.com` (the same URL as the web app). Hit
   **Test** to confirm it's reachable, then sign in from the popup with your email + password.
3. *(Optional)* In the same Settings page, allow the **microphone** and pick your input device, run
   the level test, and set echo-cancellation / silence-suspend — for capturing your own voice.

## Capture

1. Open the tab you want — a Meet/Teams call you're in, or any tab playing audio.
2. Click the minutes toolbar icon → **Start recording**. To also capture your own voice, flip
   **"Also capture my microphone"** first (it's off by default).
3. On a blank tab with the mic enabled, **Start recording (mic only)** captures just your
   microphone.
4. Watch the live transcript — and, if you set an Anthropic key, the translation — appear in the web
   app. When a meeting has both sources, switch between **Online stream** and **Host mic** in the
   meeting header.

The toolbar icon shows what's live (off / Online stream / Host mic / both). Full feature tour:
**[docs → Using the web app](https://gettheminutes.com/docs/users/web-app)**.

## License

[MIT](LICENSE).
