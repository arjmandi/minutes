---
sidebar_position: 3
title: "Configuration reference"
---

# Configuration reference

Everything about a minutes instance is configured through environment variables in a single `.env` file that lives next to your `docker-compose.yml` (in `deploy/single-box`). You create it once during [Deploy](/admin/deploy) by copying the template:

```bash
cp .env.example .env
```

This page is the reference for that file: every variable the operator sets, its exact name and default, and how the bring-your-own-key model works. The defaults shown here come straight from the application's settings (`app/config.py`) and the deployment template (`deploy/single-box/.env.example`).

:::note Variable naming
Application settings are read with the prefix `MINUTES_` (so the `auth_secret` setting is the `MINUTES_AUTH_SECRET` env var). A few infrastructure variables — `DOMAIN`, `POSTGRES_PASSWORD`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `LANDING_DIR` — are consumed by Docker Compose / the bootstrap scripts, not the app, so they have **no** prefix. The app also ignores any unknown variable, so an extra key in `.env` is harmless.
:::

:::tip Apply changes
After editing `.env`, re-run `docker compose up -d` from `deploy/single-box`. Compose recreates only the containers whose environment changed. There's no separate reload step.
:::

## Required variables

These must be set before the stack will start. In the deployment, the backend runs with `MINUTES_APP_ENV=prod`, which **fails closed**: it refuses to boot on a weak or default secret (see the note under `MINUTES_AUTH_SECRET`).

| Variable | Purpose |
| --- | --- |
| `DOMAIN` | Your public hostname, e.g. `meet.example.com`. A DNS `A` record must point at the VPS. Caddy uses it to fetch a Let's Encrypt certificate and to serve `https://` + `wss://`. |
| `MINUTES_AUTH_SECRET` | Signing secret for the capability tokens the capture pipeline uses. Must be a strong, non-default value of **at least 32 bytes**. |
| `MINUTES_SECRET_KEY` | AES-256-GCM key that encrypts each user's stored provider API keys at rest. Must also be **at least 32 bytes** and non-default. |
| `POSTGRES_PASSWORD` | Password for the bundled Postgres database. Any strong value. |
| `MINIO_ROOT_PASSWORD` | Admin password for the bundled MinIO object store (**≥ 8 chars**). Used only by the one-shot bootstrap that creates the bucket and the app's scoped service account — never by the app itself. |
| `MINUTES_S3_SECRET_KEY` | Secret for the **app-scoped** MinIO service account the backend uses for audio storage (**≥ 8 chars**). Least-privilege: limited to the `minutes-audio` bucket, not the root credential. |

Generate the two secrets with:

```bash
openssl rand -hex 32   # one for MINUTES_AUTH_SECRET, one for MINUTES_SECRET_KEY
```

:::danger The prod secret guard is not optional
On any non-development environment, the backend validates `MINUTES_AUTH_SECRET` and `MINUTES_SECRET_KEY` at startup and **raises an error** if either is the built-in default or shorter than 32 bytes. This is deliberate: it prevents shipping a publicly known signing key. If the backend container won't start and the logs mention a "strong (>=32 byte) non-default secret", this is why.
:::

The MinIO access-key *names* have safe defaults in the shipped `.env.example` and rarely need changing:

| Variable | Default | Notes |
| --- | --- | --- |
| `MINIO_ROOT_USER` | `minutes-admin` | MinIO admin username (bootstrap only). |
| `MINUTES_S3_ACCESS_KEY` | `minutes-app` | App-scoped MinIO service-account name (**≥ 3 chars**). The backend uses this, not root. (The bare application default in `app/config.py` is `minutes`; the single-box template ships `minutes-app`.) |

## STT and translation keys

minutes talks to exactly two external services, and **only** these two ever receive your data: **Soniox** for speech-to-text, and **Anthropic (Claude)** for translation. Everything else stays on your box.

Both keys are **per-user**: each user pastes their own under **Settings → API keys** in the web app, stored encrypted with `MINUTES_SECRET_KEY` (AES-256-GCM). The server-wide variables below are **optional fallbacks**.

| Variable | Default | Scope |
| --- | --- | --- |
| `MINUTES_SONIOX_API_KEY` | *(empty)* | **Server-wide fallback.** Used for live capture **only when the capturing user has not set their own Soniox key**. Leave empty to require per-user keys. |
| `MINUTES_SONIOX_REGION` | `us` | Data-residency region (`us` \| `eu`) for that **fallback** key. Per-user keys carry their own region (see below). |
| `MINUTES_ANTHROPIC_API_KEY` | *(empty)* | **Server-wide, optional.** Usually left empty — translation uses each user's own key. |

### The bring-your-own-key model

This is the most important thing to understand about minutes' key handling. There are three workflows, and they draw on different keys:

| Workflow | Speech-to-text uses… | Translation uses… |
| --- | --- | --- |
| **Live capture** (extension streams a tab) | the **owner's own** Soniox key (falls back to `MINUTES_SONIOX_API_KEY`) | the **owner's own** Anthropic key |
| **Audio upload** (user uploads a recording) | the **uploader's own** Soniox key | the **uploader's own** Anthropic key |
| **On-demand "translate this line"** | — | the **user's own** Anthropic key |

In short:

- **Soniox (speech-to-text) is bring-your-own.** Each user sets their own Soniox key in Settings; it transcribes both their **live captures and their uploads**. A server-wide `MINUTES_SONIOX_API_KEY`, if set, is used **only as a fallback** for live capture when a user hasn't added their own — convenient for a quick demo or a single-tenant box. Leave it empty to require every user to bring a key.
- **Translation is always bring-your-own.** Live, upload, and on-demand translation all use each **user's own** Anthropic key from Settings. If a user has no Anthropic key, translation is simply off for them — STT is unaffected.

Get a Soniox key at [soniox.com](https://soniox.com) and an Anthropic key at [console.anthropic.com](https://console.anthropic.com). For more on the privacy boundary, see [Limitations](/reference/limitations).

:::note Dual-source capture opens two Soniox connections
A live capture can include two independent sources kept fully separate: the **Online stream** (the browser tab's audio) and the **Host mic** (the capturing user's own microphone, **off by default**, toggled in the extension). When both are on, the capture opens **two concurrent realtime Soniox connections on the capturing user's own key** (or the server fallback). If that Soniox plan caps concurrency, the second connection is rejected and only that source shows an error — the other keeps recording. This does not change the key model above; it just means a user who runs both sources needs Soniox concurrency for two streams. See [Limitations](/reference/limitations) and [Troubleshooting](/reference/troubleshooting).
:::

### Data residency (Soniox region)

Soniox offers **EU data-residency endpoints**. Each user picks the region of **their** key under **Settings → API keys → Soniox region**, and that choice routes both their live capture and their uploads:

| Region | Endpoints | Where audio is processed |
| --- | --- | --- |
| `us` (default) | `api.soniox.com` / `stt-rt.soniox.com` | United States |
| `eu` | `api.eu.soniox.com` / `stt-rt.eu.soniox.com` | European Union (end to end) |

The region must match the Soniox **project** the key was created in — an EU-project key only works on the EU endpoints, and vice-versa. `MINUTES_SONIOX_REGION` sets the region for the **server-wide fallback key only**; per-user keys store their own. (Anthropic is not region-selectable here.)

:::tip For a fully EU-resident instance
Have each user create their key in an **EU** Soniox project and select **EU** in Settings. If you set a server fallback key, make it an EU key and set `MINUTES_SONIOX_REGION=eu` too, so even users without their own key stay in-region.
:::

### Capture settings live in the extension, not `.env`

A few capture behaviors are **per-user, set in the Chrome extension's options page** — they are *not* environment variables and there is nothing to configure here for them:

- **Host mic** — off by default; toggled per capture in the extension popup. The options page holds the **mic device**, a **live level test**, and an **echo-cancellation** toggle (**default off**: with the tab playing through speakers, AEC can clip the host's voice; leave it off on headphones too).
- **Silence-suspend** — a cost-saver in the extension settings, **default on**. During sustained silence the Host mic stops streaming to Soniox to cut billed seconds, and resumes on speech. Segment timestamps still track real meeting time — dropped silence is added back server-side — so the transcript timeline stays correct. If quiet speech is being dropped, a user can turn silence-suspend off in the extension settings (see [Troubleshooting](/reference/troubleshooting)).

These affect what the extension sends to Soniox on the **user's own** key; they have no server-side variable.

### Translation targets

| Variable | Default | Notes |
| --- | --- | --- |
| `MINUTES_TRANSLATION_TARGETS` | `["en"]` (template sets `["de"]`) | JSON array of default target languages for live capture. |

This is the default output language(s) for live meetings. It's a JSON array, e.g. `["de"]` or `["en","fa"]`. The application's built-in default is `["en"]`, but the shipped `.env.example` (and `docker-compose.yml`) sets it to `["de"]`. A target equal to the detected source language is **skipped** — so the bare app default of `["en"]` produces no output for English speech. Pick targets that differ from what's being spoken. Supported languages are English (`en`), German (`de`), and Persian (`fa`, right-to-left). Translation only actually runs when the user has an Anthropic key.

## Behavior and limits

These tune capacity, retention, and the GDPR consent gate. All are optional and have working defaults.

| Variable | Default | What it controls |
| --- | --- | --- |
| `MINUTES_REQUIRE_CONSENT` | `false` (template sets `true`) | When `true`, ingest **refuses** any meeting that doesn't have recorded consent. |
| `MINUTES_RETENTION_DAYS` | `90` | Meetings and their stored audio older than this are purged by the daily scheduler. Floor of `1` — a zero/negative value can never wipe the whole dataset. |
| `MINUTES_MAX_CONCURRENT_CALLS` | `5` | Maximum simultaneous **live** capture sessions across the whole box (admission cap). |
| `MINUTES_UPLOAD_MAX_BYTES` | `314572800` (~300 MB) | Size ceiling for a single uploaded audio/video file. |
| `MINUTES_UPLOAD_MAX_CONCURRENT` | `2` | Maximum file-transcription jobs processed at once by the scheduler. |
| `LANDING_DIR` | `/opt/minutes-site` | Host path of an optional marketing landing site served at `/`. If the directory is missing, Caddy falls back to serving the app at `/`. |

:::note Two different defaults for consent
The application's built-in default for `MINUTES_REQUIRE_CONSENT` is `false`, but the shipped `.env.example` sets it to `true` — the GDPR-safe choice. Only set it to `false` if you have an alternative lawful basis for recording and understand that obligation. See [Limitations](/reference/limitations).
:::

:::note Uploads need the user's own Soniox key
Audio upload (`audio/*` and `video/*` containers, up to `MINUTES_UPLOAD_MAX_BYTES`) is processed out-of-band by the scheduler, roughly every 20 seconds, and transcribes using the **uploader's own** Soniox key — not the server key.
:::

## Domain vs. IP, and TLS

A real **public domain is required** for any usable deployment, not just nice-to-have:

- The capture extension opens a **secure WebSocket** (`wss://`) from a secure browser context. Browsers refuse to open an insecure WebSocket from an HTTPS page.
- `wss://` needs TLS, and Caddy gets TLS from Let's Encrypt, which can only issue a certificate for a real **domain** — never a bare IP.

So set `DOMAIN` to a hostname whose DNS `A` record points at the VPS, and make sure **inbound TCP 80 and 443 are reachable from the internet** (Let's Encrypt validates over them; Caddy serves HTTPS/`wss` there). No other port should be public.

A bare IP or `http://localhost` (plain `ws://`, no TLS) works **only** for local development of the app itself — never for the extension against a server. Caddy auto-provisions and renews the certificate for `$DOMAIN`; there's nothing to configure beyond pointing DNS and opening the ports. See [Deploy](/admin/deploy) for the full DNS/firewall walkthrough.

## Backups

:::warning The default deployment ships no backups
The single-box stack runs Postgres and MinIO on a single drive with **no replication, no failover, and no automated backups**. A disk or host failure can lose data — an accepted trade-off for this low-cost showcase topology. If your data matters at all, add your own off-box backups.
:::

A nightly Postgres dump via host `cron` is the minimum. Add an entry like this on the host (run from your `deploy/single-box` directory, or use absolute paths):

```bash
# /etc/cron.d/minutes-backup — nightly Postgres dump, off-box
0 3 * * *  root  cd /path/to/minutes/deploy/single-box && docker compose exec -T postgres pg_dump -U minutes minutes | gzip > /backups/minutes-$(date +\%F).sql.gz
```

The `-T` flag is required so `docker compose exec` doesn't try to allocate a TTY under cron. Also mirror the audio bucket off-box (configure an external S3 or `rsync` target) if archived audio matters to you. For the bigger picture on what the default deployment deliberately does *not* do, see [Limitations](/reference/limitations).

## Updating

To update to a new build, pull and rebuild from `deploy/single-box`:

```bash
git pull && docker compose up -d --build
```

The one-shot `migrate` service re-runs `alembic upgrade head` before the backend starts, so schema upgrades are applied automatically. Your `.env` is untouched — but watch the release notes for any new variables you may want to set.

## Tuning concurrency

The pipeline is I/O-bound — STT runs remotely at Soniox and translation remotely at Claude — so on the 2 vCPU / 8 GB box, concurrency is gated mainly by RAM and per-call audio work, not CPU.

- **Live sessions:** if the box struggles under real load, lower `MINUTES_MAX_CONCURRENT_CALLS` (e.g. to `2`–`3`) in `.env`, then `docker compose up -d`. The cap is enforced in Valkey/Redis and shared across the whole box.
- **File uploads:** `MINUTES_UPLOAD_MAX_CONCURRENT` (default `2`) bounds how many uploaded files transcribe at once; lower it to ease memory pressure.

Per-service memory limits (Postgres 1.5 GB, MinIO 1 GB, backend 1 GB, and so on) are set in `docker-compose.yml`, not in `.env`.

:::tip Scaling past one box
The admission cap is already Redis-shared, so running more than one backend replica is safe — but it needs a Caddy upstream change to load-balance across containers. Beyond that, outgrowing the single box means moving Postgres, Redis, and object storage to managed/replicated services; the application code is unchanged. See [Limitations](/reference/limitations).
:::
